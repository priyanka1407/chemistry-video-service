package com.bank.qa.tests.mocking;

import com.bank.qa.auth.Role;
import com.bank.qa.extensions.AsRole;
import com.bank.qa.extensions.PlaywrightTest;
import com.bank.qa.extensions.UiSession;
import com.bank.qa.mock.MockSchemaGenerator;
import com.bank.qa.mock.NetworkMocks;
import com.bank.qa.pages.PaymentPage;
import com.bank.qa.config.FrameworkConfig;
import com.bank.qa.core.BrowserManager;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.node.ObjectNode;
import com.microsoft.playwright.BrowserContext;
import com.microsoft.playwright.Page;
import com.microsoft.playwright.Request;
import com.microsoft.playwright.Route;
import com.microsoft.playwright.options.AriaRole;
import org.junit.jupiter.api.Tag;
import org.junit.jupiter.api.Test;

import java.nio.file.Files;
import java.nio.file.Path;
import java.util.Map;
import java.util.regex.Pattern;

import static com.microsoft.playwright.assertions.PlaywrightAssertions.assertThat;
import static org.assertj.core.api.Assertions.assertThat;

/**
 * NETWORK MOCKING - DEEP DIVE (read together with {@link NetworkMocks}).
 *
 * Target: the FX quote on the payment page, fetched from a THIRD-PARTY provider (/external/fx/rates) that is
 * slow, rate-limited and returns live (non-deterministic) prices - the textbook case for mocking.
 * Each test demonstrates one technique; the SUT's own payment API is left real.
 */
@PlaywrightTest
@AsRole(Role.MAKER)
@Tag("mocking")
class NetworkMockingDeepDiveTest {

    private static final String FX = "**/external/fx/rates**";

    private static PaymentPage openAndChooseEur(Page page) {
        PaymentPage payments = new PaymentPage(page).open();
        payments.currency().selectOption("EUR");
        return payments;
    }

    @Test
    @Tag("smoke")
    void fulfill_with_mock_generated_from_contract_schema(UiSession session) {
        // 1) Mock body generated from the provider's JSON schema, overriding only what the test cares about.
        //    generateValid() re-validates -> the mock can never drift from the contract.
        JsonNode fx = session.mocks().generateValid("fx-rates.schema.json",
                Map.of("/provider", "MockFX", "/rates/EUR", 1.2345));
        NetworkMocks.fulfillJson(session.context(), FX, 200, fx);

        PaymentPage payments = openAndChooseEur(session.page());
        assertThat(payments.fxQuote()).hasText("Indicative rate: 1 GBP = 1.2345 EUR (MockFX)");
    }

    @Test
    void http_error_from_provider_shows_graceful_degradation(UiSession session) {
        // 2) Failure injection: HTTP 503. The server "answered", but with an error.
        NetworkMocks.failWithStatus(session.context(), FX, 503);
        PaymentPage payments = openAndChooseEur(session.page());
        assertThat(payments.fxQuote().getByRole(AriaRole.ALERT)).containsText("Live FX rates are unavailable");
        // Degradation must not block the core journey: the form is still usable.
        assertThat(payments.reviewButton()).isEnabled();
    }

    @Test
    void network_level_failure_timeout_or_offline(UiSession session) {
        // 3) route.abort(): no HTTP response at all. Codes: aborted, accessdenied, addressunreachable,
        //    blockedbyclient, connectionrefused, connectionreset, internetdisconnected, namenotresolved, timedout, failed
        NetworkMocks.abort(session.context(), FX, "timedout");
        PaymentPage payments = openAndChooseEur(session.page());
        assertThat(payments.fxQuote()).containsText("Live FX rates are unavailable");
    }

    @Test
    void patch_the_real_response_with_route_fetch(UiSession session) {
        // 4) Hybrid: call the REAL provider via route.fetch(), then modify one field (edge case: extreme rate).
        NetworkMocks.patchJsonResponse(session.context(), FX, body -> {
            ((ObjectNode) body.get("rates")).put("EUR", 0.5);
            ((ObjectNode) body).put("provider", "FXPrime (patched)");
            return body;
        });
        PaymentPage payments = openAndChooseEur(session.page());
        assertThat(payments.fxQuote()).hasText("Indicative rate: 1 GBP = 0.5000 EUR (FXPrime (patched))");
    }

    @Test
    void hold_request_to_assert_loading_state_deterministically(UiSession session) {
        // 5) Latency without sleeps: park the request, assert the in-flight UI, then release it.
        PaymentPage payments = new PaymentPage(session.page()).open();
        NetworkMocks.HeldRequests held = NetworkMocks.hold(session.page(), FX);
        payments.currency().selectOption("USD");
        held.awaitRequest();
        assertThat(payments.fxQuote()).hasText("Fetching live rate…");
        held.releaseAll();
        assertThat(payments.fxQuote()).containsText(Pattern.compile("1 GBP = \\d\\.\\d{4} USD"));
    }

    @Test
    void handler_precedence_times_and_fallback(UiSession session) {
        BrowserContext ctx = session.context();
        Page page = session.page();
        MockSchemaGenerator gen = session.mocks();
        // 6) Context-level default mock (registered FIRST = evaluated LAST).
        ctx.route(FX, route -> route.fulfill(new Route.FulfillOptions().setContentType("application/json")
                .setBody(gen.generateValid("fx-rates.schema.json", Map.of("/provider", "ContextMock", "/rates/EUR", 1.1111)).toString())));
        // Page-level route, only for the FIRST matching request (times=1), and it defers via fallback() when
        // the query is not base=GBP -> demonstrates chaining. Page routes take precedence over context routes.
        page.route(FX, route -> {
            if (!route.request().url().contains("base=GBP")) {
                route.fallback();
                return;
            }
            route.fulfill(new Route.FulfillOptions().setContentType("application/json")
                    .setBody(gen.generateValid("fx-rates.schema.json", Map.of("/provider", "PageMockOnce", "/rates/EUR", 1.9999)).toString()));
        }, new Page.RouteOptions().setTimes(1));

        PaymentPage payments = openAndChooseEur(page);
        assertThat(payments.fxQuote()).containsText("1.9999 EUR (PageMockOnce)");

        // Second request: page handler exhausted (times=1) -> context handler answers.
        payments.currency().selectOption("GBP");
        payments.currency().selectOption("EUR");
        assertThat(payments.fxQuote()).containsText("1.1111 EUR (ContextMock)");

        // unroute removes handlers -> real provider again.
        ctx.unroute(FX);
        payments.currency().selectOption("GBP");
        payments.currency().selectOption("EUR");
        assertThat(payments.fxQuote()).containsText("(FXPrime)");
    }

    @Test
    void assert_what_the_ui_sent_request_interception_without_mocking(UiSession session) {
        // 7) Observe, don't mock: verify the exact request the UI produced (method, headers, body shape).
        Page page = session.page();
        PaymentPage payments = new PaymentPage(page).open();
        payments.fill(new PaymentPage.PaymentForm("Operating Account", "north", "Northwind Traders", "GBP", "1,234.50",
                session.data().uniqueReference(), null)).review();

        Request sent = page.waitForRequest(r -> r.url().endsWith("/api/payments") && r.method().equals("POST"),
                payments::confirm);
        assertThat(sent.headerValue("content-type")).isEqualTo("application/json");
        // UI normalised the thousands separator before sending - and money travels as a string.
        assertThat(sent.postData()).contains("\"amount\":\"1234.50\"").contains("\"beneficiaryId\":\"BEN-103\"");
        assertThat(sent.response().status()).isEqualTo(201);
    }

    @Test
    void mock_own_api_for_rare_ui_states_component_level(UiSession session) {
        // 8) Mocking the SUT's own API is acceptable for UI-state tests that are otherwise impractical
        //    (e.g. a customer with zero accounts), but keep such tests few and clearly labelled.
        session.context().route("**/api/accounts", route -> route.fulfill(new Route.FulfillOptions()
                .setContentType("application/json").setBody("[]")));
        Page page = session.page();
        page.navigate("/payments.html");
        assertThat(page.getByLabel("Pay from").locator("option")).hasCount(1);
        assertThat(page.getByLabel("Pay from")).containsText("Select an account");
    }

    @Test
    void record_and_replay_with_har(UiSession session) throws Exception {
        // 9) HAR: record the provider's responses once, replay them offline & deterministically.
        Path har = FrameworkConfig.get().artifactsDir().resolve("har").resolve("fx-" + System.nanoTime() + ".har");
        Files.createDirectories(har.getParent());

        // Record (normally done once against a real environment, then sanitised and committed).
        BrowserContext recorder = BrowserManager.newContext(com.bank.qa.auth.AuthStateManager.storageStateFor(Role.MAKER));
        NetworkMocks.replayHar(recorder, har, FX, true);
        Page rec = recorder.newPage();
        PaymentPage recorded = openAndChooseEur(rec);
        assertThat(recorded.fxQuote()).containsText("FXPrime");
        String recordedQuote = recorded.fxQuote().textContent();
        recorder.close(); // HAR is written on close
        assertThat(har).exists();

        // Replay: live rates are random per call, so an IDENTICAL quote proves the response came from the HAR.
        NetworkMocks.replayHar(session.context(), har, FX, false);
        PaymentPage replayed = openAndChooseEur(session.page());
        assertThat(replayed.fxQuote()).hasText(recordedQuote);
    }

}
