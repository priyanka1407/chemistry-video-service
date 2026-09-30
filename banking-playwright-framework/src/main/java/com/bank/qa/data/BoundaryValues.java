package com.bank.qa.data;

import java.math.BigDecimal;
import java.util.ArrayList;
import java.util.List;

/**
 * BOUNDARY VALUE ANALYSIS (BVA) generator.
 *
 * <p>From a user story acceptance criterion such as
 * <i>"amount must be between 0.01 and 50,000.00 (2 dp)"</i> we derive the classic
 * 3-value BVA set for each boundary: just below, on, just above - plus a nominal value.
 *
 * <pre>
 *       invalid  |  valid                                   valid  |  invalid
 *   ----- 0.00 --|-- 0.01 -- 0.02 ....... 49,999.99 -- 50,000.00 --|-- 50,000.01 -----
 *        min-ε      min      min+ε          max-ε        max          max+ε
 * </pre>
 * ε is the smallest representable step (0.01 for money in GBP, 1 for string length).
 * Equivalence partitioning adds one representative per partition (nominal).
 * Internal thresholds (e.g. "approval required above 10,000.00") are boundaries too and get
 * the same treatment via {@link #threshold}.
 */
public final class BoundaryValues {

    public enum Expect { VALID, INVALID }

    /** One generated case: the value, whether it should be accepted, and why (for readable test names). */
    public record Case<T>(T value, Expect expect, String label) {
        public boolean valid() {
            return expect == Expect.VALID;
        }

        @Override
        public String toString() {
            return label + " [" + value + "] -> " + expect;
        }
    }

    private BoundaryValues() {}

    /** Numeric range [min, max] inclusive with step epsilon. */
    public static List<Case<BigDecimal>> range(BigDecimal min, BigDecimal max, BigDecimal epsilon) {
        List<Case<BigDecimal>> cases = new ArrayList<>();
        cases.add(new Case<>(min.subtract(epsilon), Expect.INVALID, "min - ε"));
        cases.add(new Case<>(min, Expect.VALID, "min"));
        cases.add(new Case<>(min.add(epsilon), Expect.VALID, "min + ε"));
        cases.add(new Case<>(min.add(max).divide(BigDecimal.TWO).setScale(epsilon.scale(), java.math.RoundingMode.DOWN),
                Expect.VALID, "nominal"));
        cases.add(new Case<>(max.subtract(epsilon), Expect.VALID, "max - ε"));
        cases.add(new Case<>(max, Expect.VALID, "max"));
        cases.add(new Case<>(max.add(epsilon), Expect.INVALID, "max + ε"));
        return cases;
    }

    /**
     * Internal threshold where behaviour changes but both sides are valid, e.g. approval above 10,000.00.
     * Returned cases carry {@code VALID} for "at or below threshold" and {@code INVALID} meaning
     * "on the other side of the threshold" - callers map that to the business outcome.
     */
    public static List<Case<BigDecimal>> threshold(BigDecimal threshold, BigDecimal epsilon) {
        return List.of(
                new Case<>(threshold.subtract(epsilon), Expect.VALID, "threshold - ε"),
                new Case<>(threshold, Expect.VALID, "threshold"),
                new Case<>(threshold.add(epsilon), Expect.INVALID, "threshold + ε"));
    }

    /** String length boundaries [minLen, maxLen] built from a fill character. */
    public static List<Case<String>> length(int minLen, int maxLen, char fill) {
        List<Case<String>> cases = new ArrayList<>();
        if (minLen > 0) cases.add(new Case<>(String.valueOf(fill).repeat(minLen - 1), Expect.INVALID, "minLen - 1"));
        cases.add(new Case<>(String.valueOf(fill).repeat(minLen), Expect.VALID, "minLen"));
        cases.add(new Case<>(String.valueOf(fill).repeat(minLen + 1), Expect.VALID, "minLen + 1"));
        cases.add(new Case<>(String.valueOf(fill).repeat(maxLen - 1), Expect.VALID, "maxLen - 1"));
        cases.add(new Case<>(String.valueOf(fill).repeat(maxLen), Expect.VALID, "maxLen"));
        cases.add(new Case<>(String.valueOf(fill).repeat(maxLen + 1), Expect.INVALID, "maxLen + 1"));
        return cases;
    }
}
