package com.bank.qa.components;

import com.microsoft.playwright.Locator;
import com.microsoft.playwright.Page;
import com.microsoft.playwright.options.AriaRole;

import java.util.List;
import java.util.regex.Pattern;

import static com.microsoft.playwright.assertions.PlaywrightAssertions.assertThat;

/**
 * DYNAMIC DROPDOWN - an ARIA combobox whose options are fetched asynchronously as the user types
 * (debounced search: type -> 250 ms debounce -> XHR with 250-700 ms latency -> options rendered with
 * random ids).
 *
 * <p>Why this is hard in Selenium and easy in Playwright:
 * <ul>
 *   <li>Options do not exist until the XHR completes. {@code option.click()} auto-waits for the option
 *       to be attached, visible and stable - no explicit wait needed.</li>
 *   <li>Options have RANDOM ids ({@code opt-k3j9x2}) - so we locate by ROLE + accessible NAME, which is
 *       stable because it is what the user sees.</li>
 *   <li>Several options may match "Acme" - Playwright locators are <b>strict</b>: an action on a locator
 *       that resolves to 2+ elements throws "strict mode violation" instead of silently clicking the
 *       first one. That forces precise locators (regex anchors, exact names, {@code filter()}).</li>
 *   <li>A "Searching…" row exists transiently; we wait for real {@code role=option} elements, and never
 *       count options before the result settled (use {@code assertThat(options).hasCount(n)} which retries).</li>
 *   <li>{@code fill()} sets the value in one go and fires a single input event; {@code pressSequentially}
 *       types key by key (needed for widgets that react to keydown/keyup, e.g. masked inputs).</li>
 * </ul>
 */
public final class Combobox {

    private final Page page;
    private final Locator input;

    public Combobox(Page page, String accessibleName) {
        this.page = page;
        this.input = page.getByRole(AriaRole.COMBOBOX, new Page.GetByRoleOptions().setName(accessibleName));
    }

    public Locator input() {
        return input;
    }

    /** The listbox this combobox controls (resolved through aria-controls - robust against id changes). */
    public Locator listbox() {
        return page.locator("#" + input.getAttribute("aria-controls"));
    }

    public Locator options() {
        return listbox().getByRole(AriaRole.OPTION);
    }

    /** Type a search term and pick the option whose accessible name starts with {@code optionName}. */
    public void search(String term) {
        input.fill(term);
        assertThat(input).hasAttribute("aria-expanded", "true");
    }

    public void select(String term, String optionName) {
        search(term);
        // ^ anchor: "Acme Supplies Ltd" must not also match "Acme Supplies Ltd (Old)". The option's
        // accessible name also contains the masked IBAN, hence a "starts with" regex.
        Locator option = listbox().getByRole(AriaRole.OPTION,
                new Locator.GetByRoleOptions().setName(TextPatterns.startsWith(optionName)));
        option.click();
        assertThat(input).hasValue(optionName);
        assertThat(input).hasAttribute("aria-expanded", "false");
    }

    /** Keyboard path (accessibility requirement): ArrowDown n times, then Enter. */
    public void selectByKeyboard(String term, int index) {
        search(term);
        assertThat(options().first()).isVisible();
        for (int i = 0; i <= index; i++) input.press("ArrowDown");
        input.press("Enter");
    }

    public List<String> optionNames(String term) {
        search(term);
        assertThat(options().first()).isVisible(); // wait until results rendered (not "Searching…")
        return options().allInnerTexts();
    }
}
