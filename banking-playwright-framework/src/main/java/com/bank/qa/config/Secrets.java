package com.bank.qa.config;

import com.bank.qa.auth.Role;

import java.util.Map;

/**
 * Credentials for the dedicated, non-personal TEST identities (one per role).
 *
 * <p>TEST DATA / SECRETS IN A REGULATED ENVIRONMENT (PCI-DSS, SOX, GDPR, FCA SYSC):
 * <ul>
 *   <li>Secrets come ONLY from the environment (CI secret store / Vault agent). They are never
 *       committed, never printed ({@link #toString()} is redacted) and never written into reports.</li>
 *   <li>Built-in fallbacks exist ONLY for the embedded sample app ({@code local}/{@code ci} envs).
 *       For any shared environment a missing secret fails fast instead of silently defaulting.</li>
 *   <li>Each role maps to a functional test account - never a real employee's account - so that
 *       every action in the audit log is attributable to automation.</li>
 * </ul>
 */
public final class Secrets {

    public record Credential(String username, String password, String totpSecret) {
        @Override
        public String toString() {
            return "Credential[username=" + username + ", password=***, totpSecret=***]";
        }
    }

    /** Sample-app-only fallbacks (NON-PRODUCTION, the app is in-process). */
    private static final Map<Role, Credential> SAMPLE_APP_DEFAULTS = Map.of(
            Role.VIEWER, new Credential("viewer.user", "Test@12345", "JBSWY3DPEHPK3PXP"),
            Role.MAKER, new Credential("maker.user", "Test@12345", "KRSXG5CTMVRXEZLU"),
            Role.CHECKER, new Credential("checker.user", "Test@12345", "MFRGGZDFMZTWQ2LK"),
            Role.ADMIN, new Credential("admin.user", "Test@12345", "GEZDGNBVGY3TQOJQ"));

    private Secrets() {}

    public static Credential forRole(Role role) {
        FrameworkConfig cfg = FrameworkConfig.get();
        String prefix = "auth." + role.name().toLowerCase() + ".";
        String user = cfg.get(prefix + "username");
        String pass = cfg.get(prefix + "password");
        String totp = cfg.get(prefix + "totp.secret");
        if (user != null && pass != null && totp != null) {
            return new Credential(user, pass, totp);
        }
        if (cfg.embeddedApp()) {
            return SAMPLE_APP_DEFAULTS.get(role);
        }
        throw new IllegalStateException("Missing credentials for role " + role + " in env '" + cfg.env()
                + "'. Provide BANK_AUTH_" + role.name() + "_USERNAME / _PASSWORD / _TOTP_SECRET via the secret store.");
    }
}
