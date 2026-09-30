package com.bank.qa.pages;

import com.microsoft.playwright.Locator;
import com.microsoft.playwright.Page;
import com.microsoft.playwright.options.AriaRole;

/**
 * Base of the Page Object Model.
 *
 * <h2>Page-object rules used across this framework</h2>
 * <ol>
 *   <li>Page objects expose <b>intent</b> ({@code approve(paymentRef)}), not mechanics ({@code clickButton3()}).</li>
 *   <li>Locators are created lazily as {@link Locator} fields/methods. A Locator is a <i>recipe</i>, not an
 *       element: it is re-resolved on every action, so it never goes stale when the DOM re-renders
 *       (unlike Selenium's WebElement or Playwright's legacy ElementHandle).</li>
 *   <li>No assertions inside actions, except "page is loaded" checks. Tests own the assertions, using
 *       web-first {@code assertThat(locator)} which auto-retries.</li>
 *   <li>No {@code Thread.sleep}, no {@code waitForTimeout}. Ever. Playwright AUTO-WAITS (see below).</li>
 * </ol>
 *
 * <h2>Built-in auto-waiting (actionability checks)</h2>
 * Before {@code click()} Playwright waits until the element is: attached to the DOM, visible, stable
 * (not animating - same bounding box in two consecutive animation frames), receives events (not covered
 * by an overlay/modal at the click point) and enabled. {@code fill()} additionally waits for "editable".
 * If the element is detached mid-way (React re-render) it simply re-resolves the locator and retries.
 * All within {@code context.setDefaultTimeout(...)}. This removes 90% of the explicit waits that plague
 * Selenium suites. What auto-waiting does NOT cover: business-level eventual consistency (a status
 * changing in the back-end) - that is what web-first assertions with a timeout, or {@code Poller}, are for.
 *
 * <h2>Locator priority (most to least resilient)</h2>
 * <pre>
 *  1. getByRole(role, name)   - how users & assistive tech perceive the page; also enforces a11y
 *  2. getByLabel / getByPlaceholder / getByText / getByAltText / getByTitle
 *  3. getByTestId("...")      - explicit test contract (data-testid) when no accessible name exists
 *  4. CSS                     - structural; ok for scoped, stable attributes ([data-batch-id=..])
 *  5. XPath                   - last resort (verbose, brittle, no shadow-DOM piercing)
 * </pre>
 */
public abstract class BasePage {

    protected final Page page;

    protected BasePage(Page page) {
        this.page = page;
    }

    public Page page() {
        return page;
    }

    public Locator mainNav() {
        return page.getByRole(AriaRole.NAVIGATION, new Page.GetByRoleOptions().setName("Main"));
    }

    public Locator navLink(String label) {
        return mainNav().getByRole(AriaRole.LINK, new Locator.GetByRoleOptions().setName(label).setExact(true));
    }

    public Locator currentUser() {
        return page.getByTestId("current-user");
    }

    public Locator accessDenied() {
        return page.getByTestId("access-denied");
    }

    public Locator heading(String text) {
        return page.getByRole(AriaRole.HEADING, new Page.GetByRoleOptions().setName(text).setLevel(1));
    }

    public void signOut() {
        page.getByRole(AriaRole.BUTTON, new Page.GetByRoleOptions().setName("Sign out")).click();
    }
}
