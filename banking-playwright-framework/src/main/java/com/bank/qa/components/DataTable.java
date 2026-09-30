package com.bank.qa.components;

import com.microsoft.playwright.Locator;
import com.microsoft.playwright.Page;
import com.microsoft.playwright.options.AriaRole;

import java.util.List;

/**
 * Generic accessible table helper.
 *
 * <p>Pattern: <b>find the row by business content, then act INSIDE the row</b>.
 * {@code table.getByRole(ROW).filter(hasText(ref)).getByRole(BUTTON, "Approve")} survives column
 * re-ordering, sorting, pagination and new rows being inserted by other parallel tests - unlike
 * positional locators ({@code tr:nth-child(3) td:nth-child(6) button}).
 */
public final class DataTable {

    private final Locator table;

    public DataTable(Page page, String ariaLabel) {
        this.table = page.getByRole(AriaRole.TABLE, new Page.GetByRoleOptions().setName(ariaLabel));
    }

    public Locator root() {
        return table;
    }

    /**
     * Body rows only: rows that do not contain a column header.
     * Gotcha: Playwright maps {@code <th>} to role "columnheader" only with {@code scope="col"} (or a
     * header context); a bare {@code <th>} is computed as "cell". Accessible markup = reliable locators.
     */
    public Locator rows() {
        return table.getByRole(AriaRole.ROW).filter(new Locator.FilterOptions().setHasNot(table.page().getByRole(AriaRole.COLUMNHEADER)));
    }

    /** The unique row containing the text - strictness makes an ambiguous match fail loudly. */
    public Locator row(String containsText) {
        return rows().filter(new Locator.FilterOptions().setHasText(containsText));
    }

    /** Cell of a row under the column with the given header text (header lookup = order independent). */
    public Locator cell(Locator row, String columnHeader) {
        List<String> headers = table.getByRole(AriaRole.COLUMNHEADER).allInnerTexts();
        int idx = -1;
        for (int i = 0; i < headers.size(); i++) {
            if (headers.get(i).trim().equalsIgnoreCase(columnHeader)) idx = i;
        }
        if (idx < 0) throw new IllegalArgumentException("No column '" + columnHeader + "' in " + headers);
        return row.getByRole(AriaRole.CELL).nth(idx);
    }
}
