package com.bank.qa.pages;

import com.microsoft.playwright.FrameLocator;
import com.microsoft.playwright.Locator;
import com.microsoft.playwright.Page;
import com.microsoft.playwright.options.AriaRole;

/**
 * Card authorization simulator with a 3-D Secure challenge rendered in an IFRAME.
 *
 * <p>IFRAMES: elements inside an iframe are in another document; page-level locators cannot see them.
 * {@code page.frameLocator("iframe[title='3-D Secure challenge']")} (or, since 1.43,
 * {@code locator.contentFrame()}) returns a scope whose locators auto-wait for the frame to attach
 * AND load - no switchTo().frame() juggling like Selenium, and no stale frame references.
 */
public final class CardAuthorizationPage extends BasePage {

    public record CardInput(String pan, String expiry, String cvv, String amount, String mccLabel) {}

    public CardAuthorizationPage(Page page) {
        super(page);
    }

    public CardAuthorizationPage open() {
        page.navigate("/card-auth.html");
        return this;
    }

    public void authorize(CardInput in) {
        page.getByLabel("Card number").fill(in.pan());
        page.getByLabel("Expiry (MM/YY)").fill(in.expiry());
        page.getByLabel("CVV").fill(in.cvv());
        page.getByLabel("Amount (GBP)").fill(in.amount());
        if (in.mccLabel() != null) page.getByLabel("Merchant category").selectOption(new com.microsoft.playwright.options.SelectOption().setLabel(in.mccLabel()));
        page.getByRole(AriaRole.BUTTON, new Page.GetByRoleOptions().setName("Authorize")).click();
    }

    public Locator decision() { return page.locator("#decision").getByTestId("status-badge"); }
    public Locator responseCode() { return page.getByTestId("response-code"); }
    public Locator responseMessage() { return page.getByTestId("response-message"); }
    public Locator maskedPan() { return page.getByTestId("masked-pan"); }
    public Locator fieldError(String field) { return page.locator("#" + field + "-error"); }

    /** The 3DS iframe located by its accessible title. */
    public FrameLocator challengeFrame() {
        return page.frameLocator("iframe[title='3-D Secure challenge']");
    }

    public Locator challengeIframe() {
        return page.locator("iframe[title='3-D Secure challenge']");
    }

    public void completeChallenge(String otp) {
        FrameLocator acs = challengeFrame();
        acs.getByLabel("One-time passcode").fill(otp);
        acs.getByRole(AriaRole.BUTTON, new FrameLocator.GetByRoleOptions().setName("Verify")).click();
    }
}
