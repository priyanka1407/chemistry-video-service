package com.bank.qa.core;

import com.bank.qa.config.FrameworkConfig;
import com.microsoft.playwright.Browser;
import com.microsoft.playwright.BrowserContext;
import com.microsoft.playwright.BrowserType;
import com.microsoft.playwright.Playwright;
import com.microsoft.playwright.options.ColorScheme;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import java.nio.file.Path;
import java.util.Queue;
import java.util.concurrent.ConcurrentLinkedQueue;

/**
 * Owns the Playwright -> Browser -> BrowserContext -> Page hierarchy.
 *
 * <h2>PARALLELISATION MODEL (most asked interview question for Playwright Java)</h2>
 * <ul>
 *   <li><b>Playwright / Browser objects are NOT thread-safe in Java.</b> Unlike the Node test runner
 *       (which uses worker <i>processes</i>), JUnit runs tests on threads of one JVM, so each thread
 *       gets its own {@link Playwright} + {@link Browser} via {@link ThreadLocal}.</li>
 *   <li>A Browser is expensive (~a process); a <b>BrowserContext</b> is cheap (~an incognito profile:
 *       own cookies, localStorage, cache). So: <b>one browser per worker thread, one fresh context per
 *       test</b>. That gives full test isolation at millisecond cost.</li>
 *   <li>Degree of parallelism is set in {@code junit-platform.properties}; scale-out beyond one machine
 *       is done with CI sharding ({@code -Dshard=2/4}).</li>
 *   <li>Never share a Page or Context between threads/tests; never use static Page fields.</li>
 * </ul>
 *
 * <h2>CONSISTENT BROWSER VERSION</h2>
 * The pinned Playwright artifact decides the browser revision; {@link #launch()} logs
 * {@code browser.version()} so every report states exactly which engine ran. Optional
 * {@code browser.channel=chrome|msedge} switches to the branded (auto-updating) build - only for
 * dedicated compatibility jobs, never for the gating pipeline.
 */
public final class BrowserManager {

    private static final Logger log = LoggerFactory.getLogger(BrowserManager.class);
    private static final ThreadLocal<Playwright> PLAYWRIGHT = new ThreadLocal<>();
    private static final ThreadLocal<Browser> BROWSER = new ThreadLocal<>();
    /** Everything created on any thread, closed once at JVM shutdown. */
    private static final Queue<AutoCloseable> ALL = new ConcurrentLinkedQueue<>();

    static {
        Runtime.getRuntime().addShutdownHook(new Thread(() -> ALL.forEach(c -> {
            try {
                c.close();
            } catch (Exception ignored) {
                // best effort at shutdown
            }
        }), "playwright-shutdown"));
    }

    private BrowserManager() {}

    public static Playwright playwright() {
        Playwright pw = PLAYWRIGHT.get();
        if (pw == null) {
            pw = Playwright.create();
            // getByTestId() will match data-testid (default). Set once per Playwright instance; a team
            // could standardise on e.g. data-qa: pw.selectors().setTestIdAttribute("data-qa");
            pw.selectors().setTestIdAttribute("data-testid");
            PLAYWRIGHT.set(pw);
            ALL.add(pw);
        }
        return pw;
    }

    public static Browser browser() {
        Browser b = BROWSER.get();
        if (b == null || !b.isConnected()) {
            b = launch();
            BROWSER.set(b);
        }
        return b;
    }

    private static Browser launch() {
        FrameworkConfig cfg = FrameworkConfig.get();
        BrowserType type = switch (cfg.browser()) {
            case "firefox" -> playwright().firefox();
            case "webkit" -> playwright().webkit();
            default -> playwright().chromium();
        };
        BrowserType.LaunchOptions opts = new BrowserType.LaunchOptions()
                .setHeadless(cfg.headless())
                .setSlowMo(Double.parseDouble(cfg.get("slow.mo.ms", "0")));
        String channel = cfg.get("browser.channel");
        if (channel != null && !channel.isBlank()) opts.setChannel(channel);
        String exe = cfg.get("browser.executable.path");
        if (exe != null && !exe.isBlank()) opts.setExecutablePath(Path.of(exe));
        Browser browser = type.launch(opts);
        log.info("Launched {} {} (headless={}) on thread {}", type.name(), browser.version(), cfg.headless(),
                Thread.currentThread().getName());
        ALL.add(browser);
        return browser;
    }

    /**
     * New isolated context. Deterministic environment settings (locale, timezone, viewport) remove a
     * whole class of "works on my machine" flakiness around dates and number formatting.
     */
    public static BrowserContext newContext(Path storageState) {
        FrameworkConfig cfg = FrameworkConfig.get();
        Browser.NewContextOptions opts = new Browser.NewContextOptions()
                .setBaseURL(AppUnderTest.baseUrl())
                .setLocale(cfg.get("locale", "en-GB"))
                .setTimezoneId(cfg.get("timezone", "Europe/London"))
                .setViewportSize(cfg.getInt("viewport.width"), cfg.getInt("viewport.height"))
                .setColorScheme(ColorScheme.LIGHT)
                .setAcceptDownloads(true);
        if (storageState != null) opts.setStorageStatePath(storageState);
        if (cfg.getBool("video.on.failure")) {
            opts.setRecordVideoDir(cfg.artifactsDir().resolve("videos"));
        }
        BrowserContext ctx = browser().newContext(opts);
        ctx.setDefaultTimeout(cfg.actionTimeout());
        ctx.setDefaultNavigationTimeout(cfg.navigationTimeout());
        return ctx;
    }
}
