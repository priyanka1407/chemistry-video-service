package com.bank.qa.pages;

import com.bank.qa.components.Combobox;
import com.bank.qa.components.TextPatterns;
import com.microsoft.playwright.Locator;
import com.microsoft.playwright.Page;
import com.microsoft.playwright.options.AriaRole;
import com.microsoft.playwright.options.SelectOption;

import java.util.regex.Pattern;

import static com.microsoft.playwright.assertions.PlaywrightAssertions.assertThat;

/**
 * New payment journey: form -> review dialog -> submit -> async status.
 */
public final class PaymentPage extends BasePage {

    /** Plain data carrier for the form - built by tests from SyntheticDataFactory / BVA cases. */
    public record PaymentForm(String fromAccount, String beneficiarySearch, String beneficiaryName,
                              String currency, String amount, String reference, String executionDate) {}

    public PaymentPage(Page page) {
        super(page);
    }

    public PaymentPage open() {
        page.navigate("/payments.html");
        assertThat(heading("New payment")).isVisible();
        // FLAKINESS LESSON - the "hydration race": the static HTML (inputs included) is visible and enabled
        // immediately, but the page's JS attaches the combobox listeners only AFTER /api/accounts returns.
        // Auto-waiting cannot detect "no listener yet" - typing earlier silently does nothing. So open()
        // waits for an app-level readiness signal: the accounts placeholder being replaced.
        assertThat(fromAccount().locator("option").first()).hasText("Select an account");
        return this;
    }

    // ---------------------------------------------------------------- locators
    public Locator fromAccount() { return page.getByLabel("Pay from"); }
    public Combobox beneficiary() { return new Combobox(page, "Beneficiary"); }
    public Locator selectedBeneficiary() { return page.getByTestId("selected-beneficiary"); }
    public Locator currency() { return page.getByLabel("Currency"); }
    public Locator amount() { return page.getByLabel("Amount"); }
    public Locator reference() { return page.getByLabel("Payment reference"); }
    public Locator executionDate() { return page.getByLabel("Payment date"); }
    public Locator reviewButton() { return page.getByRole(AriaRole.BUTTON, new Page.GetByRoleOptions().setName("Review payment")); }
    public Locator confirmDialog() { return page.getByRole(AriaRole.DIALOG, new Page.GetByRoleOptions().setName("Confirm payment")); }
    public Locator fxQuote() { return page.getByTestId("fx-quote"); }
    public Locator paymentId() { return page.getByTestId("payment-id"); }
    public Locator status() { return page.locator("#result-status").getByTestId("status-badge"); }
    public Locator approvalNote() { return page.getByText("requires approval by a checker"); }

    /** Field error linked to the input via aria-describedby (ids are stable here: "<field>-error"). */
    public Locator fieldError(String field) { return page.locator("#" + field + "-error"); }

    // ---------------------------------------------------------------- actions
    public PaymentPage fill(PaymentForm f) {
        // Native <select> with ASYNC options: selectOption waits until an option matching the label exists.
        // Label is partial (contains balance), so select by regex-free exact value is not possible from the
        // test's perspective -> we pick the option whose label starts with the account name.
        String label = fromAccount().locator("option").filter(new Locator.FilterOptions()
                .setHasText(TextPatterns.startsWith(f.fromAccount()))).first().textContent();
        fromAccount().selectOption(new SelectOption().setLabel(label));
        if (f.beneficiaryName() != null) beneficiary().select(f.beneficiarySearch(), f.beneficiaryName());
        currency().selectOption(f.currency());          // by value
        amount().fill(f.amount());
        reference().fill(f.reference());
        if (f.executionDate() != null) executionDate().fill(f.executionDate()); // <input type=date> takes yyyy-MM-dd
        return this;
    }

    public PaymentPage review() {
        reviewButton().click();
        assertThat(confirmDialog()).isVisible();
        return this;
    }

    public PaymentPage confirm() {
        confirmDialog().getByRole(AriaRole.BUTTON, new Locator.GetByRoleOptions().setName("Confirm and submit")).click();
        return this;
    }

    /** Full happy path; returns the payment id rendered after submission. */
    public String submit(PaymentForm f) {
        fill(f).review().confirm();
        assertThat(paymentId()).hasText(Pattern.compile("^PAY-[A-Z0-9]{8}$"));
        return paymentId().textContent();
    }
}
