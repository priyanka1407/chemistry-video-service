package com.bank.qa.tests.rbac;

import com.bank.qa.api.BankApiClient;
import com.bank.qa.api.SchemaValidator;
import com.bank.qa.auth.Role;
import com.bank.qa.auth.Role.Permission;
import com.bank.qa.extensions.PlaywrightTest;
import com.bank.qa.extensions.UiSession;
import com.bank.qa.pages.DashboardPage;
import com.microsoft.playwright.Page;
import org.junit.jupiter.api.Tag;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.Arguments;
import org.junit.jupiter.params.provider.EnumSource;
import org.junit.jupiter.params.provider.MethodSource;

import java.util.Arrays;
import java.util.List;
import java.util.Map;
import java.util.stream.Stream;

import static com.microsoft.playwright.assertions.PlaywrightAssertions.assertThat;
import static org.assertj.core.api.Assertions.assertThat;

/**
 * RBAC - verify the access-control matrix at BOTH layers.
 *
 * <ul>
 *   <li><b>UI layer</b>: navigation shows exactly the permitted entries; guarded pages show "Access denied".</li>
 *   <li><b>API layer (the one that matters)</b>: hiding a button is not security. Every protected endpoint must
 *       return 403 for roles without the permission, 401 without a session - tested directly, bypassing the UI.</li>
 *   <li>The expected matrix comes from {@link Role} (the policy), never from the app's own /api/me.</li>
 *   <li>Parameterised: roles x permissions = one test case each, so a report shows precisely which cell failed.</li>
 * </ul>
 */
@PlaywrightTest
@Tag("rbac")
class RbacMatrixTest {

    static Stream<Arguments> rolePagePairs() {
        return Stream.of(Role.VIEWER, Role.MAKER, Role.CHECKER, Role.ADMIN)
                .flatMap(role -> Arrays.stream(Permission.values()).filter(p -> p.page != null)
                        .map(p -> Arguments.of(role, p)));
    }

    @ParameterizedTest(name = "{0} -> {1}")
    @MethodSource("rolePagePairs")
    void guarded_pages_respect_role(Role role, Permission permission, UiSession session) {
        Page page = session.openAs(role);
        DashboardPage base = new DashboardPage(page);
        page.navigate(permission.page);
        assertThat(base.currentUser()).containsText(role.name()); // shell rendered for this user
        if (role.can(permission)) {
            assertThat(base.accessDenied()).isHidden();
        } else {
            assertThat(base.accessDenied()).isVisible();
            assertThat(base.accessDenied()).containsText("does not have permission");
        }
    }

    @ParameterizedTest(name = "navigation for {0}")
    @EnumSource(value = Role.class, names = {"VIEWER", "MAKER", "CHECKER", "ADMIN"})
    void navigation_shows_exactly_the_permitted_entries(Role role, UiSession session) {
        DashboardPage dashboard = new DashboardPage(session.openAs(role)).open();
        List<String> expected = Arrays.stream(Permission.values())
                .filter(p -> p.navLabel != null && role.can(p)).map(p -> p.navLabel).toList();
        var links = dashboard.mainNav().getByRole(com.microsoft.playwright.options.AriaRole.LINK);
        // hasCount RETRIES until the nav is rendered; allInnerTexts() does NOT retry - so wait first, then read.
        // (assertThat(links).hasText(String[]) would also work, but asserts ORDER, which isn't a requirement here.)
        assertThat(links).hasCount(expected.size());
        assertThat(links.allInnerTexts()).containsExactlyInAnyOrderElementsOf(expected);
    }

    /** Endpoint, the permission guarding it, and the status an AUTHORISED caller gets for this probe. */
    record Probe(String method, String path, Object body, Permission permission, int authorisedStatus) {
        @Override
        public String toString() {
            return method + " " + path;
        }
    }

    static final List<Probe> PROBES = List.of(
            new Probe("GET", "/api/accounts", null, Permission.ACCOUNTS_VIEW, 200),
            new Probe("GET", "/api/payments", null, Permission.PAYMENTS_VIEW, 200),
            new Probe("GET", "/api/settlements", null, Permission.SETTLEMENTS_VIEW, 200),
            // Probes use deliberately invalid bodies / unknown ids so authorised callers get 422/404 and
            // NOTHING is created or changed - RBAC tests must be side-effect free on shared environments.
            new Probe("POST", "/api/payments", Map.of(), Permission.PAYMENTS_CREATE, 422),
            new Probe("POST", "/api/cards/authorize", Map.of(), Permission.CARDS_AUTHORIZE, 422),
            new Probe("POST", "/api/payments/PAY-00000000/approve", null, Permission.PAYMENTS_APPROVE, 404),
            new Probe("POST", "/api/settlements/STL-00000000-000/close", null, Permission.SETTLEMENTS_CLOSE, 404),
            new Probe("GET", "/api/admin/users", null, Permission.ADMIN_USERS, 200));

    static Stream<Arguments> roleProbePairs() {
        return Arrays.stream(Role.values()).flatMap(role -> PROBES.stream().map(p -> Arguments.of(role, p)));
    }

    @ParameterizedTest(name = "{0} {1}")
    @MethodSource("roleProbePairs")
    void api_enforces_permissions_independently_of_the_ui(Role role, Probe probe, UiSession session) {
        BankApiClient api = role == Role.ANONYMOUS ? session.api() : session.apiAs(role);
        BankApiClient.Response res = api.call(probe.method(), probe.path(), probe.body());

        int expected = role == Role.ANONYMOUS ? 401 : role.can(probe.permission()) ? probe.authorisedStatus() : 403;
        assertThat(res.status()).as("%s %s as %s", probe.method(), probe.path(), role).isEqualTo(expected);
        if (res.status() >= 400) {
            SchemaValidator.assertValid("error.schema.json", res.body()); // consistent error contract
            if (expected == 403) assertThat(res.errorCode()).isEqualTo("FORBIDDEN");
        }
    }
}
