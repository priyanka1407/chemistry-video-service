package com.bank.qa.pages;

import com.bank.qa.components.DataTable;
import com.microsoft.playwright.Locator;
import com.microsoft.playwright.Page;
import com.microsoft.playwright.options.AriaRole;

public final class SettlementDetailPage extends BasePage {

    public SettlementDetailPage(Page page) {
        super(page);
    }

    public Locator title() {
        return page.getByRole(AriaRole.HEADING, new Page.GetByRoleOptions().setLevel(1));
    }

    public Locator reconciliation() {
        return page.getByTestId("reconciliation");
    }

    public DataTable payments() {
        return new DataTable(page, "Settled payments");
    }

    public void close() {
        page.close();
    }
}
