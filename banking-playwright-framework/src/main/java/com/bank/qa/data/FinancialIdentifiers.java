package com.bank.qa.data;

import java.math.BigInteger;

/**
 * Check-digit algorithms used to generate STRUCTURALLY VALID but FICTITIOUS financial identifiers.
 *
 * <p>Why not random digits? Real payment systems validate check digits at the edge. A random 16-digit
 * "card number" is rejected with "invalid card" before the logic you want to test ever runs -
 * generated data must be valid enough to reach the code under test, yet provably not real.
 */
public final class FinancialIdentifiers {

    private FinancialIdentifiers() {}

    // ------------------------------------------------------------------ Luhn (ISO/IEC 7812, cards)

    /** True when the digit string passes the Luhn mod-10 check. */
    public static boolean luhnValid(String digits) {
        if (digits == null || !digits.matches("\\d{2,}")) return false;
        return luhnSum(digits, false) % 10 == 0;
    }

    /** Computes the Luhn check digit for a payload (the PAN without its last digit). */
    public static int luhnCheckDigit(String payload) {
        int sum = luhnSum(payload, true);
        return (10 - sum % 10) % 10;
    }

    private static int luhnSum(String digits, boolean doubleFirst) {
        int sum = 0;
        boolean dbl = doubleFirst;
        for (int i = digits.length() - 1; i >= 0; i--) {
            int d = digits.charAt(i) - '0';
            if (dbl) {
                d *= 2;
                if (d > 9) d -= 9;
            }
            sum += d;
            dbl = !dbl;
        }
        return sum;
    }

    // ------------------------------------------------------------------ IBAN (ISO 13616 / ISO 7064 mod 97-10)

    public static String ibanFromBban(String countryCode, String bban) {
        int check = 98 - mod97(bban + countryCode + "00");
        return countryCode + String.format("%02d", check) + bban;
    }

    public static boolean ibanValid(String iban) {
        if (iban == null) return false;
        String s = iban.replace(" ", "").toUpperCase();
        if (!s.matches("[A-Z]{2}\\d{2}[A-Z0-9]{10,30}")) return false;
        return mod97(s.substring(4) + s.substring(0, 4)) == 1;
    }

    private static int mod97(String alnum) {
        StringBuilder numeric = new StringBuilder();
        for (char c : alnum.toCharArray()) {
            numeric.append(Character.isLetter(c) ? String.valueOf(c - 'A' + 10) : String.valueOf(c));
        }
        return new BigInteger(numeric.toString()).mod(BigInteger.valueOf(97)).intValue();
    }
}
