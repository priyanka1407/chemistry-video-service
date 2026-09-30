package com.bank.qa.mock;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.microsoft.playwright.APIResponse;
import com.microsoft.playwright.BrowserContext;
import com.microsoft.playwright.Page;
import com.microsoft.playwright.Request;
import com.microsoft.playwright.Route;
import com.microsoft.playwright.options.HarMode;

import java.nio.file.Path;
import java.util.ArrayList;
import java.util.Collections;
import java.util.List;
import java.util.Map;
import java.util.concurrent.ConcurrentLinkedQueue;
import java.util.function.UnaryOperator;
import java.util.regex.Pattern;

/**
 * NETWORK MOCKING - technical deep dive (read top to bottom).
 *
 * <h2>1. How interception works</h2>
 * {@code page.route(url, handler)} / {@code context.route(url, handler)} install an interceptor at the
 * browser's network layer (CDP Fetch domain in Chromium, equivalent hooks in Firefox/WebKit). Every
 * matching request is PAUSED and handed to your handler, which MUST end it with exactly one of:
 * <pre>
 *   route.fulfill(...)   answer with a synthetic response - the server is never contacted
 *   route.resume(...)    send to the real server (optionally with modified url/method/headers/body)
 *   route.abort(code)    fail at network level: "failed", "timedout", "connectionrefused", "internetdisconnected" ...
 *   route.fallback(...)  pass to the next matching handler (handler chaining)
 *   route.fetch()        perform the real request yourself, get an APIResponse, then fulfill with a modified copy
 * </pre>
 * Forgetting to call one of them leaves the request hanging -> the page waits forever (a classic bug).
 *
 * <h2>2. Matching</h2>
 * glob ({@code "**&#47;external/fx/**"}), {@link Pattern} regex, or a {@code Predicate<String>} on the URL.
 * Glob: {@code *} = any chars except '/', {@code **} = any chars incl. '/', {@code ?} is a LITERAL '?'
 * (use a regex to match query strings flexibly).
 *
 * <h2>3. Precedence &amp; scope</h2>
 * <ul>
 *   <li>Handlers are evaluated <b>last-registered-first</b>. A later, more specific handler can
 *       {@code fallback()} to an earlier general one.</li>
 *   <li><b>Page routes win over context routes.</b> Context routes also cover popups/new tabs and
 *       requests made before a page exists - use them for "global" mocks (see {@link #blockTrackers}).</li>
 *   <li>{@code RouteOptions.setTimes(n)} auto-removes a handler after n matches; {@code unroute()} removes it.</li>
 *   <li>Service workers can serve requests without the network -> set
 *       {@code NewContextOptions.setServiceWorkers(BLOCK)} when mocking PWA-style apps.</li>
 *   <li>{@code context.request()} / APIRequestContext traffic is NOT routed - only browser traffic.</li>
 * </ul>
 *
 * <h2>4. Java threading caveat</h2>
 * Playwright Java is single-threaded per Playwright instance: handlers run on the test thread while it is
 * inside a Playwright call. Never {@code Thread.sleep} inside a handler to "simulate latency" - it blocks
 * the whole event loop. Use {@link #hold} (deterministic: park the request, assert the loading state,
 * then release) instead of time-based delays.
 *
 * <h2>5. When to mock (banking context)</h2>
 * Mock what you DON'T own and can't control (FX provider, credit bureau, card network, SMS gateway),
 * error paths that are hard to trigger (503, timeouts, malformed payloads), and time-sensitive data.
 * Do NOT mock the system under test's own core APIs in E2E suites - that turns E2E into a UI unit test.
 * Keep mocks contract-safe via {@link MockSchemaGenerator} + schema validation.
 */
public final class NetworkMocks {

    private static final ObjectMapper JSON = new ObjectMapper();

    private NetworkMocks() {}

    // ------------------------------------------------------------------ fulfill

    /** Stub any matching request with a JSON body. Works for fetch/XHR, including cross-origin (adds CORS). */
    public static void fulfillJson(BrowserContext ctx, String glob, int status, JsonNode body) {
        ctx.route(glob, route -> route.fulfill(new Route.FulfillOptions()
                .setStatus(status)
                .setContentType("application/json")
                .setHeaders(Map.of("Access-Control-Allow-Origin", "*", "X-Mocked-By", "playwright"))
                .setBody(body.toString())));
    }

    // ------------------------------------------------------------------ modify real response

    /**
     * "Patch" pattern: perform the REAL request with {@code route.fetch()}, change a field, return it.
     * Keeps realism (real headers, cookies, most of the body) while forcing one edge case
     * (e.g. an FX rate of 0, a status the back-end rarely returns).
     */
    public static void patchJsonResponse(BrowserContext ctx, String glob, UnaryOperator<JsonNode> patch) {
        ctx.route(glob, route -> {
            APIResponse real = route.fetch();
            try {
                JsonNode patched = patch.apply(JSON.readTree(real.text()));
                route.fulfill(new Route.FulfillOptions().setResponse(real).setBody(patched.toString()));
            } catch (Exception e) {
                route.abort("failed");
                throw new IllegalStateException("Could not patch response of " + route.request().url(), e);
            }
        });
    }

    // ------------------------------------------------------------------ failure injection

    /** HTTP-level failure (server answered with an error). */
    public static void failWithStatus(BrowserContext ctx, String glob, int status) {
        ctx.route(glob, route -> route.fulfill(new Route.FulfillOptions().setStatus(status)
                .setContentType("application/json")
                .setBody("{\"error\":{\"code\":\"UPSTREAM_UNAVAILABLE\",\"message\":\"Injected by test\"}}")));
    }

    /** Network-level failure (no HTTP response at all): DNS failure, reset, timeout ... */
    public static void abort(BrowserContext ctx, String glob, String errorCode) {
        ctx.route(glob, route -> route.abort(errorCode));
    }

    // ------------------------------------------------------------------ request modification

    /** Continue to the real server with an extra header (feature flags, correlation ids, test markers). */
    public static void addRequestHeader(BrowserContext ctx, String glob, String name, String value) {
        ctx.route(glob, route -> {
            Map<String, String> headers = new java.util.HashMap<>(route.request().headers());
            headers.put(name, value);
            route.resume(new Route.ResumeOptions().setHeaders(headers));
        });
    }

    // ------------------------------------------------------------------ deterministic latency

    /** A request parked by {@link #hold}; the test decides when it proceeds. */
    public static final class HeldRequests {
        private final Page page;
        private final ConcurrentLinkedQueue<Route> parked = new ConcurrentLinkedQueue<>();

        private HeldRequests(Page page) {
            this.page = page;
        }

        /** Pumps Playwright's event loop until at least one matching request is parked. */
        public HeldRequests awaitRequest() {
            page.waitForCondition(() -> !parked.isEmpty());
            return this;
        }

        /** Lets all parked requests continue to the real server. */
        public void releaseAll() {
            Route r;
            while ((r = parked.poll()) != null) r.resume();
        }

        /** Answers all parked requests with a failure instead. */
        public void abortAll(String errorCode) {
            Route r;
            while ((r = parked.poll()) != null) r.abort(errorCode);
        }
    }

    /**
     * Parks matching requests instead of sleeping. Lets a test observe loading spinners, disabled
     * buttons and double-submit protection with ZERO timing assumptions.
     */
    public static HeldRequests hold(Page page, String glob) {
        HeldRequests held = new HeldRequests(page);
        page.route(glob, held.parked::add);
        return held;
    }

    // ------------------------------------------------------------------ observation

    /** Records every request whose URL matches - useful to assert what the UI SENT (payload, headers). */
    public static List<Request> recordRequests(Page page, Pattern urlPattern) {
        List<Request> seen = Collections.synchronizedList(new ArrayList<>());
        page.onRequest(r -> {
            if (urlPattern.matcher(r.url()).find()) seen.add(r);
        });
        return seen;
    }

    /** Context-wide: abort analytics/trackers and web fonts - faster, fewer third-party flakes. */
    public static void blockTrackers(BrowserContext ctx) {
        ctx.route(Pattern.compile("(google-analytics|googletagmanager|doubleclick|hotjar|segment)\\."),
                route -> route.abort("blockedbyclient"));
    }

    // ------------------------------------------------------------------ HAR record / replay

    /**
     * Replays responses from a HAR file (record once with {@code update=true} against a real env,
     * commit the sanitised HAR, replay in CI). Unmatched requests fall through to the network
     * ({@code setNotFound(FALLBACK)}) or fail ({@code ABORT}) depending on how strict you want to be.
     * HARs may capture PII/tokens - scrub them before committing.
     */
    public static void replayHar(BrowserContext ctx, Path har, String urlGlob, boolean update) {
        ctx.routeFromHAR(har, new BrowserContext.RouteFromHAROptions()
                .setUrl(urlGlob)
                .setUpdate(update)
                .setNotFound(com.microsoft.playwright.options.HarNotFound.FALLBACK)
                .setUpdateMode(HarMode.MINIMAL));
    }
}
