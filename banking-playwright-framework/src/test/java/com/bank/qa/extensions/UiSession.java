package com.bank.qa.extensions;

import com.bank.qa.api.BankApiClient;
import com.bank.qa.auth.AuthStateManager;
import com.bank.qa.auth.Role;
import com.bank.qa.config.FrameworkConfig;
import com.bank.qa.core.BrowserManager;
import com.bank.qa.data.SyntheticDataFactory;
import com.bank.qa.mock.MockSchemaGenerator;
import com.microsoft.playwright.BrowserContext;
import com.microsoft.playwright.Page;
import com.microsoft.playwright.Tracing;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.List;

/**
 * Everything one test owns: its primary context/page plus any extra contexts it opens for other users
 * (maker + checker in the same test). All are traced and closed automatically.
 *
 * <p>TRACE VIEWER (flakiness diagnosis): tracing records DOM snapshots before/after every action,
 * network, console and screenshots. With {@code trace.mode=retain-on-failure} the zip is saved only for
 * failing tests: {@code mvn exec:java -e -D exec.mainClass=com.microsoft.playwright.CLI
 * -D exec.args="show-trace target/playwright-artifacts/traces/<test>.zip"} or drag it onto trace.playwright.dev.
 */
public final class UiSession {

    private static final Logger log = LoggerFactory.getLogger(UiSession.class);

    private final String testId;
    private final List<BrowserContext> contexts = new ArrayList<>();
    private final BrowserContext primary;
    private final Page page;
    private final SyntheticDataFactory data;

    UiSession(String testId, Role role, boolean fresh) {
        this.testId = testId;
        this.data = SyntheticDataFactory.forTest(testId);
        this.primary = newContext(role, fresh);
        this.page = primary.newPage();
    }

    public Page page() { return page; }
    public BrowserContext context() { return primary; }
    public SyntheticDataFactory data() { return data; }
    public MockSchemaGenerator mocks() { return new MockSchemaGenerator(data); }

    /** API client sharing the primary context's cookies (same user as the page). */
    public BankApiClient api() {
        return new BankApiClient(primary.request());
    }

    /** Opens an additional, fully isolated browser context logged in as another role. */
    public Page openAs(Role role) {
        return newContext(role, false).newPage();
    }

    public BankApiClient apiAs(Role role) {
        return new BankApiClient(newContext(role, false).request());
    }

    private BrowserContext newContext(Role role, boolean fresh) {
        Path state = role == Role.ANONYMOUS ? null
                : fresh ? AuthStateManager.freshLogin(role) : AuthStateManager.storageStateFor(role);
        BrowserContext ctx = BrowserManager.newContext(state);
        if (!"off".equals(FrameworkConfig.get().traceMode())) {
            ctx.tracing().start(new Tracing.StartOptions()
                    .setScreenshots(true).setSnapshots(true).setSources(true).setTitle(testId));
        }
        contexts.add(ctx);
        return ctx;
    }

    void close(boolean failed) {
        FrameworkConfig cfg = FrameworkConfig.get();
        Path dir = cfg.artifactsDir();
        String mode = cfg.traceMode();
        for (int i = 0; i < contexts.size(); i++) {
            BrowserContext ctx = contexts.get(i);
            String suffix = contexts.size() > 1 ? "-ctx" + i : "";
            try {
                if (failed && cfg.getBool("screenshot.on.failure")) {
                    for (int p = 0; p < ctx.pages().size(); p++) {
                        ctx.pages().get(p).screenshot(new Page.ScreenshotOptions().setFullPage(true)
                                .setPath(dir.resolve("screenshots").resolve(testId + suffix + "-page" + p + ".png")));
                    }
                }
                if (!"off".equals(mode)) {
                    boolean keep = "on".equals(mode) || failed;
                    ctx.tracing().stop(keep
                            ? new Tracing.StopOptions().setPath(dir.resolve("traces").resolve(testId + suffix + ".zip"))
                            : new Tracing.StopOptions());
                    if (keep) log.info("Trace saved: {}", dir.resolve("traces").resolve(testId + suffix + ".zip"));
                }
                List<Page> pages = new ArrayList<>(ctx.pages());
                ctx.close(); // videos are finalised on close
                for (Page p : pages) {
                    if (p.video() == null) continue;
                    Path video = p.video().path();
                    if (failed) log.info("Video saved: {}", video);
                    else Files.deleteIfExists(video);
                }
            } catch (Exception e) {
                log.warn("Cleanup of context {} for {} failed: {}", i, testId, e.toString());
            }
        }
    }
}
