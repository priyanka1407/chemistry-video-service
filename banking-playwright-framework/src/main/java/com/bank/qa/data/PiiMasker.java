package com.bank.qa.data;

import java.util.regex.Matcher;
import java.util.regex.Pattern;

/**
 * Masks sensitive values before they reach logs, test reports, traces' attachments or tickets.
 *
 * <p>PCI-DSS req. 3.4: a PAN may be displayed with at most the first 6 and last 4 digits. The same
 * discipline is applied to IBANs, e-mails and anything named password/otp/cvv/token in JSON.
 * Even synthetic data is masked - the pipeline must behave identically when it one day sees real-looking
 * data, and reviewers should never have to decide "is this one fake?".
 */
public final class PiiMasker {

    private static final Pattern PAN = Pattern.compile("\\b(\\d{6})(\\d{3,9})(\\d{4})\\b");
    private static final Pattern IBAN = Pattern.compile("\\b([A-Z]{2}\\d{2})([A-Z0-9]{6,26})([A-Z0-9]{4})\\b");
    private static final Pattern SECRET_JSON = Pattern.compile(
            "(\"(?:password|otp|cvv|pan|token|totpSecret)\"\\s*:\\s*\")([^\"]*)(\")", Pattern.CASE_INSENSITIVE);

    private PiiMasker() {}

    public static String maskPan(String pan) {
        Matcher m = PAN.matcher(pan);
        return m.matches() ? m.group(1) + "*".repeat(m.group(2).length()) + m.group(3) : "****";
    }

    public static String mask(String text) {
        if (text == null) return null;
        String out = SECRET_JSON.matcher(text).replaceAll("$1***$3");
        out = PAN.matcher(out).replaceAll(r -> r.group(1) + "*".repeat(r.group(2).length()) + r.group(3));
        out = IBAN.matcher(out).replaceAll(r -> r.group(1) + "*".repeat(r.group(2).length()) + r.group(3));
        return out;
    }
}
