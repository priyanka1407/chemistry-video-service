package com.bank.qa.components;

import java.util.regex.Pattern;

/**
 * Regex helpers that are safe to pass to Playwright locators.
 *
 * <p>GOTCHA: a {@link Pattern} given to {@code getByText / setName / setHasText / hasText} is NOT evaluated
 * by Java - its source and flags are serialised and executed as a <b>JavaScript RegExp in the browser</b>.
 * Java-only syntax therefore silently breaks matching: {@code Pattern.quote()} emits {@code \Q...\E}
 * (unsupported in JS), as do possessive quantifiers ({@code a++}), {@code \A \Z}, and inline flags other than
 * i/m/s. Escape literals manually instead.
 */
public final class TextPatterns {

    private TextPatterns() {}

    /** Escapes regex metacharacters in a JS-compatible way (no \Q..\E). */
    public static String escape(String literal) {
        return literal.replaceAll("[.*+?^${}()|\\[\\]\\\\/-]", "\\\\$0");
    }

    public static Pattern startsWith(String literal) {
        return Pattern.compile("^" + escape(literal));
    }

    public static Pattern exact(String literal) {
        return Pattern.compile("^" + escape(literal) + "$");
    }
}
