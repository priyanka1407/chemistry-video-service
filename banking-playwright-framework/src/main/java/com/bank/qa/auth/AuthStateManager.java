package com.bank.qa.auth;

import com.bank.qa.config.FrameworkConfig;
import com.bank.qa.config.Secrets;
import com.bank.qa.core.AppUnderTest;
import com.bank.qa.core.BrowserManager;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.microsoft.playwright.APIRequest;
import com.microsoft.playwright.APIRequestContext;
import com.microsoft.playwright.APIResponse;
import com.microsoft.playwright.options.RequestOptions;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import java.io.IOException;
import java.io.RandomAccessFile;
import java.nio.channels.FileChannel;
import java.nio.channels.FileLock;
import java.nio.file.Files;
import java.nio.file.Path;
import java.time.Duration;
import java.time.Instant;
import java.util.Map;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.locks.ReentrantLock;

/**
 * STORAGE STATE HANDLING - "log in once per role, reuse everywhere".
 *
 * <p>Logging in through the UI (+ MFA) in every test is slow and the #1 source of auth flakiness.
 * Instead, the first test that needs role X:
 * <ol>
 *   <li>logs in via the API (username + password + TOTP) with an {@link APIRequestContext},</li>
 *   <li>saves cookies + localStorage with {@code request.storageState(path)} to
 *       {@code target/.auth/<role>.json},</li>
 *   <li>every later test creates a BrowserContext with {@code setStorageStatePath(file)} - it
 *       starts already authenticated.</li>
 * </ol>
 *
 * <p>Details that matter in interviews:
 * <ul>
 *   <li><b>Concurrency:</b> parallel threads needing the same role must not all log in at once - a
 *       per-role {@link ReentrantLock} plus an OS {@link FileLock} (for multiple JVMs/forks sharing
 *       the directory) makes exactly one of them create the file.</li>
 *   <li><b>Freshness:</b> state is reused only while younger than {@code auth.state.ttl.minutes} AND
 *       all cookies are unexpired; otherwise it is recreated (sessions expire mid-run in long suites).</li>
 *   <li><b>What is NOT saved:</b> storageState captures cookies and localStorage (and optionally
 *       IndexedDB), <b>not sessionStorage</b>. Apps that keep tokens in sessionStorage need an
 *       {@code addInitScript} to restore them.</li>
 *   <li><b>Shared-session hazard:</b> all tests using {@code maker.json} share ONE server session. A
 *       test that logs out / times out would kill it for everyone running in parallel - such tests
 *       must request a fresh, private session ({@link #freshLogin(Role)}).</li>
 *   <li><b>Security:</b> the JSON contains live session cookies - it lives under {@code target/},
 *       is git-ignored and must never be uploaded as a CI artifact.</li>
 * </ul>
 */
public final class AuthStateManager {

    private static final Logger log = LoggerFactory.getLogger(AuthStateManager.class);
    private static final ObjectMapper JSON = new ObjectMapper();
    private static final Map<Role, ReentrantLock> LOCKS = new ConcurrentHashMap<>();
    /** Roles whose state file was created or verified against the server by THIS JVM. */
    private static final java.util.Set<Role> VERIFIED = ConcurrentHashMap.newKeySet();

    private AuthStateManager() {}

    /** Returns a valid storage-state file for the role, creating it at most once concurrently. */
    public static Path storageStateFor(Role role) {
        if (role == Role.ANONYMOUS) return null;
        FrameworkConfig cfg = FrameworkConfig.get();
        Path file = cfg.authStateDir().resolve(role.name().toLowerCase() + ".json");
        ReentrantLock lock = LOCKS.computeIfAbsent(role, r -> new ReentrantLock());
        lock.lock();
        try {
            Files.createDirectories(file.getParent());
            try (FileChannel ch = new RandomAccessFile(file.resolveSibling(file.getFileName() + ".lock").toFile(), "rw").getChannel();
                 FileLock ignored = ch.lock()) {
                if (VERIFIED.contains(role)) {
                    if (isFresh(file, Duration.ofMinutes(cfg.getInt("auth.state.ttl.minutes")))) return file;
                } else if (isFresh(file, Duration.ofMinutes(cfg.getInt("auth.state.ttl.minutes"))) && acceptedByServer(file, role)) {
                    VERIFIED.add(role);
                    return file;
                }
                log.info("Creating storage state for role {}", role);
                APIRequestContext request = loginViaApi(role);
                try {
                    request.storageState(new APIRequestContext.StorageStateOptions().setPath(file));
                } finally {
                    request.dispose();
                }
                VERIFIED.add(role);
                return file;
            }
        } catch (IOException e) {
            throw new IllegalStateException("Cannot create storage state for " + role, e);
        } finally {
            lock.unlock();
        }
    }

    /**
     * A brand-new private session (not cached). Use for logout, session-timeout and concurrency tests
     * so they cannot invalidate the shared per-role session other parallel tests rely on.
     */
    public static Path freshLogin(Role role) {
        try {
            Path dir = Files.createDirectories(FrameworkConfig.get().authStateDir());
            Path file = Files.createTempFile(dir, role.name().toLowerCase() + "-fresh-", ".json");
            APIRequestContext request = loginViaApi(role);
            try {
                request.storageState(new APIRequestContext.StorageStateOptions().setPath(file));
            } finally {
                request.dispose();
            }
            file.toFile().deleteOnExit();
            return file;
        } catch (IOException e) {
            throw new IllegalStateException(e);
        }
    }

    /** Programmatic login: password step + MFA step, exactly like the UI does it. */
    public static APIRequestContext loginViaApi(Role role) {
        Secrets.Credential cred = Secrets.forRole(role);
        APIRequestContext request = BrowserManager.playwright().request().newContext(
                new APIRequest.NewContextOptions()
                        .setBaseURL(AppUnderTest.baseUrl()));
        APIResponse step1 = request.post("/api/auth/login", RequestOptions.create()
                .setData(Map.of("username", cred.username(), "password", cred.password())));
        ensureOk(step1, role, "password");
        APIResponse step2 = request.post("/api/auth/login", RequestOptions.create()
                .setData(Map.of("username", cred.username(), "password", cred.password(),
                        "otp", TotpGenerator.now(cred.totpSecret()))));
        ensureOk(step2, role, "mfa");
        return request;
    }

    private static void ensureOk(APIResponse res, Role role, String step) {
        if (!res.ok()) {
            // Never log the request body - it contains the password.
            throw new IllegalStateException("API login (" + step + ") failed for role " + role + ": HTTP " + res.status());
        }
    }

    /**
     * "Trust but verify": a state file left over from an earlier run can be young enough yet useless
     * (server restarted, deployment rotated session keys, session revoked). One cheap GET /api/me per
     * role per JVM confirms it before dozens of tests depend on it.
     */
    private static boolean acceptedByServer(Path file, Role role) {
        APIRequestContext request = BrowserManager.playwright().request().newContext(new APIRequest.NewContextOptions()
                .setBaseURL(AppUnderTest.baseUrl()).setStorageStatePath(file));
        try {
            APIResponse me = request.get("/api/me");
            boolean ok = me.ok() && me.text().contains("\"role\":\"" + role.name() + "\"");
            if (!ok) log.info("Cached storage state for {} rejected by server (HTTP {}) - re-authenticating", role, me.status());
            return ok;
        } finally {
            request.dispose();
        }
    }

    static boolean isFresh(Path file, Duration ttl) throws IOException {
        if (!Files.exists(file)) return false;
        if (Files.getLastModifiedTime(file).toInstant().isBefore(Instant.now().minus(ttl))) return false;
        JsonNode cookies = JSON.readTree(file.toFile()).path("cookies");
        if (cookies.isEmpty()) return false;
        long nowSeconds = Instant.now().getEpochSecond() + 60; // 1 minute safety buffer
        for (JsonNode c : cookies) {
            double expires = c.path("expires").asDouble(-1);
            if (expires > 0 && expires < nowSeconds) return false;
        }
        return true;
    }
}
