package com.bank.qa.pages;

import com.bank.qa.auth.TotpGenerator;
import com.bank.qa.config.Secrets;
import com.microsoft.playwright.Locator;
import com.microsoft.playwright.Page;
import com.microsoft.playwright.options.AriaRole;

/** Two-step login: credentials, then TOTP (MFA). */
public final class LoginPage extends BasePage {

    public LoginPage(Page page) {
        super(page);
    }

    public LoginPage open() {
        page.navigate("/login.html");
        return this;
    }

    public Locator username() { return page.getByLabel("Username"); }
    public Locator password() { return page.getByLabel("Password"); }
    public Locator otp() { return page.getByLabel("Verification code"); }
    public Locator continueButton() { return page.getByRole(AriaRole.BUTTON, new Page.GetByRoleOptions().setName("Continue")); }
    public Locator verifyButton() { return page.getByRole(AriaRole.BUTTON, new Page.GetByRoleOptions().setName("Verify and sign in")); }
    public Locator credentialsError() { return page.locator("#login-error"); }
    public Locator otpError() { return page.locator("#otp-error"); }
    /** Info banner (timeout / signed out) - role=status. */
    public Locator statusBanner() { return page.getByRole(AriaRole.STATUS); }

    public LoginPage submitCredentials(String user, String pass) {
        username().fill(user);
        password().fill(pass);
        continueButton().click();
        return this;
    }

    public LoginPage submitOtp(String code) {
        otp().fill(code);
        verifyButton().click();
        return this;
    }

    /** Full UI login incl. MFA; waits for the dashboard URL (auto-wait on navigation). */
    public DashboardPage loginAs(Secrets.Credential cred) {
        submitCredentials(cred.username(), cred.password());
        submitOtp(TotpGenerator.now(cred.totpSecret()));
        page.waitForURL("**/dashboard.html");
        return new DashboardPage(page);
    }
}
