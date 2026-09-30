package com.bank.qa.auth;

import javax.crypto.Mac;
import javax.crypto.spec.SecretKeySpec;
import java.nio.ByteBuffer;
import java.time.Instant;

/**
 * RFC 6238 Time-based One-Time Password generator.
 *
 * <p>AUTHENTICATION IN A REGULATED ENVIRONMENT: banking apps enforce MFA, so automation
 * cannot "turn MFA off". Common, auditable approaches:
 * <ol>
 *   <li><b>TOTP seed for dedicated test identities</b> (this class) - the seed lives in the
 *       secrets vault (GitHub/Jenkins secret, HashiCorp Vault, AWS Secrets Manager) and the
 *       test computes the same 6-digit code an authenticator app would.</li>
 *   <li>A test-only OTP delivery sink (SMS/email captured by a mock gateway) in lower envs.</li>
 *   <li>A token-minting endpoint that exists only in non-prod, guarded by mTLS.</li>
 * </ol>
 * Never share real employees' MFA devices with automation and never bypass MFA in prod.
 */
public final class TotpGenerator {

    private static final int DIGITS = 6;
    private static final int STEP_SECONDS = 30;

    private TotpGenerator() {}

    public static String now(String base32Secret) {
        return at(base32Secret, Instant.now());
    }

    public static String at(String base32Secret, Instant instant) {
        return generate(base32Decode(base32Secret), instant.getEpochSecond() / STEP_SECONDS);
    }

    /** Server-side verification helper accepting +/- one time-step of clock drift. */
    public static boolean verify(String base32Secret, String code, Instant instant) {
        if (code == null) return false;
        byte[] key = base32Decode(base32Secret);
        long counter = instant.getEpochSecond() / STEP_SECONDS;
        for (long c = counter - 1; c <= counter + 1; c++) {
            if (generate(key, c).equals(code)) return true;
        }
        return false;
    }

    private static String generate(byte[] key, long counter) {
        try {
            Mac mac = Mac.getInstance("HmacSHA1");
            mac.init(new SecretKeySpec(key, "HmacSHA1"));
            byte[] hash = mac.doFinal(ByteBuffer.allocate(8).putLong(counter).array());
            int offset = hash[hash.length - 1] & 0x0F;
            int binary = ((hash[offset] & 0x7F) << 24) | ((hash[offset + 1] & 0xFF) << 16)
                    | ((hash[offset + 2] & 0xFF) << 8) | (hash[offset + 3] & 0xFF);
            return String.format("%0" + DIGITS + "d", binary % (int) Math.pow(10, DIGITS));
        } catch (Exception e) {
            throw new IllegalStateException("TOTP generation failed", e);
        }
    }

    static byte[] base32Decode(String s) {
        String alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567";
        String clean = s.replace("=", "").replace(" ", "").toUpperCase();
        ByteBuffer out = ByteBuffer.allocate(clean.length() * 5 / 8);
        int buffer = 0;
        int bits = 0;
        for (char c : clean.toCharArray()) {
            int val = alphabet.indexOf(c);
            if (val < 0) throw new IllegalArgumentException("Invalid base32 character: " + c);
            buffer = (buffer << 5) | val;
            bits += 5;
            if (bits >= 8) {
                out.put((byte) (buffer >> (bits - 8)));
                bits -= 8;
            }
        }
        byte[] result = new byte[out.position()];
        out.flip();
        out.get(result);
        return result;
    }
}
