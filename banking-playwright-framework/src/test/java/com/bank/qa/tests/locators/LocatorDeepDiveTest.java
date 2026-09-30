package com.bank.qa.tests.locators;

import com.bank.qa.components.DataTable;
import com.bank.qa.extensions.PlaywrightTest;
import com.microsoft.playwright.ElementHandle;
import com.microsoft.playwright.FrameLocator;
import com.microsoft.playwright.Locator;
import com.microsoft.playwright.Page;
import com.microsoft.playwright.PlaywrightException;
import com.microsoft.playwright.TimeoutError;
import com.microsoft.playwright.options.AriaRole;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Tag;
import org.junit.jupiter.api.Test;

import java.util.regex.Pattern;

import static com.microsoft.playwright.assertions.PlaywrightAssertions.assertThat;
import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

/**
 * LOCATORS - TECHNICAL DEEP DIVE (runs against /playground.html of the sample bank).
 *
 * <h2>What a Locator really is</h2>
 * A {@link Locator} is a lazy, serialisable QUERY (selector chain + options). Nothing is looked up when you
 * create it. Every action/assertion re-runs the query inside the browser (injected script), applies
 * STRICTNESS (exactly one match for actions) and ACTIONABILITY checks, and retries until the timeout. Hence:
 * <ul>
 *   <li>no stale-element exceptions (contrast with {@link ElementHandle} - see {@link #locators_never_go_stale_but_element_handles_do}),</li>
 *   <li>locators can be created before the element exists (page objects define them up front),</li>
 *   <li>chaining ({@code a.locator(b)}, {@code a.getByRole(..)}) narrows scope; {@code filter()} refines a set.</li>
 * </ul>
 *
 * <h2>Built-in locator engines</h2>
 * <pre>
 *  getByRole(role, {name, exact, checked, disabled, expanded, level, pressed, selected, includeHidden})
 *     -> ARIA role + accessible name (computed like a screen reader: aria-label, aria-labelledby, label[for], text)
 *  getByLabel(text)        -> form control by its <label>, aria-label or aria-labelledby
 *  getByPlaceholder(text)  getByText(text)  getByAltText(text)  getByTitle(text)
 *  getByTestId(id)         -> [data-testid=id] (attribute configurable: selectors().setTestIdAttribute)
 *  locator("css") / locator("xpath=//..") / locator("text=..") / locator("internal:role=..")
 * </pre>
 * String matching: substring + case-insensitive + whitespace-normalised by default; {@code setExact(true)} for
 * full, case-sensitive match; {@link Pattern} for regex.
 *
 * <p>Debugging tools: {@code PWDEBUG=1 mvn test ...} (Inspector, step-through, "pick locator"),
 * {@code mvn exec:java -e -D exec.mainClass=com.microsoft.playwright.CLI -D exec.args="codegen http://localhost:8085"}
 * (records actions and suggests the most resilient locator), {@code locator.highlight()}, and the Trace Viewer.
 */
@PlaywrightTest
@Tag("locators")
class LocatorDeepDiveTest {

    private Page page;

    @BeforeEach
    void open(Page page) {
        this.page = page;
        page.navigate("/playground.html");
    }

    /** A <section aria-labelledby> has the implicit ARIA role "region" - a perfect scope for chained locators. */
    private Locator section(String heading) {
        return page.getByRole(AriaRole.REGION, new Page.GetByRoleOptions().setName(heading));
    }

    @Test
    void dynamic_ids_locate_by_role_testid_or_stable_attribute_prefix() {
        Locator s = section("1. Dynamic IDs");
        // ❌ page.locator("#btn-x7k2p9")  - id changes on every render/build -> brittle.
        // ❌ page.locator(".css-1q2w3e")  - CSS-in-JS hashed class names change per build.

        Locator byRole = s.getByRole(AriaRole.BUTTON, new Locator.GetByRoleOptions().setName("Generate statement"));
        Locator byTestId = s.getByTestId("generate-statement");
        Locator byIdPrefix = s.locator("button[id^='btn-']");                        // CSS "starts with"
        Locator byXpath = s.locator("xpath=.//button[starts-with(@id,'btn-')]");     // relative XPath (note the leading '.')

        // All four resolve to the SAME element.
        for (Locator l : new Locator[]{byTestId, byIdPrefix, byXpath}) {
            assertThat(l.evaluate("(el, other) => el === other", byRole.elementHandle())).isEqualTo(true);
        }
        byRole.click();
        assertThat(s.getByRole(AriaRole.STATUS)).hasText("Statement generated");
    }

    @Test
    void shadow_dom_is_pierced_by_css_and_getBy_but_not_by_xpath() {
        // Open shadow roots are transparent to all Playwright engines EXCEPT XPath.
        assertThat(page.getByText("Available balance")).isVisible();
        assertThat(page.getByTestId("shadow-balance")).hasText("£250,000.00");
        assertThat(page.locator("bank-balance-card .amount")).hasText("£250,000.00"); // CSS descends into shadow
        assertThat(page.locator("xpath=//strong[@data-testid='shadow-balance']")).hasCount(0); // XPath cannot

        page.locator("bank-balance-card").getByRole(AriaRole.BUTTON, new Locator.GetByRoleOptions().setName("Hide balance")).click();
        assertThat(page.getByTestId("shadow-balance")).hasText("••••••");
        // Closed shadow roots (mode: 'closed') are NOT reachable by any engine - ask devs for 'open' in test builds.
    }

    @Test
    void text_matching_substring_exact_regex_and_strictness() {
        Locator s = section("3. Text variations");
        // Default getByText = case-insensitive SUBSTRING -> matches both paragraphs.
        assertThat(s.getByText("total due")).hasCount(2);
        // exact=true = whole string, case-sensitive. &nbsp; is normalised to a space, so this matches.
        assertThat(s.getByText("Total due: £1,234.56", new Locator.GetByTextOptions().setExact(true))).hasCount(1);
        // Regex for formatted money.
        assertThat(s.getByText(Pattern.compile("£\\d{1,3}(,\\d{3})*\\.\\d{2}$"))).hasCount(2);

        // Role name matching is ALSO substring by default: "Pay" matches Pay, Pay now, Pay later.
        Locator pay = s.getByRole(AriaRole.BUTTON, new Locator.GetByRoleOptions().setName("Pay"));
        assertThat(pay).hasCount(3);
        // STRICT MODE: an action on an ambiguous locator fails fast instead of clicking a random match.
        assertThatThrownBy(() -> pay.click(new Locator.ClickOptions().setTimeout(2000)))
                .isInstanceOf(PlaywrightException.class)
                .hasMessageContaining("strict mode violation");
        s.getByRole(AriaRole.BUTTON, new Locator.GetByRoleOptions().setName("Pay").setExact(true)).click(); // unique
        // Positional escape hatches exist (first/last/nth) but encode ORDER, which is fragile - avoid for actions.
        assertThat(pay.last()).hasText("Pay later");

        // Assertion-side text matching: hasText (full, normalised) vs containsText (substring).
        assertThat(s.getByTestId("total-line")).hasText("Total due: £1,234.56");
        assertThat(s.getByTestId("total-line")).containsText("1,234.56");
    }

    @Test
    void auto_waiting_for_content_that_appears_later() {
        Locator s = section("4. Delayed content");
        s.getByRole(AriaRole.BUTTON, new Locator.GetByRoleOptions().setName("Load offers")).click();
        // No sleep, no explicit wait: web-first assertions poll until true or timeout (default 7 s here).
        assertThat(s.getByRole(AriaRole.PROGRESSBAR)).isVisible();
        assertThat(s.getByRole(AriaRole.ARTICLE, new Locator.GetByRoleOptions().setName("Offer"))).containsText("0% FX fees");
        assertThat(s.getByRole(AriaRole.PROGRESSBAR)).isHidden();
        // Equivalent imperative form when you need to wait without asserting:
        s.getByRole(AriaRole.ARTICLE).waitFor();
    }

    @Test
    void tables_filter_rows_by_content_then_act_inside_the_row() {
        DataTable table = new DataTable(page, "Transactions");

        // filter(hasText) -> the row; then scoped getByRole -> the button inside that row only.
        table.row("Brightwater Utilities").getByRole(AriaRole.BUTTON, new Locator.GetByRoleOptions().setName("Dispute")).click();
        assertThat(section("5. Transactions table").getByRole(AriaRole.STATUS))
                .hasText("Dispute raised for Direct debit - Brightwater Utilities");

        // "Tesco" matches 2 rows (Tesco, Tesco Express) - be explicit.
        assertThat(table.row("Tesco")).hasCount(2);
        // Gotcha: hasText on a ROW matches the row's whole concatenated text ("2026-09-01Card payment - Tesco£54.20..."),
        // so an anchored regex like "Tesco$" matches nothing. Match the exact CELL instead:
        assertThat(table.rows().filter(new Locator.FilterOptions().setHas(page.getByRole(AriaRole.CELL,
                new Page.GetByRoleOptions().setName("Card payment - Tesco").setExact(true))))).hasCount(1);

        // filter(has: locator) - rows that CONTAIN an element; filter(hasNot / hasNotText) - exclusion.
        Locator pendingRow = table.rows().filter(new Locator.FilterOptions()
                .setHas(page.getByRole(AriaRole.CELL, new Page.GetByRoleOptions().setName("Pending").setExact(true))));
        assertThat(pendingRow.getByRole(AriaRole.BUTTON)).isDisabled();
        assertThat(table.rows().filter(new Locator.FilterOptions().setHasNotText("Tesco"))).hasCount(2);

        // Locator algebra: and() = both conditions on the same element, or() = either.
        Locator enabledDisputes = page.getByRole(AriaRole.BUTTON, new Page.GetByRoleOptions().setName("Dispute"))
                .and(page.locator(":enabled"));
        assertThat(enabledDisputes).hasCount(3);
        assertThat(table.root().getByText("Posted", new Locator.GetByTextOptions().setExact(true))
                .or(table.root().getByText("Pending", new Locator.GetByTextOptions().setExact(true)))).hasCount(4);

        // Column lookup by header -> immune to column re-ordering.
        assertThat(table.cell(table.row("Northwind"), "Amount")).hasText("£2,500.00");
    }

    @Test
    void actionability_click_waits_for_enabled_and_times_out_otherwise() {
        Locator release = page.getByRole(AriaRole.BUTTON, new Page.GetByRoleOptions().setName("Release funds"));
        assertThat(release).isDisabled();
        // Auto-wait: click() waits for "enabled" - so on a disabled button it times out (short timeout for demo).
        assertThatThrownBy(() -> release.click(new Locator.ClickOptions().setTimeout(1000)))
                .isInstanceOf(TimeoutError.class);

        page.getByLabel("I confirm the beneficiary details are correct").check();
        assertThat(release).isEnabled();
        release.click();
        assertThat(page.getByText("Funds released")).isVisible();
    }

    @Test
    void iframes_via_frameLocator_and_contentFrame() {
        FrameLocator frame = page.frameLocator("iframe[title='E-signature widget']");
        frame.getByLabel("Type your full name to sign").fill("Ada Admin");
        frame.getByRole(AriaRole.BUTTON, new FrameLocator.GetByRoleOptions().setName("Sign")).click();
        assertThat(frame.getByText("Signed by Ada Admin")).isVisible();

        // Same frame reached from an element locator (1.43+): iframe element -> its content.
        assertThat(page.getByTitle("E-signature widget").contentFrame().locator("#done")).hasText("Signed by Ada Admin");
        // Page-level locators do NOT see inside frames:
        assertThat(page.getByText("Signed by Ada Admin")).hasCount(0);
    }

    @Test
    void hover_reveals_tooltip() {
        page.getByText("What is a cut-off time?").hover();
        assertThat(page.getByRole(AriaRole.TOOLTIP)).isVisible();
        assertThat(page.getByRole(AriaRole.TOOLTIP)).containsText("15:30 UK time");
    }

    @Test
    void native_multi_select_and_radio_groups() {
        Locator channels = page.getByLabel("Notification channels");
        channels.selectOption(new String[]{"email", "sms"});                      // by value
        assertThat(channels).hasValues(new String[]{"email", "sms"});
        channels.selectOption(new com.microsoft.playwright.options.SelectOption[]{
                new com.microsoft.playwright.options.SelectOption().setLabel("Push notification")}); // by visible label
        assertThat(channels).hasValues(new String[]{"push"});

        Locator chaps = page.getByRole(AriaRole.RADIO, new Page.GetByRoleOptions().setName(Pattern.compile("^CHAPS")));
        chaps.check();
        assertThat(chaps).isChecked();
        assertThat(page.getByRole(AriaRole.RADIO, new Page.GetByRoleOptions().setChecked(true))).hasCount(1);
    }

    @Test
    void legacy_div_dropdown_without_aria_needs_css_and_text() {
        // No roles, no labels: fall back to a scoped CSS container + visible text. Encapsulate this in a
        // page object and raise an accessibility defect - a screen-reader user cannot use this control either.
        Locator dropdown = page.locator("#branch-select");
        dropdown.getByText("Select branch").click();
        dropdown.locator(".fs-item").filter(new Locator.FilterOptions().setHasText("Manchester")).click();
        assertThat(page.getByTestId("branch-code")).hasText("MAN");
        assertThat(dropdown.locator(".fs-value")).hasText(Pattern.compile("^Manchester - Spinningfields"));
    }

    @Test
    void locators_never_go_stale_but_element_handles_do() {
        Locator firstRate = page.getByRole(AriaRole.LIST, new Page.GetByRoleOptions().setName("Exchange rates"))
                .getByRole(AriaRole.LISTITEM).first();
        ElementHandle handle = firstRate.elementHandle(); // pinned to ONE DOM node (legacy API, avoid)
        assertThat(firstRate).hasText("GBP/EUR v1");

        page.getByRole(AriaRole.BUTTON, new Page.GetByRoleOptions().setName("Refresh rates")).click();

        // The list is destroyed and rebuilt: the Locator transparently re-resolves to the new node...
        assertThat(firstRate).hasText("GBP/EUR v2");
        // ...while the handle still points at the old, detached node (Selenium's StaleElementReference problem).
        assertThat(handle.evaluate("el => el.isConnected")).isEqualTo(false);
        handle.dispose();
    }
}
