package com.bank.qa.api;

import com.bank.qa.data.PiiMasker;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.microsoft.playwright.APIRequestContext;
import com.microsoft.playwright.APIResponse;
import com.microsoft.playwright.options.RequestOptions;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import java.io.IOException;
import java.util.Map;
import java.util.UUID;

/**
 * Thin, typed wrapper over Playwright's {@link APIRequestContext}.
 *
 * <h2>API VALIDATION with Playwright</h2>
 * <ul>
 *   <li>{@code browserContext.request()} shares the cookie jar with the browser context: an API call made
 *       with it is authenticated as the logged-in user and cookies it receives flow back to the pages.
 *       Perfect for HYBRID tests (arrange via API, assert via UI, or vice-versa).</li>
 *   <li>{@code playwright.request().newContext(...)} is a standalone HTTP client - used for pure API
 *       suites and for the programmatic login in {@code AuthStateManager}.</li>
 *   <li>Responses are validated for status, headers, JSON schema (contract) and business values.</li>
 * </ul>
 * Arranging data via API instead of clicking through the UI is ~10-50x faster and removes UI flakiness
 * from the setup of tests whose purpose is not the UI.
 */
public final class BankApiClient {

    private static final Logger log = LoggerFactory.getLogger(BankApiClient.class);
    private static final ObjectMapper JSON = new ObjectMapper();

    private final APIRequestContext request;

    public BankApiClient(APIRequestContext request) {
        this.request = request;
    }

    public record Response(int status, JsonNode body, Map<String, String> headers) {
        public String text(String field) {
            return body.path(field).asText();
        }

        public String errorCode() {
            return body.path("error").path("code").asText();
        }
    }

    public Response createPayment(Map<String, Object> payment) {
        return createPayment(payment, UUID.randomUUID().toString());
    }

    public Response createPayment(Map<String, Object> payment, String idempotencyKey) {
        return exec("POST", "/api/payments", RequestOptions.create().setData(payment)
                .setHeader("Idempotency-Key", idempotencyKey));
    }

    public Response getPayment(String paymentId) {
        return exec("GET", "/api/payments/" + paymentId, null);
    }

    public Response approvePayment(String paymentId) {
        return exec("POST", "/api/payments/" + paymentId + "/approve", null);
    }

    public Response rejectPayment(String paymentId) {
        return exec("POST", "/api/payments/" + paymentId + "/reject", null);
    }

    public Response authorizeCard(Map<String, Object> body) {
        return exec("POST", "/api/cards/authorize", RequestOptions.create().setData(body));
    }

    public Response settlements() {
        return exec("GET", "/api/settlements", null);
    }

    public Response settlement(String batchId) {
        return exec("GET", "/api/settlements/" + batchId, null);
    }

    public Response closeSettlement(String batchId) {
        return exec("POST", "/api/settlements/" + batchId + "/close", null);
    }

    public Response me() {
        return exec("GET", "/api/me", null);
    }

    /** Generic call - used by RBAC matrix tests to hit arbitrary endpoints. */
    public Response call(String method, String path, Object body) {
        return exec(method, path, body == null ? null : RequestOptions.create().setData(body));
    }

    private Response exec(String method, String path, RequestOptions options) {
        RequestOptions opts = options == null ? RequestOptions.create() : options;
        APIResponse res = request.fetch(path, opts.setMethod(method));
        String text = res.text();
        log.debug("{} {} -> {} {}", method, path, res.status(), PiiMasker.mask(text));
        try {
            JsonNode body = text == null || text.isBlank() ? JSON.createObjectNode() : JSON.readTree(text);
            return new Response(res.status(), body, res.headers());
        } catch (IOException e) {
            throw new IllegalStateException(method + " " + path + " returned non-JSON: " + res.status(), e);
        }
    }
}
