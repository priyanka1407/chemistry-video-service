package com.bank.qa.tests.api;

import com.bank.qa.api.BankApiClient;
import com.bank.qa.api.SchemaValidator;
import com.bank.qa.auth.Role;
import com.bank.qa.core.AppUnderTest;
import com.bank.qa.core.BrowserManager;
import com.bank.qa.data.SyntheticDataFactory;
import com.bank.qa.extensions.AsRole;
import com.bank.qa.extensions.PlaywrightTest;
import com.bank.qa.support.PaymentPayloads;
import com.microsoft.playwright.APIRequest;
import com.microsoft.playwright.APIRequestContext;
import com.microsoft.playwright.APIResponse;
import com.microsoft.playwright.options.RequestOptions;
import org.junit.jupiter.api.Tag;
import org.junit.jupiter.api.Test;

import java.util.Map;
import java.util.UUID;

import static com.microsoft.playwright.assertions.PlaywrightAssertions.assertThat;
import static org.assertj.core.api.Assertions.assertThat;

/**
 * API VALIDATION with Playwright's APIRequestContext - status codes, headers, JSON-schema contract,
 * idempotency, error envelope, and negative/security cases. No browser page is used here; the context's
 * request() simply reuses the MAKER session cookies.
 */
@PlaywrightTest
@AsRole(Role.MAKER)
@Tag("api")
class PaymentApiContractTest {

    @Test
    @Tag("smoke")
    void create_payment_returns_201_location_and_contract_valid_body(BankApiClient api, SyntheticDataFactory data) {
        Map<String, Object> request = PaymentPayloads.valid(data, "99.10");
        BankApiClient.Response res = api.createPayment(request);

        assertThat(res.status()).isEqualTo(201);
        assertThat(res.headers()).containsEntry("content-type", "application/json");
        assertThat(res.headers()).containsEntry("cache-control", "no-store");
        assertThat(res.headers().get("location")).isEqualTo("/api/payments/" + res.text("paymentId"));
        SchemaValidator.assertValid("payment.schema.json", res.body());
        // Business echo: money as STRING with exactly 2 dp - never a float (0.1 + 0.2 != 0.3).
        assertThat(res.text("amount")).isEqualTo("99.10");
        assertThat(res.text("reference")).isEqualTo(request.get("reference"));

        BankApiClient.Response fetched = api.getPayment(res.text("paymentId"));
        assertThat(fetched.status()).isEqualTo(200);
        SchemaValidator.assertValid("payment.schema.json", fetched.body());
    }

    @Test
    void idempotency_key_replays_the_original_result_instead_of_paying_twice(BankApiClient api, SyntheticDataFactory data) {
        // Network timeouts make clients retry; without idempotency a retry = a duplicate payment.
        String key = UUID.randomUUID().toString();
        Map<String, Object> request = PaymentPayloads.valid(data, "500.00");

        BankApiClient.Response first = api.createPayment(request, key);
        BankApiClient.Response retry = api.createPayment(request, key);
        assertThat(first.status()).isEqualTo(201);
        assertThat(retry.status()).isEqualTo(200);
        assertThat(retry.headers()).containsEntry("idempotent-replayed", "true");
        assertThat(retry.text("paymentId")).isEqualTo(first.text("paymentId"));

        // Same key, different body = client bug -> must be rejected, not silently replayed.
        BankApiClient.Response misuse = api.createPayment(PaymentPayloads.with(request, "amount", "501.00"), key);
        assertThat(misuse.status()).isEqualTo(409);
        assertThat(misuse.errorCode()).isEqualTo("IDEMPOTENCY_KEY_REUSED");
    }

    @Test
    void validation_errors_use_the_standard_error_envelope(BankApiClient api, SyntheticDataFactory data) {
        Map<String, Object> bad = PaymentPayloads.with(PaymentPayloads.with(PaymentPayloads.valid(data, "-1"), "currency", "XYZ"), "reference", "bad<ref>");
        BankApiClient.Response res = api.createPayment(bad);
        assertThat(res.status()).isEqualTo(422);
        SchemaValidator.assertValid("error.schema.json", res.body());
        assertThat(res.body().at("/error/fields").findValuesAsText("field")).containsExactlyInAnyOrder("amount", "currency", "reference");
    }

    @Test
    void malformed_json_and_unknown_resources(BankApiClient api, com.microsoft.playwright.BrowserContext context) {
        APIResponse malformed = context.request().post("/api/payments", RequestOptions.create()
                .setHeader("Content-Type", "application/json").setData("{not json"));
        // Playwright also ships web-first-style assertions for API responses:
        assertThat(malformed).not().isOK();
        assertThat(malformed.status()).isEqualTo(400);
        assertThat(malformed.text()).contains("MALFORMED_JSON");

        BankApiClient.Response missing = api.getPayment("PAY-DOESNOT");
        assertThat(missing.status()).isEqualTo(404);
        SchemaValidator.assertValid("error.schema.json", missing.body());
    }

    @Test
    void unauthenticated_client_gets_401_on_every_protected_endpoint() {
        // A standalone APIRequestContext = a raw HTTP client without the browser's cookies.
        APIRequestContext anonymous = BrowserManager.playwright().request().newContext(
                new APIRequest.NewContextOptions().setBaseURL(AppUnderTest.baseUrl()));
        try {
            for (String path : new String[]{"/api/me", "/api/accounts", "/api/payments", "/api/settlements"}) {
                APIResponse res = anonymous.get(path);
                assertThat(res.status()).as(path).isEqualTo(401);
            }
            assertThat(anonymous.get("/api/health")).isOK(); // public health check stays open
        } finally {
            anonymous.dispose();
        }
    }
}
