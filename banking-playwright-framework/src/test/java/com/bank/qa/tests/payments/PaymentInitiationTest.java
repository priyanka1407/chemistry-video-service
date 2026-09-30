package com.bank.qa.tests.payments;

import com.bank.qa.api.BankApiClient;
import com.bank.qa.api.SchemaValidator;
import com.bank.qa.auth.Role;
import com.bank.qa.config.FrameworkConfig;
import com.bank.qa.data.SyntheticDataFactory;
import com.bank.qa.extensions.AsRole;
import com.bank.qa.extensions.PlaywrightTest;
import com.bank.qa.mock.NetworkMocks;
import com.bank.qa.pages.DashboardPage;
import com.bank.qa.pages.PaymentPage;
import com.bank.qa.pages.PaymentPage.PaymentForm;
import com.microsoft.playwright.Locator;
import com.microsoft.playwright.Page;
import com.microsoft.playwright.Request;
import com.microsoft.playwright.options.AriaRole;
import org.junit.jupiter.api.Tag;
import org.junit.jupiter.api.Test;

import java.util.List;
import java.util.regex.Pattern;

import static com.microsoft.playwright.assertions.PlaywrightAssertions.assertThat;
import static org.assertj.core.api.Assertions.assertThat;

/**
 * User story PAY-101: "As a MAKER I can initiate a domestic payment from a company account to a saved
 * beneficiary; payments up to 10,000.00 are released automatically and settle asynchronously."
 */
@PlaywrightTest
@AsRole(Role.MAKER)
@Tag("payments")
class PaymentInitiationTest {

    @Test
    @Tag("smoke")
    void maker_pays_a_beneficiary_end_to_end_and_payment_settles(Page page, SyntheticDataFactory data, BankApiClient api) {
        String reference = data.uniqueReference();
        PaymentPage payments = new PaymentPage(page).open();
        payments.fill(new PaymentForm("Operating Account", "north", "Northwind Traders", "GBP", "1250.75", reference, data.today()));
        assertThat(payments.selectedBeneficiary()).containsText("NatWest");
        assertThat(payments.selectedBeneficiary()).containsText("••••"); // IBAN masked in the UI

        payments.review();
        assertThat(payments.confirmDialog().getByTestId("confirm-amount")).hasText("1250.75 GBP");
        assertThat(payments.confirmDialog().getByTestId("confirm-beneficiary")).hasText("Northwind Traders");
        payments.confirm();

        assertThat(payments.paymentId()).hasText(Pattern.compile("^PAY-"));
        String paymentId = payments.paymentId().textContent();

        // ASYNC: the UI polls the settlement queue; the web-first assertion waits (up to the settlement SLA)
        // for the badge to reach SETTLED - no sleeps, returns as soon as it happens.
        assertThat(payments.status()).hasText("SETTLED",
                new com.microsoft.playwright.assertions.LocatorAssertions.HasTextOptions()
                        .setTimeout(FrameworkConfig.get().settlementTimeout()));

        // HYBRID check through the API (same session cookies as the page): persisted exactly as entered.
        BankApiClient.Response persisted = api.getPayment(paymentId);
        SchemaValidator.assertValid("payment.schema.json", persisted.body());
        assertThat(persisted.text("amount")).isEqualTo("1250.75");
        assertThat(persisted.text("reference")).isEqualTo(reference);
        assertThat(persisted.text("createdBy")).isEqualTo("maker.user");
        assertThat(persisted.body().path("statusHistory")).extracting(n -> n.path("status").asText())
                .containsExactly("PENDING_SETTLEMENT", "PROCESSING", "SETTLED");

        // And visible on the dashboard, found by business key (reference), not by row position.
        DashboardPage dashboard = new DashboardPage(page).open();
        assertThat(dashboard.payments().row(reference)).containsText("£1,250.75");
    }

    @Test
    void payment_above_threshold_is_held_for_checker_approval(Page page, SyntheticDataFactory data) {
        PaymentPage payments = new PaymentPage(page).open();
        payments.submit(new PaymentForm("Payroll Account", "bright", "Brightwater Utilities", "GBP", "10000.01", data.uniqueReference(), null));
        assertThat(payments.status()).hasText("PENDING APPROVAL");
        assertThat(payments.approvalNote()).isVisible();
    }

    @Test
    void dynamic_dropdown_async_results_no_match_and_keyboard_selection(Page page) {
        PaymentPage payments = new PaymentPage(page).open();

        // Several matches: option list is rendered asynchronously - hasCount retries until results arrive.
        payments.beneficiary().search("Acme");
        assertThat(payments.beneficiary().options()).hasCount(2);
        assertThat(payments.beneficiary().options()).hasText(new Pattern[]{
                Pattern.compile("^Acme Supplies Ltd"), Pattern.compile("^Acme Logistics PLC")});

        // No match: an informative empty state, not a stale list from the previous search.
        payments.beneficiary().search("zzzz");
        assertThat(payments.beneficiary().listbox()).containsText("No matching beneficiaries");
        assertThat(payments.beneficiary().options()).hasCount(0);

        // Keyboard only (accessibility): ArrowDown x2 + Enter picks the second option.
        payments.beneficiary().selectByKeyboard("Acme", 1);
        assertThat(payments.beneficiary().input()).hasValue("Acme Logistics PLC");
        assertThat(payments.selectedBeneficiary()).containsText("HSBC UK");

        // Native <select> populated asynchronously: options are present only after /api/accounts returns.
        Locator options = payments.fromAccount().locator("option");
        assertThat(options).hasCount(4); // placeholder + 3 accounts
        payments.fromAccount().selectOption("ACC-003");                  // by value
        assertThat(payments.fromAccount()).hasValue("ACC-003");
    }

    @Test
    void review_then_confirm_is_protected_against_double_submission(Page page, SyntheticDataFactory data) {
        PaymentPage payments = new PaymentPage(page).open();
        payments.fill(new PaymentForm("Operating Account", "stark", "Stark Components", "GBP", "42.00", data.uniqueReference(), null)).review();

        List<Request> posts = NetworkMocks.recordRequests(page, Pattern.compile("/api/payments$"));
        NetworkMocks.HeldRequests held = NetworkMocks.hold(page, "**/api/payments");   // park the POST
        Locator confirm = payments.confirmDialog().getByRole(AriaRole.BUTTON,
                new Locator.GetByRoleOptions().setName("Confirm and submit"));
        confirm.click();
        held.awaitRequest();
        // While the request is in flight the button must be disabled -> a second click cannot create a duplicate.
        assertThat(confirm).isDisabled();
        held.releaseAll();

        assertThat(payments.paymentId()).hasText(Pattern.compile("^PAY-"));
        assertThat(posts).hasSize(1);
        // Defence in depth: the request carries an Idempotency-Key (server de-duplicates retries).
        assertThat(posts.get(0).headerValue("idempotency-key")).matches("[0-9a-f-]{36}");
    }
}
