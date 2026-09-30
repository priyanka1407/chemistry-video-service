package com.bank.qa.extensions;

import com.bank.qa.api.BankApiClient;
import com.bank.qa.auth.Role;
import com.bank.qa.config.FrameworkConfig;
import com.bank.qa.data.SyntheticDataFactory;
import com.microsoft.playwright.BrowserContext;
import com.microsoft.playwright.Page;
import com.microsoft.playwright.assertions.PlaywrightAssertions;
import org.junit.jupiter.api.extension.AfterEachCallback;
import org.junit.jupiter.api.extension.BeforeEachCallback;
import org.junit.jupiter.api.extension.ExtensionContext;
import org.junit.jupiter.api.extension.ParameterContext;
import org.junit.jupiter.api.extension.ParameterResolver;

import java.util.Optional;

/**
 * JUnit 5 extension = the framework's test fixture ("Playwright boilerplate" hidden from tests).
 *
 * <pre>
 *  beforeEach : resolve @AsRole -> storage state -> new BrowserContext (+tracing) -> new Page
 *  test       : receives Page / BrowserContext / UiSession / BankApiClient / SyntheticDataFactory as parameters
 *  afterEach  : on failure keep trace + screenshot (+video); close every context the test opened
 * </pre>
 *
 * Parameter injection (instead of inherited fields) keeps tests thread-safe under parallel execution:
 * nothing is stored in instance or static fields that another thread could see.
 *
 * <p>Alternative worth knowing: Playwright Java ships its own JUnit integration
 * ({@code @UsePlaywright(OptionsFactory.class)} injecting Page/BrowserContext). This custom extension
 * exists because we need role-based storage state, multi-context sessions and failure-only artifacts.
 */
public final class PlaywrightExtension implements BeforeEachCallback, AfterEachCallback, ParameterResolver {

    private static final ExtensionContext.Namespace NS = ExtensionContext.Namespace.create(PlaywrightExtension.class);

    static {
        // Web-first assertion timeout (assertThat(locator).isVisible() etc. retry up to this long).
        PlaywrightAssertions.setDefaultAssertionTimeout(FrameworkConfig.get().assertionTimeout());
    }

    @Override
    public void beforeEach(ExtensionContext ctx) {
        AsRole asRole = findAsRole(ctx).orElse(null);
        Role role = asRole == null ? Role.ANONYMOUS : asRole.value();
        boolean fresh = asRole != null && asRole.freshSession();
        ctx.getStore(NS).put(UiSession.class, new UiSession(testId(ctx), role, fresh));
    }

    @Override
    public void afterEach(ExtensionContext ctx) {
        UiSession s = ctx.getStore(NS).remove(UiSession.class, UiSession.class);
        if (s != null) s.close(ctx.getExecutionException().isPresent());
    }

    @Override
    public boolean supportsParameter(ParameterContext pc, ExtensionContext ec) {
        Class<?> t = pc.getParameter().getType();
        return t == Page.class || t == BrowserContext.class || t == UiSession.class
                || t == BankApiClient.class || t == SyntheticDataFactory.class;
    }

    @Override
    public Object resolveParameter(ParameterContext pc, ExtensionContext ec) {
        UiSession s = ec.getStore(NS).get(UiSession.class, UiSession.class);
        Class<?> t = pc.getParameter().getType();
        if (t == Page.class) return s.page();
        if (t == BrowserContext.class) return s.context();
        if (t == BankApiClient.class) return s.api();
        if (t == SyntheticDataFactory.class) return s.data();
        return s;
    }

    private static Optional<AsRole> findAsRole(ExtensionContext ctx) {
        return ctx.getTestMethod().map(m -> m.getAnnotation(AsRole.class))
                .or(() -> ctx.getTestClass().map(c -> c.getAnnotation(AsRole.class)));
    }

    private static String testId(ExtensionContext ctx) {
        String raw = ctx.getRequiredTestClass().getSimpleName() + "-" + ctx.getDisplayName();
        return raw.replaceAll("[^A-Za-z0-9._-]+", "_").replaceAll("_+", "_");
    }
}
