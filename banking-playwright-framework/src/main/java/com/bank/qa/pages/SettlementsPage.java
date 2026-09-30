package com.bank.qa.pages;

import com.bank.qa.components.DataTable;
import com.microsoft.playwright.Download;
import com.microsoft.playwright.Locator;
import com.microsoft.playwright.Page;
import com.microsoft.playwright.options.AriaRole;

/**
 * Settlement batches: live-refreshing table, links opening a NEW TAB, and a file DOWNLOAD.
 *
 * <h2>Multi-tab handling</h2>
 * A {@code target=_blank} link creates a new {@link Page} in the same BrowserContext (shares cookies /
 * session). Pattern: register the wait BEFORE the action that triggers it, otherwise the event may fire
 * before you start listening (race):
 * <pre>
 *   Page tab = page.context().waitForPage(() -> link.click());   // or page.waitForPopup(...)
 *   tab.waitForLoadState();
 * </pre>
 * {@code context.pages()} lists all open tabs; {@code tab.bringToFront()} switches focus; close tabs you
 * opened so later steps don't accidentally act on them.
 */
public final class SettlementsPage extends BasePage {

    public SettlementsPage(Page page) {
        super(page);
    }

    public SettlementsPage open() {
        page.navigate("/settlements.html");
        return this;
    }

    public DataTable table() {
        return new DataTable(page, "Settlement batches");
    }

    /** CSS attribute selector on a stable business key - fine when scoped and meaningful. */
    public Locator row(String batchId) {
        return page.locator("tr[data-batch-id='" + batchId + "']");
    }

    public Locator status(String batchId) {
        return row(batchId).getByTestId("status-badge");
    }

    public SettlementDetailPage openDetailsInNewTab(String batchId) {
        Page tab = page.context().waitForPage(() ->
                row(batchId).getByRole(AriaRole.LINK, new Locator.GetByRoleOptions().setName("View details")).click());
        tab.waitForLoadState();
        return new SettlementDetailPage(tab);
    }

    /** Download: wait for the download event while clicking, then persist it to a known path. */
    public Download downloadCsv(String batchId) {
        return page.waitForDownload(() ->
                row(batchId).getByRole(AriaRole.LINK, new Locator.GetByRoleOptions().setName("Download CSV")).click());
    }

    public void closeBatch(String batchId) {
        row(batchId).getByRole(AriaRole.BUTTON, new Locator.GetByRoleOptions().setName("Close batch")).click();
    }
}
