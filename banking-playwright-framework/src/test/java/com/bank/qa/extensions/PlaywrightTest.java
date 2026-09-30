package com.bank.qa.extensions;

import org.junit.jupiter.api.Tag;
import org.junit.jupiter.api.extension.ExtendWith;

import java.lang.annotation.ElementType;
import java.lang.annotation.Retention;
import java.lang.annotation.RetentionPolicy;
import java.lang.annotation.Target;

/** Composed annotation: every browser-backed test class gets the fixture and the "e2e" tag. */
@Retention(RetentionPolicy.RUNTIME)
@Target(ElementType.TYPE)
@ExtendWith(PlaywrightExtension.class)
@Tag("e2e")
public @interface PlaywrightTest {
}
