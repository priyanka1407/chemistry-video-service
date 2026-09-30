package com.bank.qa.tests.settlement;

import com.bank.qa.api.BankApiClient;
import com.bank.qa.api.SchemaValidator;
import com.bank.qa.auth.Role;
import com.bank.qa.config.FrameworkConfig;
import com.bank.qa.data.SyntheticDataFactory;
import com.bank.qa.extensions.AsRole;
import com.bank.qa.extensions.PlaywrightTest;
import com.bank.qa.support.PaymentPayloads;
import com.bank.qa.wait.Poller;
import com.fasterxml.jackson.databind.JsonNode;
import org.junit.jupiter.api.Tag;
import org.junit.jupiter.api.Test;

import java.math.BigDecimal;
import java.time.Duration;
import java.util.ArrayList;
import java.util.List;
import java.util.stream.StreamSupport;

import static org.assertj.core.api.Assertions.assertThat;

/**
 * ASYNC QUEUE PROCESSING - payments are settled by a background worker:
 * <pre>
 *   POST /payments -> PENDING_SETTLEMENT --(queue pick-up 0.3-1.2 s)--> PROCESSING --(0.5-1.5 s)--> SETTLED | FAILED
 * </pre>
 * Techniques: submit many items first and wait for them together (total time = slowest item, not the sum);
 * poll a cheap status endpoint with back-off; assert the full state history (ordering / no skipped states);
 * reconcile totals (money in == money out); never assume the batch contains ONLY your payments - other
 * tests (and in UAT other teams) share the queue.
 */
@PlaywrightTest
@AsRole(Role.MAKER)
@Tag("settlement")
class SettlementQueueTest {

    private static final List<String> TERMINAL = List.of("SETTLED", "FAILED", "REJECTED");

    private static Duration sla() {
        return Duration.ofMillis(FrameworkConfig.get().settlementTimeout());
    }

    @Test
    @Tag("smoke")
    void submitted_payments_are_processed_by_the_queue_in_order_and_reconcile(BankApiClient api, SyntheticDataFactory data) {
        List<String> ids = new ArrayList<>();
        BigDecimal expectedTotal = BigDecimal.ZERO;
        for (int i = 0; i < 5; i++) {
            BigDecimal amount = data.amountBetween(new BigDecimal("1.00"), new BigDecimal("9999.99"));
            expectedTotal = expectedTotal.add(amount);
            BankApiClient.Response res = api.createPayment(PaymentPayloads.valid(data, amount.toPlainString()));
            assertThat(res.status()).isEqualTo(201);
            assertThat(res.headers().get("location")).isEqualTo("/api/payments/" + res.text("paymentId"));
            ids.add(res.text("paymentId"));
        }

        List<JsonNode> settled = new ArrayList<>();
        for (String id : ids) { // all were enqueued up-front -> this loop waits ~max(latency), not the sum
            settled.add(Poller.until("settlement of " + id, () -> api.getPayment(id),
                    r -> TERMINAL.contains(r.text("status")), sla()).body());
        }

        BigDecimal actualTotal = BigDecimal.ZERO;
        for (JsonNode p : settled) {
            SchemaValidator.assertValid("payment.schema.json", p);
            assertThat(p.path("status").asText()).isEqualTo("SETTLED");
            assertThat(StreamSupport.stream(p.path("statusHistory").spliterator(), false).map(h -> h.path("status").asText()))
                    .containsExactly("PENDING_SETTLEMENT", "PROCESSING", "SETTLED");
            actualTotal = actualTotal.add(new BigDecimal(p.path("amount").asText()));

            // The batch must contain the payment (it may contain other tests' payments as well).
            BankApiClient.Response batch = api.settlement(p.path("settlementBatchId").asText());
            SchemaValidator.assertValid("settlement-batch.schema.json", batch.body());
            assertThat(batch.body().path("payments").findValuesAsText("paymentId")).contains(p.path("paymentId").asText());
        }
        assertThat(actualTotal).as("reconciliation").isEqualByComparingTo(expectedTotal);
    }

    @Test
    void settlement_engine_failure_is_surfaced_not_swallowed(BankApiClient api, SyntheticDataFactory data) {
        // The sample engine fails references containing FAIL (a deterministic "poison message").
        String id = api.createPayment(PaymentPayloads.with(PaymentPayloads.valid(data, "10.00"), "reference", "QA-FAIL-" + data.random().nextInt(999)))
                .text("paymentId");
        BankApiClient.Response res = Poller.until("terminal state of " + id, () -> api.getPayment(id),
                r -> TERMINAL.contains(r.text("status")), sla());
        assertThat(res.text("status")).isEqualTo("FAILED");
        assertThat(res.body().path("settlementBatchId").isNull()).isTrue();
    }

    @Test
    void payments_awaiting_approval_are_not_picked_up_by_the_queue(BankApiClient api, SyntheticDataFactory data) {
        String id = api.createPayment(PaymentPayloads.valid(data, "20000.00")).text("paymentId");
        // Negative async assertion: "stays the same for a while". Bounded, deliberately short observation window -
        // proving a negative always costs wall time, so keep these few and cheap.
        long end = System.currentTimeMillis() + 2500;
        while (System.currentTimeMillis() < end) {
            assertThat(api.getPayment(id).text("status")).isEqualTo("PENDING_APPROVAL");
        }
    }
}
