package com.bank.qa.sampleapp;

import java.math.BigDecimal;
import java.math.BigInteger;
import java.math.RoundingMode;
import java.time.Instant;
import java.time.LocalDate;
import java.time.ZoneId;
import java.time.format.DateTimeFormatter;
import java.util.ArrayList;
import java.util.Collection;
import java.util.Comparator;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Optional;
import java.util.UUID;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.ThreadLocalRandom;
import java.util.concurrent.atomic.AtomicInteger;
import java.util.regex.Pattern;
import java.util.stream.Collectors;

/**
 * In-memory domain model of the SAMPLE banking application (the "system under test").
 *
 * <p>This is NOT part of the test framework - it exists so the framework has a realistic,
 * self-contained target: authentication with MFA, RBAC, maker-checker payment approval,
 * card authorization with 3-D Secure, an asynchronous settlement queue and settlement batches.
 *
 * <p>Business rules encoded here are the "user stories" the tests are derived from
 * (see docs/USER_STORIES.md), e.g. payment amount 0.01..50,000.00, approval above 10,000.00.
 */
public final class BankStore {

    public static final ZoneId BANK_ZONE = ZoneId.of("Europe/London");

    // ---- User story PAY-101 acceptance criteria (single source of truth for the SUT) ----
    public static final BigDecimal MIN_PAYMENT = new BigDecimal("0.01");
    public static final BigDecimal MAX_PAYMENT = new BigDecimal("50000.00");
    public static final BigDecimal APPROVAL_THRESHOLD = new BigDecimal("10000.00");
    public static final int REFERENCE_MAX = 18;
    public static final int MAX_EXECUTION_DAYS_AHEAD = 30;
    private static final Pattern AMOUNT_FORMAT = Pattern.compile("^\\d{1,9}(\\.\\d{1,2})?$");
    private static final Pattern REFERENCE_FORMAT = Pattern.compile("^[A-Za-z0-9 \\-]+$");

    // ---- User story CARD-201 ----
    public static final BigDecimal CARD_MAX_AMOUNT = new BigDecimal("10000.00");
    public static final BigDecimal CARD_AVAILABLE_LIMIT = new BigDecimal("5000.00");
    public static final BigDecimal THREE_DS_THRESHOLD = new BigDecimal("250.00");
    public static final String THREE_DS_OTP = "123456";

    public static final int SESSION_IDLE_TIMEOUT_SECONDS = 30 * 60;

    public record User(String username, String displayName, String role, String totpSecret) {}

    public record Account(String id, String name, String currency, BigDecimal balance, String maskedNumber) {}

    public record Beneficiary(String id, String name, String iban, String bank) {}

    public static final class Payment {
        public String id;
        public String reference;
        public String debtorAccountId;
        public String beneficiaryId;
        public String beneficiaryName;
        public BigDecimal amount;
        public String currency;
        public String executionDate;
        public volatile String status;
        public String createdBy;
        public String approvedBy;
        public Instant createdAt;
        public volatile Instant updatedAt;
        public volatile String settlementBatchId;
        public final List<Map<String, String>> statusHistory = new ArrayList<>();
        volatile Instant readyAt;
    }

    public static final class SettlementBatch {
        public String batchId;
        public String businessDate;
        public volatile String status; // OPEN, CLOSING, CLOSED
        public final List<String> paymentIds = new ArrayList<>();
        volatile Instant readyAt;
    }

    public static final class CardAuthorization {
        public String authId;
        public String maskedPan;
        public BigDecimal amount;
        public String currency;
        public String merchant;
        public String mcc;
        public volatile String decision;
        public volatile String responseCode;
        public volatile String message;
        public Instant createdAt;
    }

    final Map<String, User> users = new LinkedHashMap<>();
    final Map<String, String> passwords = new ConcurrentHashMap<>();
    final List<Account> accounts = new ArrayList<>();
    final List<Beneficiary> beneficiaries = new ArrayList<>();
    final Map<String, Payment> payments = new ConcurrentHashMap<>();
    final Map<String, SettlementBatch> batches = new ConcurrentHashMap<>();
    final Map<String, CardAuthorization> cardAuths = new ConcurrentHashMap<>();
    /** key = username + ":" + Idempotency-Key -> [requestFingerprint, paymentId]. */
    final Map<String, String[]> idempotency = new ConcurrentHashMap<>();
    private final AtomicInteger batchSeq = new AtomicInteger();

    public BankStore(String password) {
        // TOTP seeds are NON-PRODUCTION values for the demo app only.
        addUser(new User("viewer.user", "Vera Viewer", "VIEWER", "JBSWY3DPEHPK3PXP"), password);
        addUser(new User("maker.user", "Mark Maker", "MAKER", "KRSXG5CTMVRXEZLU"), password);
        addUser(new User("checker.user", "Chloe Checker", "CHECKER", "MFRGGZDFMZTWQ2LK"), password);
        addUser(new User("admin.user", "Ada Admin", "ADMIN", "GEZDGNBVGY3TQOJQ"), password);

        accounts.add(new Account("ACC-001", "Operating Account", "GBP", new BigDecimal("250000.00"), "•••• 4821"));
        accounts.add(new Account("ACC-002", "Payroll Account", "GBP", new BigDecimal("98000.00"), "•••• 7710"));
        accounts.add(new Account("ACC-003", "EUR Treasury", "EUR", new BigDecimal("120000.00"), "•••• 3302"));

        String[][] bens = {
                {"Acme Supplies Ltd", "Barclays"}, {"Acme Logistics PLC", "HSBC UK"},
                {"Brightwater Utilities", "Lloyds"}, {"Northwind Traders", "NatWest"},
                {"Contoso Facilities", "Santander UK"}, {"Globex Insurance", "Barclays"},
                {"Initech Software", "Monzo"}, {"Umbrella Health", "Starling"},
                {"Stark Components", "HSBC UK"}, {"Wayne Office Supplies", "Lloyds"},
                {"Tyrell Robotics", "Metro Bank"}, {"Soylent Catering", "NatWest"}};
        for (int i = 0; i < bens.length; i++) {
            String account = String.format("%08d", 31926819 + i * 1111);
            beneficiaries.add(new Beneficiary("BEN-" + (100 + i), bens[i][0], gbIban("NWBK", "601613", account), bens[i][1]));
        }
    }

    private void addUser(User u, String password) {
        users.put(u.username(), u);
        passwords.put(u.username(), password);
    }

    /** ISO 13616 IBAN with valid ISO 7064 mod-97 check digits. */
    static String gbIban(String bankCode, String sortCode, String account) {
        String bban = bankCode + sortCode + account;
        String rearranged = bban + "GB00";
        StringBuilder numeric = new StringBuilder();
        for (char c : rearranged.toCharArray()) {
            numeric.append(Character.isLetter(c) ? String.valueOf(c - 'A' + 10) : String.valueOf(c));
        }
        int check = 98 - new BigInteger(numeric.toString()).mod(BigInteger.valueOf(97)).intValue();
        return "GB" + String.format("%02d", check) + bban;
    }

    public static Map<String, Object> permissionsFor(String role) {
        // Actual RBAC implemented by the app. Tests hold their OWN expected matrix (the requirement)
        // and compare - never derive expectations from the SUT itself.
        List<String> p = switch (role) {
            case "VIEWER" -> List.of("ACCOUNTS_VIEW", "PAYMENTS_VIEW", "SETTLEMENTS_VIEW");
            case "MAKER" -> List.of("ACCOUNTS_VIEW", "PAYMENTS_VIEW", "SETTLEMENTS_VIEW", "PAYMENTS_CREATE", "CARDS_AUTHORIZE");
            case "CHECKER" -> List.of("ACCOUNTS_VIEW", "PAYMENTS_VIEW", "SETTLEMENTS_VIEW", "PAYMENTS_APPROVE");
            case "ADMIN" -> List.of("ACCOUNTS_VIEW", "PAYMENTS_VIEW", "SETTLEMENTS_VIEW", "PAYMENTS_CREATE",
                    "PAYMENTS_APPROVE", "CARDS_AUTHORIZE", "SETTLEMENTS_CLOSE", "ADMIN_USERS");
            default -> List.of();
        };
        return Map.of("permissions", p);
    }

    @SuppressWarnings("unchecked")
    public static boolean hasPermission(String role, String permission) {
        return ((List<String>) permissionsFor(role).get("permissions")).contains(permission);
    }

    // ------------------------------------------------------------------ payments

    public List<Map<String, String>> validatePayment(Map<String, Object> body) {
        List<Map<String, String>> errors = new ArrayList<>();
        String amountStr = str(body.get("amount"));
        if (amountStr == null || amountStr.isBlank()) {
            errors.add(err("amount", "Amount is required"));
        } else if (!AMOUNT_FORMAT.matcher(amountStr).matches()) {
            errors.add(err("amount", "Amount must be a number with at most 2 decimal places"));
        } else {
            BigDecimal amount = new BigDecimal(amountStr);
            if (amount.compareTo(MIN_PAYMENT) < 0) errors.add(err("amount", "Amount must be at least 0.01"));
            if (amount.compareTo(MAX_PAYMENT) > 0) errors.add(err("amount", "Amount must not exceed 50,000.00"));
        }
        String currency = str(body.get("currency"));
        if (currency == null || !List.of("GBP", "EUR", "USD").contains(currency)) errors.add(err("currency", "Unsupported currency"));
        String reference = str(body.get("reference"));
        if (reference == null || reference.isBlank()) {
            errors.add(err("reference", "Reference is required"));
        } else if (reference.length() > REFERENCE_MAX) {
            errors.add(err("reference", "Reference must be 18 characters or fewer"));
        } else if (!REFERENCE_FORMAT.matcher(reference).matches()) {
            errors.add(err("reference", "Reference may contain letters, numbers, spaces and hyphens only"));
        }
        if (findAccount(str(body.get("debtorAccountId"))).isEmpty()) errors.add(err("debtorAccountId", "Unknown debtor account"));
        if (findBeneficiary(str(body.get("beneficiaryId"))).isEmpty()) errors.add(err("beneficiaryId", "Select a beneficiary"));
        String date = str(body.get("executionDate"));
        try {
            LocalDate d = LocalDate.parse(date);
            LocalDate today = LocalDate.now(BANK_ZONE);
            if (d.isBefore(today)) errors.add(err("executionDate", "Execution date cannot be in the past"));
            if (d.isAfter(today.plusDays(MAX_EXECUTION_DAYS_AHEAD))) errors.add(err("executionDate", "Execution date must be within 30 days"));
        } catch (Exception e) {
            errors.add(err("executionDate", "Execution date is required (YYYY-MM-DD)"));
        }
        return errors;
    }

    public Payment createPayment(Map<String, Object> body, String username) {
        Payment p = new Payment();
        p.id = "PAY-" + UUID.randomUUID().toString().substring(0, 8).toUpperCase();
        p.reference = str(body.get("reference"));
        p.debtorAccountId = str(body.get("debtorAccountId"));
        p.beneficiaryId = str(body.get("beneficiaryId"));
        p.beneficiaryName = findBeneficiary(p.beneficiaryId).map(Beneficiary::name).orElse("?");
        p.amount = new BigDecimal(str(body.get("amount"))).setScale(2, RoundingMode.UNNECESSARY);
        p.currency = str(body.get("currency"));
        p.executionDate = str(body.get("executionDate"));
        p.createdBy = username;
        p.createdAt = Instant.now();
        transition(p, p.amount.compareTo(APPROVAL_THRESHOLD) > 0 ? "PENDING_APPROVAL" : "PENDING_SETTLEMENT", username);
        payments.put(p.id, p);
        return p;
    }

    void transition(Payment p, String status, String actor) {
        synchronized (p) {
            p.status = status;
            p.updatedAt = Instant.now();
            p.statusHistory.add(Map.of("status", status, "at", p.updatedAt.toString(), "by", actor));
            if ("PENDING_SETTLEMENT".equals(status)) {
                // Async queue: picked up after a random delay -> tests MUST poll/wait, never sleep.
                p.readyAt = Instant.now().plusMillis(ThreadLocalRandom.current().nextLong(300, 1200));
            } else if ("PROCESSING".equals(status)) {
                p.readyAt = Instant.now().plusMillis(ThreadLocalRandom.current().nextLong(500, 1500));
            }
        }
    }

    public List<Payment> listPayments(String status) {
        return payments.values().stream()
                .filter(p -> status == null || status.isBlank() || status.equals(p.status))
                .sorted(Comparator.comparing((Payment p) -> p.createdAt).reversed())
                .collect(Collectors.toList());
    }

    /** One tick of the settlement queue worker (called by a scheduler). */
    void processQueueTick() {
        Instant now = Instant.now();
        for (Payment p : payments.values()) {
            if (p.readyAt == null || p.readyAt.isAfter(now)) continue;
            if ("PENDING_SETTLEMENT".equals(p.status)) {
                transition(p, "PROCESSING", "settlement-engine");
            } else if ("PROCESSING".equals(p.status)) {
                p.readyAt = null;
                if (p.reference.toUpperCase().contains("FAIL")) {
                    transition(p, "FAILED", "settlement-engine");
                } else {
                    SettlementBatch b = openBatch();
                    synchronized (b) {
                        b.paymentIds.add(p.id);
                    }
                    p.settlementBatchId = b.batchId;
                    transition(p, "SETTLED", "settlement-engine");
                }
            }
        }
        for (SettlementBatch b : batches.values()) {
            if ("CLOSING".equals(b.status) && b.readyAt != null && !b.readyAt.isAfter(now)) {
                b.status = "CLOSED";
            }
        }
    }

    synchronized SettlementBatch openBatch() {
        String today = LocalDate.now(BANK_ZONE).toString();
        return batches.values().stream()
                .filter(b -> "OPEN".equals(b.status) && today.equals(b.businessDate))
                .findFirst()
                .orElseGet(() -> {
                    SettlementBatch b = new SettlementBatch();
                    b.batchId = "STL-" + LocalDate.now(BANK_ZONE).format(DateTimeFormatter.BASIC_ISO_DATE)
                            + "-" + String.format("%03d", batchSeq.incrementAndGet());
                    b.businessDate = today;
                    b.status = "OPEN";
                    batches.put(b.batchId, b);
                    return b;
                });
    }

    public void closeBatch(SettlementBatch b) {
        b.status = "CLOSING";
        b.readyAt = Instant.now().plusMillis(1500);
    }

    public Collection<SettlementBatch> listBatches() {
        return batches.values().stream().sorted(Comparator.comparing((SettlementBatch b) -> b.batchId).reversed()).toList();
    }

    public BigDecimal batchTotal(SettlementBatch b) {
        synchronized (b) {
            return b.paymentIds.stream().map(payments::get).map(p -> p.amount).reduce(BigDecimal.ZERO, BigDecimal::add);
        }
    }

    // ------------------------------------------------------------------ cards

    public CardAuthorization authorizeCard(Map<String, Object> body) {
        CardAuthorization a = new CardAuthorization();
        a.authId = "AUTH-" + UUID.randomUUID().toString().replace("-", "").substring(0, 10).toUpperCase();
        a.createdAt = Instant.now();
        String pan = str(body.get("pan")).replace(" ", "");
        a.maskedPan = pan.length() >= 10 ? pan.substring(0, 6) + "*".repeat(pan.length() - 10) + pan.substring(pan.length() - 4) : "****";
        a.amount = new BigDecimal(str(body.get("amount")));
        a.currency = str(body.get("currency"));
        a.merchant = str(body.get("merchant"));
        a.mcc = str(body.get("mcc"));

        // Decision engine - simplified ISO 8583 response codes.
        if (!pan.matches("\\d{13,19}") || !luhn(pan)) {
            decide(a, "DECLINED", "14", "Invalid card number");
        } else if (isExpired(str(body.get("expiry")))) {
            decide(a, "DECLINED", "54", "Expired card");
        } else if (!str(body.get("cvv")).matches("\\d{3}")) {
            decide(a, "DECLINED", "N7", "CVV2 failure");
        } else if (pan.startsWith("400005")) {
            decide(a, "DECLINED", "05", "Do not honour");
        } else if (pan.startsWith("400051") || a.amount.compareTo(CARD_AVAILABLE_LIMIT) > 0) {
            decide(a, "DECLINED", "51", "Insufficient funds");
        } else if (a.amount.compareTo(THREE_DS_THRESHOLD) > 0) {
            decide(a, "CHALLENGE", "1A", "Strong customer authentication required");
        } else {
            decide(a, "APPROVED", "00", "Approved");
        }
        cardAuths.put(a.authId, a);
        return a;
    }

    public List<Map<String, String>> validateCard(Map<String, Object> body) {
        List<Map<String, String>> errors = new ArrayList<>();
        String amount = str(body.get("amount"));
        if (amount == null || !AMOUNT_FORMAT.matcher(amount).matches()) {
            errors.add(err("amount", "Amount must be a number with at most 2 decimal places"));
        } else {
            BigDecimal a = new BigDecimal(amount);
            if (a.compareTo(MIN_PAYMENT) < 0) errors.add(err("amount", "Amount must be at least 0.01"));
            if (a.compareTo(CARD_MAX_AMOUNT) > 0) errors.add(err("amount", "Amount must not exceed 10,000.00"));
        }
        if (str(body.get("pan")) == null) errors.add(err("pan", "Card number is required"));
        if (str(body.get("expiry")) == null) errors.add(err("expiry", "Expiry is required"));
        if (str(body.get("cvv")) == null) errors.add(err("cvv", "CVV is required"));
        return errors;
    }

    static void decide(CardAuthorization a, String decision, String code, String message) {
        a.decision = decision;
        a.responseCode = code;
        a.message = message;
    }

    static boolean isExpired(String expiry) {
        if (expiry == null || !expiry.matches("(0[1-9]|1[0-2])/\\d{2}")) return true;
        int month = Integer.parseInt(expiry.substring(0, 2));
        int year = 2000 + Integer.parseInt(expiry.substring(3));
        LocalDate endOfMonth = LocalDate.of(year, month, 1).plusMonths(1).minusDays(1);
        return endOfMonth.isBefore(LocalDate.now(BANK_ZONE));
    }

    static boolean luhn(String digits) {
        int sum = 0;
        boolean dbl = false;
        for (int i = digits.length() - 1; i >= 0; i--) {
            int d = digits.charAt(i) - '0';
            if (dbl) {
                d *= 2;
                if (d > 9) d -= 9;
            }
            sum += d;
            dbl = !dbl;
        }
        return sum % 10 == 0;
    }

    // ------------------------------------------------------------------ lookups

    public Optional<Account> findAccount(String id) {
        return accounts.stream().filter(a -> a.id().equals(id)).findFirst();
    }

    public Optional<Beneficiary> findBeneficiary(String id) {
        return beneficiaries.stream().filter(b -> b.id().equals(id)).findFirst();
    }

    static String str(Object o) {
        return o == null ? null : o.toString();
    }

    static Map<String, String> err(String field, String message) {
        return Map.of("field", field, "message", message);
    }
}
