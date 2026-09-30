package com.bank.qa.wait;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import java.time.Duration;
import java.util.function.Predicate;
import java.util.function.Supplier;

/**
 * ASYNC QUEUE PROCESSING - waiting for eventually-consistent back-end state.
 *
 * <p>Payments are settled by a queue worker (message broker / batch engine in real banks), so right
 * after "submit" the status is PENDING and becomes SETTLED some seconds later. Three wrong ways and
 * one right way to wait:
 * <ul>
 *   <li>❌ {@code Thread.sleep(5000)} - too long when fast (slow suite), too short when slow (flaky).</li>
 *   <li>❌ Retrying the whole test - hides real latency regressions.</li>
 *   <li>❌ Huge global timeouts - every genuine failure now takes minutes to report.</li>
 *   <li>✅ <b>Poll a cheap observable</b> (status API) until a condition holds, with a deadline tied to
 *       the SLA, exponential back-off to avoid hammering the system, and a failure message that shows
 *       the last observed value.</li>
 * </ul>
 * In UI code prefer Playwright's web-first assertions ({@code assertThat(locator).hasText(..., timeout)}),
 * which poll the DOM for you. Playwright JS has {@code expect.poll}; Java does not, hence this helper.
 */
public final class Poller {

    private static final Logger log = LoggerFactory.getLogger(Poller.class);

    private Poller() {}

    public static <T> T until(String description, Supplier<T> probe, Predicate<T> done, Duration timeout) {
        return until(description, probe, done, timeout, Duration.ofMillis(200), Duration.ofSeconds(2));
    }

    public static <T> T until(String description, Supplier<T> probe, Predicate<T> done,
                              Duration timeout, Duration initialDelay, Duration maxDelay) {
        long deadline = System.nanoTime() + timeout.toNanos();
        Duration delay = initialDelay;
        T last = null;
        int attempts = 0;
        RuntimeException lastError = null;
        while (true) {
            attempts++;
            try {
                last = probe.get();
                lastError = null;
                if (done.test(last)) {
                    log.debug("'{}' satisfied after {} attempt(s)", description, attempts);
                    return last;
                }
            } catch (RuntimeException e) {
                lastError = e; // transient errors (e.g. 503 while a pod restarts) are retried until the deadline
            }
            if (System.nanoTime() + delay.toNanos() > deadline) {
                AssertionError err = new AssertionError("Timed out after " + timeout.toMillis() + " ms waiting for: "
                        + description + " (attempts=" + attempts + ", last value=" + last + ")");
                if (lastError != null) err.initCause(lastError);
                throw err;
            }
            try {
                Thread.sleep(delay.toMillis());
            } catch (InterruptedException e) {
                Thread.currentThread().interrupt();
                throw new IllegalStateException(e);
            }
            delay = delay.multipliedBy(2).compareTo(maxDelay) > 0 ? maxDelay : delay.multipliedBy(2);
        }
    }
}
