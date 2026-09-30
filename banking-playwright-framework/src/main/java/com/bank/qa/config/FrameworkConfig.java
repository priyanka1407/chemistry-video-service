package com.bank.qa.config;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import java.io.IOException;
import java.io.InputStream;
import java.nio.file.Path;
import java.util.Properties;

/**
 * Layered, immutable configuration.
 *
 * <pre>
 *   default.properties  <  &lt;env&gt;.properties  <  -Dsystem.property  <  BANK_ENV_VARIABLE
 * </pre>
 *
 * <p>Why layered? The same test binary must run on a laptop, in CI and against UAT without code
 * changes (12-factor style). Secrets are NEVER in property files that are committed - they only
 * arrive through the last layer (environment variables injected by the CI secret store).
 *
 * <p>Thread-safety: initialised once (holder idiom), read-only afterwards, safe for parallel tests.
 */
public final class FrameworkConfig {

    private static final Logger log = LoggerFactory.getLogger(FrameworkConfig.class);

    private final Properties props = new Properties();
    private final String env;

    private FrameworkConfig() {
        String envName = firstNonBlank(System.getProperty("env"), System.getenv("BANK_ENV"), "local");
        this.env = envName;
        load("config/default.properties", true);
        load("config/" + envName + ".properties", false);
        log.info("Configuration loaded for env='{}' browser='{}' headless={} baseUrl='{}'",
                envName, get("browser"), get("headless"), get("base.url"));
    }

    private static final class Holder {
        private static final FrameworkConfig INSTANCE = new FrameworkConfig();
    }

    public static FrameworkConfig get() {
        return Holder.INSTANCE;
    }

    private void load(String resource, boolean required) {
        try (InputStream in = FrameworkConfig.class.getClassLoader().getResourceAsStream(resource)) {
            if (in == null) {
                if (required) throw new IllegalStateException("Missing " + resource);
                log.warn("No {} found - using defaults", resource);
                return;
            }
            props.load(in);
        } catch (IOException e) {
            throw new IllegalStateException("Cannot read " + resource, e);
        }
    }

    /** Resolves a key through all layers. */
    public String get(String key) {
        String envVar = "BANK_" + key.toUpperCase().replace('.', '_');
        return firstNonBlank(System.getenv(envVar), System.getProperty(key), props.getProperty(key));
    }

    public String get(String key, String fallback) {
        String v = get(key);
        return v == null || v.isBlank() ? fallback : v;
    }

    public int getInt(String key) {
        return Integer.parseInt(get(key).trim());
    }

    public boolean getBool(String key) {
        return Boolean.parseBoolean(get(key, "false").trim());
    }

    public String env() {
        return env;
    }

    // ---- typed accessors for the most used keys
    public String browser() { return get("browser", "chromium").toLowerCase(); }
    public boolean headless() { return getBool("headless"); }
    public boolean embeddedApp() { return getBool("app.embedded"); }
    public int actionTimeout() { return getInt("timeout.action"); }
    public int navigationTimeout() { return getInt("timeout.navigation"); }
    public int assertionTimeout() { return getInt("timeout.assertion"); }
    public int settlementTimeout() { return getInt("timeout.async.settlement"); }
    public String traceMode() { return get("trace.mode", "retain-on-failure"); }
    public Path artifactsDir() { return Path.of(get("artifacts.dir", "target/playwright-artifacts")); }
    public Path authStateDir() { return Path.of(get("auth.state.dir", "target/.auth")); }
    public boolean mockThirdParty() { return getBool("mock.third.party"); }

    private static String firstNonBlank(String... values) {
        for (String v : values) {
            if (v != null && !v.isBlank() && !v.startsWith("${")) return v;
        }
        return null;
    }
}
