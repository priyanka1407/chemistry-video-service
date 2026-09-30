# Flakiness at scale

A flaky test passes and fails on the same code. At 10 tests it's an annoyance. At 2,000 tests running 50 times a day, a 0.5 % flake rate means most pipelines go red for no reason, people stop trusting red, and real regressions get merged. Treat flakiness as an **engineering metric with an owner**, not as bad luck.

## 1. Root causes and how this framework prevents them

| Root cause | Symptom | Prevention in this repo |
|---|---|---|
| Timing guesses (`sleep`) | too slow when fast, fails when slow | auto-waiting, web-first assertions, `Poller`, `hold()`. No `Thread.sleep` or `waitForTimeout` in tests |
| Hydration race | typing into a visible input "does nothing" | page objects wait for an app readiness signal (`PaymentPage#open`) |
| Non-retrying reads | `count()` / `allInnerTexts()` taken mid-render | wait with `assertThat(loc).hasCount(n)` first, then read |
| Shared test data | parallel tests find each other's rows | `SyntheticDataFactory.uniqueReference()` (run id + sequence); rows located by business key |
| Shared mutable state | one test closes the batch another is asserting on | `@ResourceLock("settlement-batch")` serialises only those tests |
| Shared session | a logout test kills the session 20 parallel tests use | `@AsRole(freshSession = true)` for session-ending tests |
| Stale cached auth | storage state valid by age, dead on the server | `AuthStateManager.acceptedByServer` verifies once per JVM |
| Ordering assumptions | `rows().first()` depends on other tests | `DataTable.row(ref)`; column lookup by header text |
| Environment drift | new browser build, locale, time zone, viewport | pinned Playwright + container, fixed `locale` / `timezoneId` / viewport |
| Real time | 30-minute idle timeout, cut-off times | `page.clock()` fake timers |
| Third-party dependencies | FX provider slow or down | mocked by default (`mock.third.party=true`), contract-validated |
| Eventual consistency | queue not yet processed | poll a status endpoint up to the SLA, or retry a block until it passes (`FlakinessPatternsTest`) |
| Resource exhaustion | browsers killed by OOM on CI | fixed parallelism plus `max-pool-size`, `--ipc=host`, one browser per worker |
| Hung tests | job blocked forever | `junit.jupiter.execution.timeout.default=3m`, job `timeout-minutes` |

## 2. Retries: a signal, not a cure

- `-Pci` sets `surefire.rerunFailingTestsCount=2`. A test that fails and then passes is written to the JUnit XML as a **`<flakyFailure>`**, not as a pass.
- The pipeline's *Flaky test summary* step lists every flake in the job summary and raises a warning.
- Locally, retries are **0**, so developers see the real failure immediately.
- Retrying without reporting hides real race conditions in the product. Payment systems have real races (double submit, idempotency), so a "flaky" test is sometimes a real bug. Always look at the trace before dismissing a flake.

## 3. Diagnosis toolkit

- **Trace Viewer** (`trace.mode=retain-on-failure`): DOM snapshots before and after every action, network, console, source, timing. Usually enough to answer "what did the page look like when it failed?".
- Screenshots and video on failure (`video.on.failure=true` in the `ci` env).
- `PWDEBUG=1` for step-through; `slow.mo.ms` to watch.
- **Reproduce under stress:** `@RepeatedTest(50)` with `-Djunit.jupiter.execution.parallel.config.fixed.parallelism=8`, plus CPU throttling (`--cpus=1` on the container).
- **Reproduce the data:** the run seed is logged (`Synthetic data seed = …`). Re-run with `-Ddata.seed=<seed>`.

## 4. Process

1. **Detect.** Flakes are surfaced in each run and aggregated nightly (flake rate per test, per week).
2. **Triage within 48 h.** The owning team fixes it, or…
3. **Quarantine.** Add `@Tag("quarantine")` plus a ticket id in a comment. The gating pipeline excludes it (`excludedGroups=quarantine`); the **nightly quarantine lane** keeps running it (`-Pquarantine`, `continue-on-error`) so its behaviour stays visible.
4. **Exit.** After N consecutive green nights (for example 10), remove the tag. Quarantine has an SLA; it isn't a graveyard.
5. **Budget.** Pipeline-level SLO, for example "≥ 99 % of main builds green without retries". Breaching it stops feature work on tests.

## 5. Scale-out

- **Within a machine:** JUnit 5 concurrent classes and methods, with one browser per worker thread and one context per test (`BrowserManager`).
- **Across machines:** `-Dshard=i/n` (`ShardFilter`) splits by a stable hash of the class name. Next step when shard durations diverge: duration-based balancing from the previous run's timings.
- **Keep tests independent** so any subset can run in any order on any shard. That's the property that makes all of the above possible.
