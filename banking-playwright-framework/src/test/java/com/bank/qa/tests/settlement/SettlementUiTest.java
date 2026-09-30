package com.bank.qa.tests.settlement;

import com.bank.qa.api.BankApiClient;
import com.bank.qa.auth.Role;
import com.bank.qa.config.FrameworkConfig;
import com.bank.qa.data.SyntheticDataFactory;
import com.bank.qa.extensions.AsRole;
import com.bank.qa.extensions.PlaywrightTest;
import com.bank.qa.pages.SettlementDetailPage;
import com.bank.qa.pages.SettlementsPage;
import com.bank.qa.support.PaymentPayloads;
import com.bank.qa.wait.Poller;
import com.microsoft.playwright.BrowserContext;
import com.microsoft.playwright.Download;
import com.microsoft.playwright.Page;
import com.microsoft.playwright.assertions.LocatorAssertions;
import org.junit.jupiter.api.Tag;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.parallel.ResourceLock;

import java.nio.file.Files;
import java.nio.file.Path;
import java.time.Duration;

import static com.microsoft.playwright.assertions.PlaywrightAssertions.assertThat;
import static org.assertj.core.api.Assertions.assertThat;

/**
 * Settlement UI: MULTI-TAB handling, file DOWNLOAD, and a live-refreshing table.
 *
 * <p>{@code @ResourceLock("settlement-batch")}: closing today's batch changes shared state that other
 * settlement tests observe, so JUnit serialises just these tests while everything else still runs in
 * parallel. Prefer this targeted lock over disabling parallelism globally.
 */
@PlaywrightTest
@AsRole(Role.ADMIN)
@Tag("settlement")
class SettlementUiTest {

    /** Arrange via API: a settled payment, returning its batch id. */
    private static String settledPaymentBatch(BankApiClient api, SyntheticDataFactory data, String reference) {
        String id = api.createPayment(PaymentPayloads.with(PaymentPayloads.valid(data, "321.09"), "reference", reference)).text("paymentId");
        return Poller.until("payment settled", () -> api.getPayment(id), r -> r.text("status").equals("SETTLED"),
                Duration.ofMillis(FrameworkConfig.get().settlementTimeout())).text("settlementBatchId");
    }

    @Test
    @ResourceLock("settlement-batch")
    void batch_details_open_in_a_new_tab_sharing_the_session(Page page, BrowserContext context, BankApiClient api, SyntheticDataFactory data) {
        String reference = data.uniqueReference();
        String batchId = settledPaymentBatch(api, data, reference);

        SettlementsPage settlements = new SettlementsPage(page).open();
        assertThat(settlements.row(batchId)).isVisible();

        SettlementDetailPage detail = settlements.openDetailsInNewTab(batchId);
        assertThat(context.pages()).hasSize(2);
        assertThat(detail.page()).hasTitle(batchId + " · Northbridge Bank");
        assertThat(detail.title()).hasText("Settlement batch " + batchId);
        assertThat(detail.payments().row(reference)).containsText("£321.09");
        assertThat(detail.reconciliation()).containsText("across");

        // Back to the first tab: it is still fully usable; close the popup to avoid acting on the wrong tab later.
        detail.close();
        page.bringToFront();
        assertThat(context.pages()).hasSize(1);
        assertThat(settlements.row(batchId)).isVisible();
    }

    @Test
    @ResourceLock("settlement-batch")
    void settlement_report_downloads_as_csv_containing_the_payment(Page page, BankApiClient api, SyntheticDataFactory data) throws Exception {
        String reference = data.uniqueReference();
        String batchId = settledPaymentBatch(api, data, reference);

        Download download = new SettlementsPage(page).open().downloadCsv(batchId);
        assertThat(download.suggestedFilename()).isEqualTo(batchId + ".csv");
        Path file = FrameworkConfig.get().artifactsDir().resolve("downloads").resolve(batchId + "-" + reference + ".csv");
        download.saveAs(file);

        String csv = Files.readString(file);
        assertThat(csv.lines().findFirst()).hasValue("paymentId,reference,beneficiary,amount,currency,status");
        assertThat(csv).contains(reference + ",Northwind Traders,321.09,GBP,SETTLED");
    }

    @Test
    @ResourceLock("settlement-batch")
    void admin_closes_batch_and_live_table_reflects_async_transition(Page page, BankApiClient api, SyntheticDataFactory data) {
        String batchId = settledPaymentBatch(api, data, data.uniqueReference());
        SettlementsPage settlements = new SettlementsPage(page).open();
        assertThat(settlements.status(batchId)).hasText("OPEN");

        settlements.closeBatch(batchId);
        // OPEN -> CLOSING -> CLOSED happens in the back-end; the table refreshes itself every 2 s.
        assertThat(settlements.status(batchId)).hasText("CLOSED", new LocatorAssertions.HasTextOptions().setTimeout(10_000));
        assertThat(settlements.row(batchId).getByRole(com.microsoft.playwright.options.AriaRole.BUTTON)).isDisabled();

        // A closed batch cannot be closed again (API guard, not just a disabled button).
        assertThat(api.closeSettlement(batchId).status()).isEqualTo(409);
    }
}
