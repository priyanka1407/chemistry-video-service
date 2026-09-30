package com.bank.qa.tests.flakiness;

import com.bank.qa.api.BankApiClient;
import com.bank.qa.auth.Role;
import com.bank.qa.data.SyntheticDataFactory;
import com.bank.qa.extensions.AsRole;
import com.bank.qa.extensions.PlaywrightTest;
import com.bank.qa.pages.DashboardPage;
import com.bank.qa.support.PaymentPayloads;
import com.bank.qa.wait.Poller;
import com.microsoft.playwright.Page;
import com.microsoft.playwright.Response;
import org.junit.jupiter.api.RepeatedTest;
import org.junit.jupiter.api.Tag;
import org.junit.jupiter.api.Test;

import java.time.Duration;

import static com.microsoft.playwright.assertions.PlaywrightAssertions.assertThat;
import static org.assertj.core.api.Assertions.assertThat;

/**
 * FLAKINESS AT SCALE - patterns that keep a 1,000+ test parallel suite green. Each test shows the
 * RIGHT way; the comment shows the anti-pattern it replaces. See docs/FLAKINESS.md for the process side
 * (retries-as-signal, quarantine lane, flake-rate dashboards, ownership).
 */
@PlaywrightTest
@AsRole(Role.MAKER)
@Tag("flakiness")
class FlakinessPatternsTest {

    /**
     * Run the same test many times IN PARALLEL: the cheapest way to shake out order dependence and shared
     * data. (Locally: -Djunit.jupiter.execution.parallel.config.fixed.parallelism=8 and @RepeatedTest(50).)
     */
    @RepeatedTest(5)
    void unique_data_per_test_makes_parallel_runs_safe(Page page, BankApiClient api, SyntheticDataFactory data) {
        // ❌ reference = "TEST PAYMENT"  -> every parallel copy finds 5 rows -> strict-mode violation / wrong row
        String reference = data.uniqueReference();
        api.createPayment(PaymentPayloads.valid(data, "7.77"));
        api.createPayment(PaymentPayloads.with(PaymentPayloads.valid(data, "8.88"), "reference", reference));

        DashboardPage dashboard = new DashboardPage(page).open();
        // ❌ dashboard.payments().rows().first()   -> "first" depends on what other tests created
        assertThat(dashboard.payments().row(reference)).containsText("£8.88");
    }

    @Test
    void wait_for_the_specific_response_not_for_time_or_network_idle(Page page) {
        DashboardPage dashboard = new DashboardPage(page).open();
        // ❌ page.waitForTimeout(2000)                    -> slow AND still flaky under load
        // ❌ page.waitForLoadState(NETWORKIDLE)           -> never idle with polling/websockets; discouraged
        // ✅ tie the wait to the exact request the action triggers (listener armed BEFORE the action):
        Response filtered = page.waitForResponse(r -> r.url().contains("/api/payments?status=SETTLED"),
                () -> dashboard.filterByStatus("SETTLED"));
        assertThat(filtered.status()).isEqualTo(200);
        // ✅ then assert the DOM with a retrying web-first assertion (each visible badge now reads SETTLED)
        assertThat(dashboard.payments().root().getByTestId("status-badge")
                .filter(new com.microsoft.playwright.Locator.FilterOptions().setHasNotText("SETTLED"))).hasCount(0);
    }

    @Test
    void retry_a_block_until_it_passes_for_eventually_consistent_views(Page page, BankApiClient api, SyntheticDataFactory data) {
        // The dashboard does not live-refresh. A single reload may run before the queue settled the payment.
        String reference = data.uniqueReference();
        api.createPayment(PaymentPayloads.with(PaymentPayloads.valid(data, "5.55"), "reference", reference));
        DashboardPage dashboard = new DashboardPage(page);
        // ❌ open(); sleep(3000); open(); assert  -> guesses the latency
        // ✅ retry the whole "reload + check" block until it passes (JS: expect(...).toPass()) - bounded by the SLA.
        Poller.until("dashboard shows " + reference + " as SETTLED", () -> {
            dashboard.open();
            return dashboard.payments().row(reference).textContent();
        }, text -> text != null && text.contains("SETTLED"), Duration.ofSeconds(20));
        assertThat(dashboard.payments().row(reference)).containsText("SETTLED");
    }
}
