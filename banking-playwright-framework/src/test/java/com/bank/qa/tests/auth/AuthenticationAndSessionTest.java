package com.bank.qa.tests.auth;

import com.bank.qa.api.BankApiClient;
import com.bank.qa.auth.AuthStateManager;
import com.bank.qa.auth.Role;
import com.bank.qa.auth.TotpGenerator;
import com.bank.qa.config.Secrets;
import com.bank.qa.extensions.AsRole;
import com.bank.qa.extensions.PlaywrightTest;
import com.bank.qa.pages.DashboardPage;
import com.bank.qa.pages.LoginPage;
import com.microsoft.playwright.BrowserContext;
import com.microsoft.playwright.Page;
import com.microsoft.playwright.options.AriaRole;
import com.microsoft.playwright.options.Cookie;
import org.junit.jupiter.api.Tag;
import org.junit.jupiter.api.Test;

import java.time.Instant;
import java.util.regex.Pattern;

import static com.microsoft.playwright.assertions.PlaywrightAssertions.assertThat;
import static org.assertj.core.api.Assertions.assertThat;

/**
 * AUTHENTICATION AND SESSION HANDLING.
 *
 * <ul>
 *   <li>UI login incl. MFA is tested HERE, once, thoroughly. Every other test starts pre-authenticated from
 *       a cached storage state (see AuthStateManager) - faster and far less flaky.</li>
 *   <li>Tests that END a session (logout, idle timeout) use {@code freshSession = true}: they must not
 *       destroy the shared per-role session that parallel tests are using.</li>
 *   <li>Idle timeout is 30 min. Nobody waits 30 min in CI: {@code page.clock()} fakes time in the browser
 *       (Date, setTimeout, setInterval, performance.now) so the timer can be fast-forwarded deterministically.</li>
 * </ul>
 */
@PlaywrightTest
@Tag("auth")
class AuthenticationAndSessionTest {

    @Test
    @Tag("smoke")
    void user_can_sign_in_with_password_and_totp(Page page) {
        Secrets.Credential maker = Secrets.forRole(Role.MAKER);
        DashboardPage dashboard = new LoginPage(page).open().loginAs(maker);

        assertThat(dashboard.welcome()).hasText("Welcome, Mark Maker");
        assertThat(dashboard.currentUser()).hasText("Mark Maker (MAKER)");
        assertThat(dashboard.accountCards()).hasCount(3);

        // Security properties of the session cookie (a regulator / pen-tester WILL ask about these).
        Cookie session = page.context().cookies().stream().filter(c -> c.name.equals("NB_SESSION")).findFirst().orElseThrow();
        assertThat(session.httpOnly).as("HttpOnly - not readable by injected JS").isTrue();
        assertThat(session.sameSite).as("SameSite - CSRF mitigation").isEqualTo(com.microsoft.playwright.options.SameSiteAttribute.STRICT);
        assertThat(page.evaluate("() => document.cookie")).asString().doesNotContain("NB_SESSION");
    }

    @Test
    void wrong_password_is_rejected_without_revealing_which_field_was_wrong(Page page) {
        LoginPage login = new LoginPage(page).open().submitCredentials("maker.user", "wrong-password");
        // Generic message: no username enumeration.
        assertThat(login.credentialsError()).hasText("Username or password is incorrect");
        assertThat(login.otp()).isHidden();
    }

    @Test
    void wrong_or_expired_totp_is_rejected(Page page) {
        Secrets.Credential maker = Secrets.forRole(Role.MAKER);
        LoginPage login = new LoginPage(page).open().submitCredentials(maker.username(), maker.password());
        // A code from 10 minutes ago is outside the +/-1 step window.
        login.submitOtp(TotpGenerator.at(maker.totpSecret(), Instant.now().minusSeconds(600)));
        assertThat(login.otpError()).hasText("The verification code is incorrect or expired");
        assertThat(page).hasURL(Pattern.compile("/login\\.html"));
    }

    @Test
    void deep_link_without_session_redirects_to_login(Page page) {
        page.navigate("/payments.html");
        assertThat(page).hasURL(Pattern.compile("/login\\.html\\?reason=UNAUTHENTICATED"));
        assertThat(new LoginPage(page).username()).isVisible();
    }

    @Test
    @AsRole(Role.MAKER)
    void storage_state_starts_the_test_already_authenticated(Page page, BrowserContext context) {
        // No login step at all: cookies came from target/.auth/maker.json.
        page.navigate("/dashboard.html");
        assertThat(new DashboardPage(page).currentUser()).hasText("Mark Maker (MAKER)");
        assertThat(context.cookies()).extracting(c -> c.name).contains("NB_SESSION");
        // The cached file is reused by later tests (created at most once per TTL, under a lock).
        assertThat(AuthStateManager.storageStateFor(Role.MAKER)).exists();
    }

    @Test
    @AsRole(value = Role.VIEWER, freshSession = true)
    void sign_out_invalidates_the_session_server_side(Page page, BankApiClient api) {
        DashboardPage dashboard = new DashboardPage(page).open();
        assertThat(dashboard.currentUser()).isVisible();

        dashboard.signOut();
        assertThat(page).hasURL(Pattern.compile("reason=signedout"));
        assertThat(new LoginPage(page).statusBanner()).hasText("You have signed out securely.");
        // Not just a client-side redirect: the server must reject the old session.
        assertThat(api.me().status()).isEqualTo(401);
        // Browser Back must not reveal protected content. This test caught two real defects in the sample app:
        //  1. API responses lacked "Cache-Control: no-store" -> on history navigation Chrome served /api/me
        //     from the HTTP cache and the dashboard rendered for a signed-out user;
        //  2. pages restored from the back/forward cache do not re-run scripts unless the app handles 'pageshow'.
        page.goBack();
        assertThat(page).hasURL(Pattern.compile("/login\\.html"));
    }

    @Test
    @AsRole(value = Role.VIEWER, freshSession = true)
    void idle_session_warns_then_signs_out_using_fake_clock(Page page) {
        // install() BEFORE navigation so the page's timers are created on the fake clock.
        page.clock().install();
        DashboardPage dashboard = new DashboardPage(page).open();
        assertThat(dashboard.currentUser()).isVisible();

        Page.GetByRoleOptions dialogName = new Page.GetByRoleOptions().setName("Your session is about to expire");
        page.clock().fastForward("28:59");
        assertThat(page.getByRole(AriaRole.ALERTDIALOG, dialogName)).isHidden();
        page.clock().fastForward("00:02");  // crosses the 29:00 warning boundary (BVA on time!)
        assertThat(page.getByRole(AriaRole.ALERTDIALOG, dialogName)).isVisible();

        // "Stay signed in" resets the idle timer.
        page.getByRole(AriaRole.BUTTON, new Page.GetByRoleOptions().setName("Stay signed in")).click();
        assertThat(page.getByRole(AriaRole.ALERTDIALOG, dialogName)).isHidden();

        page.clock().fastForward("30:01");  // full idle period elapses with no activity
        assertThat(page).hasURL(Pattern.compile("reason=timeout"));
        assertThat(new LoginPage(page).statusBanner()).hasText("You were signed out due to inactivity.");
    }
}
