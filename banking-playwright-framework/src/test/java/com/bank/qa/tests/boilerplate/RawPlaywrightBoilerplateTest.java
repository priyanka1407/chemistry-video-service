package com.bank.qa.tests.boilerplate;

import com.bank.qa.core.AppUnderTest;
import com.microsoft.playwright.Browser;
import com.microsoft.playwright.BrowserContext;
import com.microsoft.playwright.BrowserType;
import com.microsoft.playwright.Page;
import com.microsoft.playwright.Playwright;
import com.microsoft.playwright.options.AriaRole;
import org.junit.jupiter.api.AfterAll;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeAll;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Tag;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.TestInstance;
import org.junit.jupiter.api.parallel.Execution;
import org.junit.jupiter.api.parallel.ExecutionMode;

import static com.microsoft.playwright.assertions.PlaywrightAssertions.assertThat;

/**
 * PLAYWRIGHT BOILERPLATE - the minimal, framework-free lifecycle every Playwright-Java suite is built on.
 * (Everything else in this repo is this skeleton + configuration, auth, data and reporting around it.)
 *
 * <pre>
 *  Playwright   (1 per thread)  - starts the Node.js driver process; NOT thread-safe
 *   └ Browser   (1 per thread)  - a real browser process (expensive: ~100-300 ms, ~100+ MB)
 *      └ BrowserContext (1 per test) - incognito-like profile: cookies, storage, cache, permissions (cheap: ms)
 *         └ Page (1..n per context)  - a tab
 * </pre>
 * PER_CLASS lifecycle lets @BeforeAll be non-static; SAME_THREAD keeps this class's methods on one thread
 * because the Playwright instance stored in the fields is not thread-safe.
 */
@Tag("boilerplate")
@TestInstance(TestInstance.Lifecycle.PER_CLASS)
@Execution(ExecutionMode.SAME_THREAD)
class RawPlaywrightBoilerplateTest {

    private Playwright playwright;
    private Browser browser;
    private BrowserContext context;
    private Page page;

    @BeforeAll
    void launchBrowser() {
        playwright = Playwright.create();
        browser = playwright.chromium().launch(new BrowserType.LaunchOptions().setHeadless(true));
    }

    @BeforeEach
    void createContextAndPage() {
        context = browser.newContext(new Browser.NewContextOptions().setBaseURL(AppUnderTest.baseUrl()));
        page = context.newPage();
    }

    @Test
    void login_page_renders() {
        page.navigate("/login.html");
        assertThat(page).hasTitle("Sign in · Northbridge Bank");
        assertThat(page.getByRole(AriaRole.HEADING, new Page.GetByRoleOptions().setName("Sign in to Corporate Banking"))).isVisible();
    }

    @Test
    void each_test_gets_a_clean_context() {
        // Nothing leaks from the previous test: no cookies, no storage.
        page.navigate("/login.html");
        assertThat(page.getByLabel("Username")).isEmpty();
        org.assertj.core.api.Assertions.assertThat(context.cookies()).isEmpty();
    }

    @AfterEach
    void closeContext() {
        context.close(); // closes its pages too; flushes video/HAR
    }

    @AfterAll
    void closeBrowser() {
        browser.close();
        playwright.close(); // stops the driver process
    }
}
