package com.bank.qa.extensions;

import com.bank.qa.auth.Role;

import java.lang.annotation.ElementType;
import java.lang.annotation.Inherited;
import java.lang.annotation.Retention;
import java.lang.annotation.RetentionPolicy;
import java.lang.annotation.Target;

/**
 * Declares which authenticated role the test's primary BrowserContext starts with.
 * Method-level wins over class-level. Absent = {@link Role#ANONYMOUS}.
 *
 * <pre>
 * {@literal @}AsRole(Role.MAKER)                        // shared cached session (fast)
 * {@literal @}AsRole(value = Role.MAKER, freshSession = true)  // private session: logout / timeout tests
 * </pre>
 */
@Retention(RetentionPolicy.RUNTIME)
@Target({ElementType.TYPE, ElementType.METHOD})
@Inherited
public @interface AsRole {
    Role value();

    boolean freshSession() default false;
}
