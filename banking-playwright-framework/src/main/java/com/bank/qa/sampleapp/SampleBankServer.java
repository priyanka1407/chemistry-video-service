package com.bank.qa.sampleapp;

import com.bank.qa.auth.TotpGenerator;
import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.sun.net.httpserver.HttpExchange;
import com.sun.net.httpserver.HttpServer;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.InetSocketAddress;
import java.net.URLDecoder;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.time.Instant;
import java.util.HexFormat;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.UUID;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.Executors;
import java.util.concurrent.ScheduledExecutorService;
import java.util.concurrent.ThreadLocalRandom;
import java.util.concurrent.TimeUnit;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/**
 * Embedded sample banking web application ("Northbridge Bank - Corporate Portal").
 *
 * <p>Started automatically by the test framework (see {@code AppUnderTest}) or manually with
 * {@code mvn -q exec:java} for exploration and {@code playwright codegen}.
 *
 * <p>Endpoints (all JSON unless stated):
 * <pre>
 *  POST /api/auth/login              {username,password,otp?} -> 200 {mfaRequired} | 200 + Set-Cookie
 *  POST /api/auth/logout
 *  GET  /api/me
 *  GET  /api/accounts
 *  GET  /api/beneficiaries?q=        (artificial latency -> dynamic dropdown)
 *  GET  /external/fx/rates?base=GBP  (pretend 3rd-party FX provider -> network mocking target)
 *  POST /api/payments                (Idempotency-Key header supported)
 *  GET  /api/payments[?status=] , GET /api/payments/{id}
 *  POST /api/payments/{id}/approve | /reject   (maker-checker / four-eyes)
 *  POST /api/cards/authorize , POST /api/cards/3ds/verify
 *  GET  /api/settlements , GET /api/settlements/{id} , POST /api/settlements/{id}/close
 *  GET  /api/settlements/{id}/report.csv       (file download)
 *  GET  /api/admin/users
 * </pre>
 */
public final class SampleBankServer {

    private static final Logger log = LoggerFactory.getLogger(SampleBankServer.class);
    private static final ObjectMapper JSON = new ObjectMapper();
    private static final String SESSION_COOKIE = "NB_SESSION";

    private final BankStore store;
    private final HttpServer http;
    private final ScheduledExecutorService worker = Executors.newSingleThreadScheduledExecutor(r -> {
        Thread t = new Thread(r, "settlement-queue-worker");
        t.setDaemon(true);
        return t;
    });
    private final boolean simulateLatency;

    private record Session(String id, String username, Instant created, Instant lastSeen) {
        Session touch() {
            return new Session(id, username, created, Instant.now());
        }
    }

    private final Map<String, Session> sessions = new ConcurrentHashMap<>();

    public SampleBankServer(int port, String password, boolean simulateLatency) throws IOException {
        this.store = new BankStore(password);
        this.simulateLatency = simulateLatency;
        this.http = HttpServer.create(new InetSocketAddress("127.0.0.1", port), 0);
        this.http.createContext("/", this::handle);
        this.http.setExecutor(Executors.newFixedThreadPool(32));
    }

    public static void main(String[] args) throws Exception {
        int port = Integer.parseInt(System.getProperty("port", "8085"));
        SampleBankServer server = new SampleBankServer(port, System.getProperty("sampleapp.password", "Test@12345"), true);
        server.start();
        log.info("Northbridge sample bank running at {}", server.baseUrl());
        log.info("Users: viewer.user | maker.user | checker.user | admin.user   password: Test@12345");
        log.info("MFA codes: run `mvn -q exec:java -Dexec.mainClass=com.bank.qa.sampleapp.PrintTotp`");
        Thread.currentThread().join();
    }

    public void start() {
        http.start();
        worker.scheduleAtFixedRate(() -> {
            try {
                store.processQueueTick();
            } catch (Exception e) {
                log.error("queue tick failed", e);
            }
        }, 200, 200, TimeUnit.MILLISECONDS);
    }

    public void stop() {
        worker.shutdownNow();
        http.stop(0);
    }

    public String baseUrl() {
        return "http://localhost:" + http.getAddress().getPort();
    }

    // =========================================================================== routing

    private void handle(HttpExchange ex) throws IOException {
        try {
            String path = ex.getRequestURI().getPath();
            if (path.startsWith("/api/") || path.startsWith("/external/")) {
                route(ex, ex.getRequestMethod(), path);
            } else {
                serveStatic(ex, path);
            }
        } catch (HttpError e) {
            sendJson(ex, e.status, Map.of("error", e.body));
        } catch (com.fasterxml.jackson.core.JacksonException e) {
            sendJson(ex, 400, Map.of("error", Map.of("code", "MALFORMED_JSON", "message", "Request body is not valid JSON")));
        } catch (IOException e) {
            log.debug("Client went away: {}", e.toString()); // navigation aborted an in-flight request
        } catch (Exception e) {
            log.error("Unhandled error", e);
            sendJson(ex, 500, Map.of("error", Map.of("code", "INTERNAL_ERROR", "message", "Unexpected error")));
        } finally {
            ex.close();
        }
    }

    private void route(HttpExchange ex, String method, String path) throws Exception {
        Matcher m;
        if (method.equals("POST") && path.equals("/api/auth/login")) { login(ex); return; }
        if (method.equals("POST") && path.equals("/api/auth/logout")) { logout(ex); return; }
        if (method.equals("GET") && path.equals("/external/fx/rates")) { fxRates(ex); return; }
        if (method.equals("GET") && path.equals("/api/health")) { sendJson(ex, 200, Map.of("status", "UP")); return; }

        Session s = requireSession(ex);
        String role = store.users.get(s.username()).role();

        if (method.equals("GET") && path.equals("/api/me")) { me(ex, s); return; }
        if (method.equals("GET") && path.equals("/api/accounts")) {
            requirePermission(role, "ACCOUNTS_VIEW");
            latency(150, 600);
            sendJson(ex, 200, store.accounts);
            return;
        }
        if (method.equals("GET") && path.equals("/api/beneficiaries")) {
            requirePermission(role, "PAYMENTS_VIEW");
            String q = query(ex).getOrDefault("q", "").toLowerCase();
            latency(250, 700);
            sendJson(ex, 200, store.beneficiaries.stream()
                    .filter(b -> b.name().toLowerCase().contains(q) || b.iban().toLowerCase().contains(q)).toList());
            return;
        }
        if (path.equals("/api/payments")) {
            if (method.equals("GET")) {
                requirePermission(role, "PAYMENTS_VIEW");
                sendJson(ex, 200, store.listPayments(query(ex).get("status")).stream().map(this::paymentJson).toList());
            } else if (method.equals("POST")) {
                requirePermission(role, "PAYMENTS_CREATE");
                createPayment(ex, s);
            }
            return;
        }
        if ((m = match("/api/payments/([A-Z0-9-]+)", path)) != null && method.equals("GET")) {
            requirePermission(role, "PAYMENTS_VIEW");
            sendJson(ex, 200, paymentJson(payment(m.group(1))));
            return;
        }
        if ((m = match("/api/payments/([A-Z0-9-]+)/(approve|reject)", path)) != null && method.equals("POST")) {
            requirePermission(role, "PAYMENTS_APPROVE");
            decidePayment(ex, s, payment(m.group(1)), m.group(2));
            return;
        }
        if (method.equals("POST") && path.equals("/api/cards/authorize")) {
            requirePermission(role, "CARDS_AUTHORIZE");
            Map<String, Object> body = body(ex);
            List<Map<String, String>> errors = store.validateCard(body);
            if (!errors.isEmpty()) throw validation(errors);
            latency(100, 400);
            sendJson(ex, 200, cardJson(store.authorizeCard(body)));
            return;
        }
        if (method.equals("POST") && path.equals("/api/cards/3ds/verify")) {
            requirePermission(role, "CARDS_AUTHORIZE");
            Map<String, Object> body = body(ex);
            BankStore.CardAuthorization a = store.cardAuths.get(BankStore.str(body.get("authId")));
            if (a == null) throw new HttpError(404, "NOT_FOUND", "Unknown authorization");
            if (!"CHALLENGE".equals(a.decision)) throw new HttpError(409, "INVALID_STATE", "Authorization is not awaiting a challenge");
            if (BankStore.THREE_DS_OTP.equals(BankStore.str(body.get("otp")))) {
                BankStore.decide(a, "APPROVED", "00", "Approved after 3-D Secure");
            } else {
                BankStore.decide(a, "DECLINED", "N0", "Authentication failed");
            }
            sendJson(ex, 200, cardJson(a));
            return;
        }
        if (method.equals("GET") && path.equals("/api/settlements")) {
            requirePermission(role, "SETTLEMENTS_VIEW");
            sendJson(ex, 200, store.listBatches().stream().map(this::batchJson).toList());
            return;
        }
        if ((m = match("/api/settlements/([A-Z0-9-]+)", path)) != null && method.equals("GET")) {
            requirePermission(role, "SETTLEMENTS_VIEW");
            BankStore.SettlementBatch b = batch(m.group(1));
            Map<String, Object> json = batchJson(b);
            synchronized (b) {
                json.put("payments", b.paymentIds.stream().map(id -> paymentJson(store.payments.get(id))).toList());
            }
            sendJson(ex, 200, json);
            return;
        }
        if ((m = match("/api/settlements/([A-Z0-9-]+)/close", path)) != null && method.equals("POST")) {
            requirePermission(role, "SETTLEMENTS_CLOSE");
            BankStore.SettlementBatch b = batch(m.group(1));
            if (!"OPEN".equals(b.status)) throw new HttpError(409, "INVALID_STATE", "Batch is not open");
            store.closeBatch(b);
            sendJson(ex, 202, batchJson(b));
            return;
        }
        if ((m = match("/api/settlements/([A-Z0-9-]+)/report\\.csv", path)) != null && method.equals("GET")) {
            requirePermission(role, "SETTLEMENTS_VIEW");
            BankStore.SettlementBatch b = batch(m.group(1));
            StringBuilder csv = new StringBuilder("paymentId,reference,beneficiary,amount,currency,status\n");
            synchronized (b) {
                for (String id : b.paymentIds) {
                    BankStore.Payment p = store.payments.get(id);
                    csv.append(String.join(",", p.id, p.reference, p.beneficiaryName.replace(",", " "),
                            p.amount.toPlainString(), p.currency, p.status)).append('\n');
                }
            }
            ex.getResponseHeaders().add("Content-Disposition", "attachment; filename=\"" + b.batchId + ".csv\"");
            send(ex, 200, "text/csv; charset=utf-8", csv.toString().getBytes(StandardCharsets.UTF_8));
            return;
        }
        if (method.equals("GET") && path.equals("/api/admin/users")) {
            requirePermission(role, "ADMIN_USERS");
            sendJson(ex, 200, store.users.values().stream()
                    .map(u -> Map.of("username", u.username(), "displayName", u.displayName(), "role", u.role())).toList());
            return;
        }
        throw new HttpError(404, "NOT_FOUND", "No route for " + method + " " + path);
    }

    // =========================================================================== handlers

    private void login(HttpExchange ex) throws Exception {
        Map<String, Object> body = body(ex);
        String username = BankStore.str(body.get("username"));
        String password = BankStore.str(body.get("password"));
        BankStore.User user = username == null ? null : store.users.get(username);
        latency(100, 300);
        if (user == null || !store.passwords.get(username).equals(password)) {
            throw new HttpError(401, "INVALID_CREDENTIALS", "Username or password is incorrect");
        }
        String otp = BankStore.str(body.get("otp"));
        if (otp == null || otp.isBlank()) {
            sendJson(ex, 200, Map.of("mfaRequired", true));
            return;
        }
        if (!TotpGenerator.verify(user.totpSecret(), otp, Instant.now())) {
            throw new HttpError(401, "INVALID_OTP", "The verification code is incorrect or expired");
        }
        Session s = new Session(UUID.randomUUID().toString(), username, Instant.now(), Instant.now());
        sessions.put(s.id(), s);
        // HttpOnly + SameSite=Strict: the token is NOT readable from JS (XSS-safe) - which is exactly why
        // tests persist it via Playwright storageState (cookies) instead of scraping localStorage.
        ex.getResponseHeaders().add("Set-Cookie", SESSION_COOKIE + "=" + s.id()
                + "; Path=/; HttpOnly; SameSite=Strict; Max-Age=" + 8 * 3600);
        sendJson(ex, 200, Map.of("mfaRequired", false, "username", username, "role", user.role()));
    }

    private void logout(HttpExchange ex) throws IOException {
        String sid = cookie(ex, SESSION_COOKIE);
        if (sid != null) sessions.remove(sid);
        ex.getResponseHeaders().add("Set-Cookie", SESSION_COOKIE + "=; Path=/; Max-Age=0");
        sendJson(ex, 200, Map.of("loggedOut", true));
    }

    private void me(HttpExchange ex, Session s) throws IOException {
        BankStore.User u = store.users.get(s.username());
        Map<String, Object> json = new LinkedHashMap<>();
        json.put("username", u.username());
        json.put("displayName", u.displayName());
        json.put("role", u.role());
        json.putAll(BankStore.permissionsFor(u.role()));
        json.put("sessionIdleTimeoutSeconds", BankStore.SESSION_IDLE_TIMEOUT_SECONDS);
        sendJson(ex, 200, json);
    }

    private void fxRates(HttpExchange ex) throws IOException {
        // Pretend third-party dependency: slow and occasionally unavailable -> the reason we MOCK it.
        latency(400, 1200);
        String base = query(ex).getOrDefault("base", "GBP");
        Map<String, Object> rates = new LinkedHashMap<>();
        rates.put("GBP", 1.0);
        rates.put("EUR", 1.1712 + ThreadLocalRandom.current().nextDouble(-0.01, 0.01));
        rates.put("USD", 1.2655 + ThreadLocalRandom.current().nextDouble(-0.01, 0.01));
        sendJson(ex, 200, Map.of("provider", "FXPrime", "base", base, "timestamp", Instant.now().toString(), "rates", rates));
    }

    private void createPayment(HttpExchange ex, Session s) throws Exception {
        Map<String, Object> body = body(ex);
        String idemKey = ex.getRequestHeaders().getFirst("Idempotency-Key");
        String fingerprint = sha256(JSON.writeValueAsString(new java.util.TreeMap<>(body)));
        if (idemKey != null) {
            String[] previous = store.idempotency.get(s.username() + ":" + idemKey);
            if (previous != null) {
                if (!previous[0].equals(fingerprint)) {
                    throw new HttpError(409, "IDEMPOTENCY_KEY_REUSED", "Idempotency-Key was used with a different request body");
                }
                ex.getResponseHeaders().add("Idempotent-Replayed", "true");
                sendJson(ex, 200, paymentJson(store.payments.get(previous[1])));
                return;
            }
        }
        List<Map<String, String>> errors = store.validatePayment(body);
        if (!errors.isEmpty()) throw validation(errors);
        latency(150, 500);
        BankStore.Payment p = store.createPayment(body, s.username());
        if (idemKey != null) store.idempotency.put(s.username() + ":" + idemKey, new String[]{fingerprint, p.id});
        ex.getResponseHeaders().add("Location", "/api/payments/" + p.id);
        sendJson(ex, 201, paymentJson(p));
    }

    private void decidePayment(HttpExchange ex, Session s, BankStore.Payment p, String action) throws IOException {
        if (!"PENDING_APPROVAL".equals(p.status)) {
            throw new HttpError(409, "INVALID_STATE", "Payment is " + p.status + ", not PENDING_APPROVAL");
        }
        if (p.createdBy.equals(s.username())) {
            // Four-eyes principle: the initiator can never authorise their own payment.
            throw new HttpError(403, "FOUR_EYES_VIOLATION", "You cannot approve a payment you initiated");
        }
        if (action.equals("approve")) {
            p.approvedBy = s.username();
            store.transition(p, "PENDING_SETTLEMENT", s.username());
        } else {
            store.transition(p, "REJECTED", s.username());
        }
        sendJson(ex, 200, paymentJson(p));
    }

    // =========================================================================== JSON views

    private Map<String, Object> paymentJson(BankStore.Payment p) {
        Map<String, Object> m = new LinkedHashMap<>();
        synchronized (p) {
            m.put("paymentId", p.id);
            m.put("reference", p.reference);
            m.put("debtorAccountId", p.debtorAccountId);
            m.put("beneficiaryId", p.beneficiaryId);
            m.put("beneficiaryName", p.beneficiaryName);
            m.put("amount", p.amount.toPlainString());
            m.put("currency", p.currency);
            m.put("executionDate", p.executionDate);
            m.put("status", p.status);
            m.put("createdBy", p.createdBy);
            m.put("approvedBy", p.approvedBy);
            m.put("createdAt", p.createdAt.toString());
            m.put("updatedAt", p.updatedAt.toString());
            m.put("settlementBatchId", p.settlementBatchId);
            m.put("statusHistory", List.copyOf(p.statusHistory));
        }
        return m;
    }

    private Map<String, Object> batchJson(BankStore.SettlementBatch b) {
        Map<String, Object> m = new LinkedHashMap<>();
        m.put("batchId", b.batchId);
        m.put("businessDate", b.businessDate);
        m.put("status", b.status);
        synchronized (b) {
            m.put("paymentCount", b.paymentIds.size());
        }
        m.put("totalAmount", store.batchTotal(b).setScale(2).toPlainString());
        return m;
    }

    private Map<String, Object> cardJson(BankStore.CardAuthorization a) {
        Map<String, Object> m = new LinkedHashMap<>();
        m.put("authId", a.authId);
        m.put("maskedPan", a.maskedPan);
        m.put("amount", a.amount.setScale(2).toPlainString());
        m.put("currency", a.currency);
        m.put("decision", a.decision);
        m.put("responseCode", a.responseCode);
        m.put("message", a.message);
        m.put("createdAt", a.createdAt.toString());
        return m;
    }

    // =========================================================================== plumbing

    private Session requireSession(HttpExchange ex) {
        String sid = cookie(ex, SESSION_COOKIE);
        Session s = sid == null ? null : sessions.get(sid);
        if (s == null) throw new HttpError(401, "UNAUTHENTICATED", "Please sign in");
        if (s.lastSeen().plusSeconds(BankStore.SESSION_IDLE_TIMEOUT_SECONDS).isBefore(Instant.now())) {
            sessions.remove(sid);
            throw new HttpError(401, "SESSION_EXPIRED", "Your session has expired");
        }
        Session touched = s.touch();
        sessions.put(sid, touched);
        return touched;
    }

    private static void requirePermission(String role, String permission) {
        if (!BankStore.hasPermission(role, permission)) {
            throw new HttpError(403, "FORBIDDEN", "Missing permission " + permission);
        }
    }

    private BankStore.Payment payment(String id) {
        BankStore.Payment p = store.payments.get(id);
        if (p == null) throw new HttpError(404, "NOT_FOUND", "Payment " + id + " not found");
        return p;
    }

    private BankStore.SettlementBatch batch(String id) {
        BankStore.SettlementBatch b = store.batches.get(id);
        if (b == null) throw new HttpError(404, "NOT_FOUND", "Batch " + id + " not found");
        return b;
    }

    private void latency(int minMs, int maxMs) {
        if (!simulateLatency) return;
        try {
            Thread.sleep(ThreadLocalRandom.current().nextInt(minMs, maxMs));
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
        }
    }

    private static HttpError validation(List<Map<String, String>> errors) {
        return new HttpError(422, Map.of("code", "VALIDATION_FAILED", "message", "Request validation failed", "fields", errors));
    }

    private static Map<String, Object> body(HttpExchange ex) throws IOException {
        String raw = new String(ex.getRequestBody().readAllBytes(), StandardCharsets.UTF_8);
        if (raw.isBlank()) return Map.of();
        try {
            return JSON.readValue(raw, new TypeReference<>() {});
        } catch (Exception e) {
            throw new HttpError(400, "MALFORMED_JSON", "Request body is not valid JSON");
        }
    }

    private static Map<String, String> query(HttpExchange ex) {
        Map<String, String> q = new LinkedHashMap<>();
        String raw = ex.getRequestURI().getRawQuery();
        if (raw == null) return q;
        for (String pair : raw.split("&")) {
            String[] kv = pair.split("=", 2);
            q.put(URLDecoder.decode(kv[0], StandardCharsets.UTF_8), kv.length > 1 ? URLDecoder.decode(kv[1], StandardCharsets.UTF_8) : "");
        }
        return q;
    }

    private static String cookie(HttpExchange ex, String name) {
        List<String> headers = ex.getRequestHeaders().getOrDefault("Cookie", List.of());
        for (String h : headers) {
            for (String c : h.split(";")) {
                String[] kv = c.trim().split("=", 2);
                if (kv.length == 2 && kv[0].equals(name)) return kv[1];
            }
        }
        return null;
    }

    private static Matcher match(String regex, String path) {
        Matcher m = Pattern.compile("^" + regex + "$").matcher(path);
        return m.matches() ? m : null;
    }

    private void serveStatic(HttpExchange ex, String path) throws IOException {
        if (path.equals("/")) path = "/login.html";
        if (path.contains("..")) throw new HttpError(400, "BAD_PATH", "Invalid path");
        try (InputStream in = SampleBankServer.class.getResourceAsStream("/sampleapp" + path)) {
            if (in == null) {
                send(ex, 404, "text/html; charset=utf-8", "<h1>404 Not Found</h1>".getBytes(StandardCharsets.UTF_8));
                return;
            }
            String type = path.endsWith(".html") ? "text/html; charset=utf-8"
                    : path.endsWith(".js") ? "application/javascript; charset=utf-8"
                    : path.endsWith(".css") ? "text/css; charset=utf-8"
                    : path.endsWith(".svg") ? "image/svg+xml" : "application/octet-stream";
            send(ex, 200, type, in.readAllBytes());
        }
    }

    private static void sendJson(HttpExchange ex, int status, Object body) throws IOException {
        send(ex, status, "application/json", JSON.writeValueAsBytes(body));
    }

    private static void send(HttpExchange ex, int status, String contentType, byte[] bytes) throws IOException {
        ex.getResponseHeaders().set("Content-Type", contentType);
        // Sensitive data must never be served from the HTTP cache (e.g. on Back after sign-out).
        ex.getResponseHeaders().set("Cache-Control", "no-store");
        ex.sendResponseHeaders(status, bytes.length == 0 ? -1 : bytes.length);
        if (bytes.length > 0) {
            try (OutputStream os = ex.getResponseBody()) {
                os.write(bytes);
            }
        }
    }

    private static String sha256(String s) {
        try {
            return HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(s.getBytes(StandardCharsets.UTF_8)));
        } catch (Exception e) {
            throw new IllegalStateException(e);
        }
    }

    static final class HttpError extends RuntimeException {
        final int status;
        final Map<String, Object> body;

        HttpError(int status, String code, String message) {
            this(status, Map.of("code", code, "message", message));
        }

        HttpError(int status, Map<String, Object> body) {
            super(String.valueOf(body.get("code")));
            this.status = status;
            this.body = body;
        }
    }
}
