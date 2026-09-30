package com.bank.qa.tests.payments;

import com.bank.qa.api.BankApiClient;
import com.bank.qa.auth.Role;
import com.bank.qa.config.FrameworkConfig;
import com.bank.qa.data.SyntheticDataFactory;
import com.bank.qa.extensions.AsRole;
import com.bank.qa.extensions.PlaywrightTest;
import com.bank.qa.extensions.UiSession;
import com.bank.qa.pages.ApprovalsPage;
import com.bank.qa.support.PaymentPayloads;
import com.bank.qa.wait.Poller;
import com.microsoft.playwright.Page;
import org.junit.jupiter.api.Tag;
import org.junit.jupiter.api.Test;

import java.time.Duration;

import static com.microsoft.playwright.assertions.PlaywrightAssertions.assertThat;
import static org.assertj.core.api.Assertions.assertThat;

/**
 * MAKER-CHECKER (four-eyes principle) - two different users collaborate inside ONE test, each in its own
 * isolated BrowserContext (separate cookie jars = separate sessions), like two people on two laptops.
 * Arrange via API (fast), act via UI (what we're testing), assert via API + UI.
 */
@PlaywrightTest
@AsRole(Role.MAKER)
@Tag("payments")
class MakerCheckerApprovalTest {

    @Test
    @Tag("smoke")
    void checker_approves_high_value_payment_which_then_settles(UiSession session, BankApiClient makerApi, SyntheticDataFactory data) {
        // Arrange (maker, API)
        BankApiClient.Response created = makerApi.createPayment(PaymentPayloads.valid(data, "25000.00"));
        assertThat(created.text("status")).isEqualTo("PENDING_APPROVAL");
        String paymentId = created.text("paymentId");

        // Act (checker, UI, separate context)
        Page checkerPage = session.openAs(Role.CHECKER);
        ApprovalsPage approvals = new ApprovalsPage(checkerPage).open();
        approvals.waitUntilListed(paymentId);
        assertThat(approvals.rowFor(paymentId)).containsText("maker.user");
        approvals.approve(paymentId);
        assertThat(approvals.toast()).hasText("Payment " + paymentId + " approved");
        assertThat(approvals.rowFor(paymentId)).hasCount(0);

        // Assert (maker's view, API) - async settlement, polled with back-off up to the SLA.
        BankApiClient.Response settled = Poller.until("payment " + paymentId + " settled",
                () -> makerApi.getPayment(paymentId),
                r -> r.text("status").equals("SETTLED"),
                Duration.ofMillis(FrameworkConfig.get().settlementTimeout()));
        assertThat(settled.text("approvedBy")).isEqualTo("checker.user");
        assertThat(settled.text("settlementBatchId")).matches("STL-\\d{8}-\\d{3}");
    }

    @Test
    void checker_can_reject(UiSession session, BankApiClient makerApi, SyntheticDataFactory data) {
        String paymentId = makerApi.createPayment(PaymentPayloads.valid(data, "15000.00")).text("paymentId");
        ApprovalsPage approvals = new ApprovalsPage(session.openAs(Role.CHECKER)).open();
        approvals.waitUntilListed(paymentId);
        approvals.reject(paymentId);
        assertThat(approvals.toast()).hasText("Payment " + paymentId + " rejected");
        assertThat(makerApi.getPayment(paymentId).text("status")).isEqualTo("REJECTED");
    }

    @Test
    void initiator_cannot_approve_own_payment_even_with_approve_permission(UiSession session, SyntheticDataFactory data) {
        // ADMIN holds both CREATE and APPROVE - segregation of duties must still be enforced per payment.
        BankApiClient adminApi = session.apiAs(Role.ADMIN);
        String paymentId = adminApi.createPayment(PaymentPayloads.valid(data, "30000.00")).text("paymentId");

        ApprovalsPage approvals = new ApprovalsPage(session.openAs(Role.ADMIN)).open();
        approvals.waitUntilListed(paymentId);
        approvals.approve(paymentId);
        assertThat(approvals.error()).hasText("You cannot approve a payment you initiated");
        assertThat(approvals.rowFor(paymentId)).isVisible();

        // Also enforced at API level (UI could be bypassed).
        BankApiClient.Response direct = adminApi.approvePayment(paymentId);
        assertThat(direct.status()).isEqualTo(403);
        assertThat(direct.errorCode()).isEqualTo("FOUR_EYES_VIOLATION");
    }

    @Test
    void approving_twice_is_rejected_as_invalid_state(UiSession session, BankApiClient makerApi, SyntheticDataFactory data) {
        String paymentId = makerApi.createPayment(PaymentPayloads.valid(data, "12000.00")).text("paymentId");
        BankApiClient checkerApi = session.apiAs(Role.CHECKER);
        assertThat(checkerApi.approvePayment(paymentId).status()).isEqualTo(200);
        BankApiClient.Response second = checkerApi.approvePayment(paymentId);
        assertThat(second.status()).isEqualTo(409);
        assertThat(second.errorCode()).isEqualTo("INVALID_STATE");
    }
}
