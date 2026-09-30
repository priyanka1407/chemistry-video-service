package com.bank.qa.pages;

import com.bank.qa.components.DataTable;
import com.microsoft.playwright.Locator;
import com.microsoft.playwright.Page;
import com.microsoft.playwright.options.AriaRole;

import static com.microsoft.playwright.assertions.PlaywrightAssertions.assertThat;

public final class ApprovalsPage extends BasePage {

    public ApprovalsPage(Page page) {
        super(page);
    }

    public ApprovalsPage open() {
        page.navigate("/approvals.html");
        return this;
    }

    public DataTable table() {
        return new DataTable(page, "Pending approvals");
    }

    public Locator rowFor(String paymentId) {
        return table().row(paymentId);
    }

    public void approve(String paymentId) {
        // Row scoping: the page has N identical "Approve" buttons; the row filter makes it unique.
        rowFor(paymentId).getByRole(AriaRole.BUTTON, new Locator.GetByRoleOptions().setName("Approve")).click();
    }

    public void reject(String paymentId) {
        rowFor(paymentId).getByRole(AriaRole.BUTTON, new Locator.GetByRoleOptions().setName("Reject")).click();
    }

    public Locator error() {
        return page.getByTestId("approval-error");
    }

    public Locator toast() {
        return page.getByRole(AriaRole.STATUS).filter(new Locator.FilterOptions().setHasText("Payment "));
    }

    public void waitUntilListed(String paymentId) {
        // The list is loaded once; if the payment was created a moment ago, refresh until it shows up.
        // waitForResponse(predicate, action): the listener is armed BEFORE the click - no race.
        Locator refresh = page.getByRole(AriaRole.BUTTON, new Page.GetByRoleOptions().setName("Refresh"));
        for (int i = 0; i < 5 && rowFor(paymentId).count() == 0; i++) {
            page.waitForResponse(r -> r.url().contains("status=PENDING_APPROVAL"), refresh::click);
        }
        assertThat(rowFor(paymentId)).isVisible();
    }
}
