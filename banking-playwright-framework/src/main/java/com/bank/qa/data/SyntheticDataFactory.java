package com.bank.qa.data;

import com.bank.qa.config.FrameworkConfig;
import net.datafaker.Faker;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import java.math.BigDecimal;
import java.math.RoundingMode;
import java.time.LocalDate;
import java.time.YearMonth;
import java.time.ZoneId;
import java.time.format.DateTimeFormatter;
import java.util.Locale;
import java.util.Random;
import java.util.concurrent.atomic.AtomicInteger;

/**
 * SYNTHETIC FINANCIAL TEST DATA.
 *
 * <h2>Test data in a regulated environment - the rules this class enforces</h2>
 * <ol>
 *   <li><b>No production data, ever.</b> Copying/masking prod data into test environments creates
 *       GDPR, PCI-DSS and banking-secrecy exposure. Everything here is generated.</li>
 *   <li><b>Structurally valid, provably fake.</b> PANs use reserved TEST BIN ranges (400000/400005/
 *       400051 - the issuer simulator maps BIN -> outcome) with a correct Luhn digit; IBANs use a
 *       fictitious bank code with correct mod-97 check digits; sort codes are in a test range.</li>
 *   <li><b>Deterministic &amp; reproducible.</b> A single run seed (config {@code data.seed}, logged at
 *       start) plus a per-test salt gives each test its own reproducible stream, independent of
 *       parallel scheduling order. Re-run a failure with {@code -Ddata.seed=<seed>}.</li>
 *   <li><b>Unique &amp; traceable.</b> {@link #uniqueReference()} embeds a run id so parallel tests never
 *       collide on shared environments, and every record created by automation is identifiable for
 *       audit and clean-up (prefix {@code QA}).</li>
 *   <li><b>Masked in logs/reports</b> - see {@link PiiMasker}.</li>
 * </ol>
 */
public final class SyntheticDataFactory {

    private static final Logger log = LoggerFactory.getLogger(SyntheticDataFactory.class);
    private static final long RUN_SEED;
    /** Short id of this test run - part of every generated reference. */
    public static final String RUN_ID;
    private static final AtomicInteger SEQUENCE = new AtomicInteger();

    static {
        String configured = FrameworkConfig.get().get("data.seed");
        RUN_SEED = configured == null || configured.isBlank() ? new Random().nextLong() : Long.parseLong(configured);
        RUN_ID = Long.toString(Math.abs(RUN_SEED % 46656), 36).toUpperCase(); // 3 chars base36
        log.info("Synthetic data seed = {} (re-run with -Ddata.seed={}) runId={}", RUN_SEED, RUN_SEED, RUN_ID);
    }

    /** Card outcome is selected by BIN, the same way real issuer simulators work. */
    public enum CardProfile {
        APPROVE("400000"), DO_NOT_HONOUR("400005"), INSUFFICIENT_FUNDS("400051");
        final String bin;

        CardProfile(String bin) {
            this.bin = bin;
        }

        public String bin() {
            return bin;
        }
    }

    private final Random random;
    private final Faker faker;

    private SyntheticDataFactory(long seed) {
        this.random = new Random(seed);
        this.faker = new Faker(Locale.UK, new Random(seed));
    }

    /** Reproducible generator for one test: same seed + same test name => same data. */
    public static SyntheticDataFactory forTest(String testId) {
        return new SyntheticDataFactory(RUN_SEED ^ testId.hashCode());
    }

    public static long runSeed() {
        return RUN_SEED;
    }

    // ------------------------------------------------------------------ cards

    public String cardNumber(CardProfile profile) {
        StringBuilder payload = new StringBuilder(profile.bin);
        while (payload.length() < 15) payload.append(random.nextInt(10));
        return payload.append(FinancialIdentifiers.luhnCheckDigit(payload.toString())).toString();
    }

    /** Same PAN with the check digit deliberately broken (negative testing). */
    public String invalidLuhnCardNumber() {
        String valid = cardNumber(CardProfile.APPROVE);
        int last = valid.charAt(valid.length() - 1) - '0';
        return valid.substring(0, valid.length() - 1) + ((last + 1) % 10);
    }

    public String futureExpiry() {
        return YearMonth.now(ZoneId.of("Europe/London")).plusMonths(12 + random.nextInt(36)).format(DateTimeFormatter.ofPattern("MM/yy"));
    }

    public String cvv() {
        return String.format("%03d", random.nextInt(1000));
    }

    // ------------------------------------------------------------------ accounts

    /** GB IBAN with fictitious bank code "TEST" and a valid mod-97 check. */
    public String iban() {
        String sortCode = sortCode().replace("-", "");
        return FinancialIdentifiers.ibanFromBban("GB", "TEST" + sortCode + accountNumber());
    }

    /** Sort code in the 99-xx-xx test range. */
    public String sortCode() {
        return String.format("99-%02d-%02d", random.nextInt(100), random.nextInt(100));
    }

    public String accountNumber() {
        return String.format("%08d", random.nextInt(100_000_000));
    }

    public String companyName() {
        return faker.company().name();
    }

    // ------------------------------------------------------------------ payments

    /** Amount uniformly in [min, max] with 2 decimals (BigDecimal - never double for money). */
    public BigDecimal amountBetween(BigDecimal min, BigDecimal max) {
        BigDecimal span = max.subtract(min);
        BigDecimal r = span.multiply(BigDecimal.valueOf(random.nextDouble()));
        return min.add(r).setScale(2, RoundingMode.DOWN);
    }

    /** Unique, 18-char-safe payment reference: QA-&lt;runId&gt;-&lt;seq&gt;-&lt;rand&gt;, e.g. QA-7K2-0042-X9Q. */
    public String uniqueReference() {
        String rand = Integer.toString(random.nextInt(46656), 36).toUpperCase();
        return String.format("%s-%s-%04d-%s", FrameworkConfig.get().get("data.reference.prefix", "QA"),
                RUN_ID, SEQUENCE.incrementAndGet() % 10000, rand);
    }

    public String today() {
        return LocalDate.now(ZoneId.of("Europe/London")).toString();
    }

    public Random random() {
        return random;
    }
}
