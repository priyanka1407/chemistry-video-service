package com.bank.qa.pages;

import com.bank.qa.components.DataTable;
import com.microsoft.playwright.Locator;
import com.microsoft.playwright.Page;
import com.microsoft.playwright.options.AriaRole;

public final class DashboardPage extends BasePage {

    public DashboardPage(Page page) {
        super(page);
    }

    public DashboardPage open() {
        page.navigate("/dashboard.html");
        return this;
    }

    public Locator welcome() {
        return page.getByRole(AriaRole.HEADING, new Page.GetByRoleOptions().setName(java.util.regex.Pattern.compile("^Welcome, ")));
    }

    public Locator accountCards() {
        return page.getByTestId("account-card");
    }

    /** Card located by its accessible name (aria-label), then a child test id - chained locators. */
    public Locator balanceOf(String accountName) {
        return page.getByRole(AriaRole.ARTICLE, new Page.GetByRoleOptions().setName(accountName)).getByTestId("account-balance");
    }

    public DataTable payments() {
        return new DataTable(page, "Recent payments");
    }

    public void filterByStatus(String status) {
        page.getByLabel("Filter by status").selectOption(status);
    }
}
