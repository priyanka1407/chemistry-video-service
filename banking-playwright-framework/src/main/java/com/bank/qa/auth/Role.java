package com.bank.qa.auth;

import java.util.EnumSet;
import java.util.Set;

/**
 * RBAC - Role Based Access Control, as specified by the REQUIREMENTS (the test oracle).
 *
 * <p>Key principle: the expected permission matrix lives in the test code and is written from the
 * access-control policy document - it is NEVER read back from the application (otherwise a bug in the
 * app's RBAC would also be a "bug" in the expectation and the test could never fail).
 *
 * <pre>
 *                     VIEWER  MAKER  CHECKER  ADMIN
 *  ACCOUNTS_VIEW        x       x      x        x
 *  PAYMENTS_VIEW        x       x      x        x
 *  SETTLEMENTS_VIEW     x       x      x        x
 *  PAYMENTS_CREATE              x               x
 *  CARDS_AUTHORIZE              x               x
 *  PAYMENTS_APPROVE                    x        x   (never on own payment - four-eyes)
 *  SETTLEMENTS_CLOSE                            x
 *  ADMIN_USERS                                  x
 * </pre>
 * Segregation of duties: MAKER can create but not approve; CHECKER can approve but not create.
 */
public enum Role {
    VIEWER(EnumSet.of(Permission.ACCOUNTS_VIEW, Permission.PAYMENTS_VIEW, Permission.SETTLEMENTS_VIEW)),
    MAKER(EnumSet.of(Permission.ACCOUNTS_VIEW, Permission.PAYMENTS_VIEW, Permission.SETTLEMENTS_VIEW,
            Permission.PAYMENTS_CREATE, Permission.CARDS_AUTHORIZE)),
    CHECKER(EnumSet.of(Permission.ACCOUNTS_VIEW, Permission.PAYMENTS_VIEW, Permission.SETTLEMENTS_VIEW,
            Permission.PAYMENTS_APPROVE)),
    ADMIN(EnumSet.allOf(Permission.class)),
    /** No session at all - used for login tests and unauthenticated negative tests. */
    ANONYMOUS(EnumSet.noneOf(Permission.class));

    private final Set<Permission> permissions;

    Role(Set<Permission> permissions) {
        this.permissions = permissions;
    }

    public boolean can(Permission p) {
        return permissions.contains(p);
    }

    public Set<Permission> permissions() {
        return permissions.isEmpty() ? EnumSet.noneOf(Permission.class) : EnumSet.copyOf(permissions);
    }

    public enum Permission {
        ACCOUNTS_VIEW("/dashboard.html", "Dashboard"),
        PAYMENTS_VIEW(null, null),
        SETTLEMENTS_VIEW("/settlements.html", "Settlements"),
        PAYMENTS_CREATE("/payments.html", "New payment"),
        CARDS_AUTHORIZE("/card-auth.html", "Card authorization"),
        PAYMENTS_APPROVE("/approvals.html", "Approvals"),
        SETTLEMENTS_CLOSE(null, null),
        ADMIN_USERS("/admin.html", "Administration");

        /** Page guarded by this permission (null = API-only permission). */
        public final String page;
        /** Navigation link text shown only to roles holding the permission. */
        public final String navLabel;

        Permission(String page, String navLabel) {
            this.page = page;
            this.navLabel = navLabel;
        }
    }
}
