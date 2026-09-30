# Interview guide: Playwright (Java) for banking

Short model answers, each pointing at the code that shows it. Practise explaining the *why*, then open the file and walk through the *how*.

---

## Architecture and boilerplate

**Q: Explain Playwright's object model.**
`Playwright` (starts the Node driver) → `Browser` (a real browser process) → `BrowserContext` (an isolated incognito-like profile: cookies, storage, cache, permissions) → `Page` (a tab). A browser is expensive; a context costs milliseconds. So: one browser per worker, one context per test. → `RawPlaywrightBoilerplateTest`, `BrowserManager`.

**Q: Why not Selenium?**
Auto-waiting and web-first assertions, strict locators, built-in network interception, multiple contexts per browser, storage-state auth, tracing, a bundled API client, and pinned browser builds, all over one WebSocket connection instead of per-command HTTP. Honest trade-off: fewer language bindings than Selenium, and no real Safari (Playwright uses WebKit builds).

**Q: How is your framework structured?**
Layered: config → core (browser lifecycle) → auth → page objects/components → API client/mocks/data → a JUnit extension that wires it all → tests. Tests receive `Page`, `UiSession`, `BankApiClient` and `SyntheticDataFactory` as parameters. There are no static or shared fields, which keeps them thread-safe. → `PlaywrightExtension`, `README §2`.

**Q: `@UsePlaywright` vs a custom extension?**
Playwright Java ships `@UsePlaywright(OptionsFactory)`, which injects a Page/Context per test and is enough for simple suites. We needed role-based storage state, several users per test and failure-only artifacts, so we wrote a small extension instead.

## Parallelization and browser versions

**Q: Is Playwright Java thread-safe?**
No. `Playwright`, `Browser` and `Page` must stay on the thread that created them. JUnit runs tests on threads, not processes (unlike the Node runner), so each worker thread gets its own Playwright and Browser through `ThreadLocal`, and each test gets a new context. → `BrowserManager`.

**Q: How do you configure parallelism?**
`junit-platform.properties`: concurrent classes and methods, `fixed` strategy with `parallelism` and `max-pool-size` (so blocked threads don't spawn extra browsers). `@ResourceLock` / `@Isolated` for tests that touch shared state. `-Dshard=i/n` (`ShardFilter`, a `PostDiscoveryFilter`) to split across CI machines.

**Q: How do you keep the browser version consistent?**
Each Playwright version bundles specific browser revisions, so pinning `playwright.version` in the pom pins the browsers. CI runs in `mcr.microsoft.com/playwright/java:v<same>`. The browser version is logged at launch. Branded `channel=chrome|msedge` is only for dedicated compatibility jobs. Renovate bumps the pom and the image tag together.

## Waiting and flakiness

**Q: What does auto-waiting check before a click?**
Attached, visible, stable (no animation), receives events (not covered), enabled. `fill` also checks editable. It re-resolves if the element was re-rendered. → `BasePage` Javadoc, `LocatorDeepDiveTest#actionability…`.

**Q: What does auto-waiting NOT solve?**
Back-end eventual consistency (use web-first assertions with a timeout, or polling); hydration (the element is visible before its JS listener exists); non-retrying reads such as `count()`. Real example in this repo: `PaymentPage#open` waits for the accounts to load before the combobox works.

**Q: `assertThat(locator).isVisible()` vs `assertTrue(locator.isVisible())`?**
The first retries until the timeout. The second is a one-shot snapshot and a classic source of flakes.

**Q: How do you handle flaky tests at scale?**
Prevention: no sleeps, unique data, isolation, pinned environment, mocked third parties, fake clock. Detection: CI retries reported as `<flakyFailure>` plus a summary. Process: triage within 48 h, a quarantine tag with an SLA, a nightly quarantine lane, a flake-rate budget. Diagnosis: Trace Viewer. → `docs/FLAKINESS.md`, `FlakinessPatternsTest`.

## Locators

**Q: Your locator strategy?**
`getByRole` + accessible name first, then label/text/placeholder, then `getByTestId`, then scoped CSS; XPath last. Strictness guarantees each action targets exactly one element. → `docs/LOCATORS_AND_NETWORK_MOCKING.md` Part A.

**Q: Locator vs ElementHandle?**
A Locator is a lazy query that is re-resolved every time, so it's never stale. An ElementHandle pins one node, which goes stale when the DOM is re-rendered. → `locators_never_go_stale_but_element_handles_do`.

**Q: How do you handle a dynamic dropdown?**
Locate the combobox by role and name, `fill` the search term, wait for `aria-expanded=true` (proves the handler ran), click `getByRole(OPTION, name ^Exact)` (auto-waits for the async option), assert the value was committed. Never locate by the random option ids. For a native `<select>` with async options, `selectOption` waits for the option to exist. → `Combobox`, `PaymentPage#fill`.

**Q: Dynamic ids, shadow DOM, iframes?**
Dynamic ids: role / test-id / `[id^=prefix]`. Shadow DOM: CSS and `getBy*` pierce *open* roots; XPath doesn't; closed roots can't be reached. iframes: `frameLocator(...)` or `locator.contentFrame()`, which auto-wait for the frame to load. → `LocatorDeepDiveTest`, `CardAuthorizationPage`.

**Q: A gotcha you hit with Java + Playwright regex?**
Java `Pattern`s are executed as JavaScript RegExps in the browser, so `Pattern.quote()` (`\Q…\E`) silently fails to match. Escape manually. → `TextPatterns`.

## Network mocking

**Q: How does `route` work?**
It intercepts at the browser network layer and pauses matching requests. The handler must `fulfill`, `resume` (continue), `abort`, `fallback`, or `fetch` and then fulfill. Handlers run last-registered-first; page routes beat context routes; `times(n)`, `unroute`. `APIRequestContext` and service-worker traffic aren't routed. → `NetworkMocks` Javadoc.

**Q: How do you simulate latency?**
Not with sleeps inside handlers, which block Java's single-threaded event loop. Park the request (`hold`), assert the loading UI, release it. Fully deterministic. → `hold_request_to_assert_loading_state_deterministically`.

**Q: How do you stop mocks drifting from the real API?**
Generate them from the provider's JSON Schema and validate every mock against it (`MockSchemaGenerator.generateValid`). Validate real responses against the same schema in contract tests. Consumer-driven contracts (Pact) are the next step.

**Q: What would you *not* mock?**
The system under test's own core flows in E2E tests, and authentication. Mock third parties, error states, and rare UI states in component-level tests.

## Authentication, sessions, storage state

**Q: How do you avoid logging in for every test?**
Log in once per role through the API (password + TOTP), save `storageState` (cookies + localStorage) to `target/.auth/<role>.json`, and start each context from it. Protected by a per-role lock plus a file lock, with a TTL, and verified against the server once per JVM. → `AuthStateManager`.

**Q: Pitfalls of storage state?**
It doesn't include sessionStorage. The file holds live cookies (keep it out of git and CI artifacts). Parallel tests share one server session, so logout and timeout tests must use a fresh session. It can be fresh by age yet revoked on the server, so verify it.

**Q: How do you automate MFA in a bank without disabling it?**
Dedicated test identities whose TOTP seeds live in the vault; the test computes RFC 6238 codes. Alternatives: an OTP capture sink in lower environments, or a non-prod token endpoint behind mTLS. → `TotpGenerator`.

**Q: How do you test a 30-minute idle timeout?**
`page.clock().install()` before navigation, then `fastForward("28:59")` (no warning) and `fastForward("00:02")` (warning). A BVA on time. → `idle_session_warns_then_signs_out_using_fake_clock`.

## RBAC, multi-user, multi-tab

**Q: How do you test RBAC?**
The expected matrix comes from policy (`Role`), never from the app. Parameterised role × permission cases at the **UI** (navigation, access denied) and, more importantly, at the **API** (403, or 401 without a session), because hiding a button isn't security. Probes are side-effect free. → `RbacMatrixTest` (68 cases).

**Q: Maker-checker in one test?**
Two isolated contexts (separate cookie jars): the maker arranges through the API, the checker acts in the UI, and the result is asserted through the API. The four-eyes violation is checked at both UI and API level. → `MakerCheckerApprovalTest`.

**Q: Handling a new tab or popup?**
`Page tab = context.waitForPage(() -> link.click())`. Arm the wait *before* the action. Then `tab.waitForLoadState()`, `bringToFront()`, and close it afterwards. Downloads: `page.waitForDownload(action)` then `saveAs`. → `SettlementsPage`, `SettlementUiTest`.

## Async processing, API validation

**Q: How do you test asynchronous settlement?**
Enqueue all items first, then poll a cheap status API with exponential back-off up to the SLA, with a failure message showing the last observed state. Assert the full state history and reconcile totals. Assertions must tolerate other tests' items in the shared batch. → `Poller`, `SettlementQueueTest`.

**Q: API testing with Playwright?**
`APIRequestContext`: `context.request()` shares cookies with the browser (hybrid tests); `playwright.request().newContext()` is standalone. Validate status, headers (`Location`, `Cache-Control`), JSON Schema, business values, idempotency (same key → replay, different body → 409), the error envelope, and 400/401/403/404. → `PaymentApiContractTest`.

**Q: Why is idempotency important in payments?**
Clients retry after timeouts; without an idempotency key a retry becomes a duplicate payment. Test it at the API (replay/409) and in the UI (button disabled while in flight, exactly one POST). → `review_then_confirm_is_protected_against_double_submission`.

## Test data and BVA

**Q: Where does your test data come from in a regulated bank?**
It's synthetic, never copied from production. PANs in test BINs with valid Luhn digits, IBANs with valid mod-97, seeded and reproducible, unique `QA-` references for isolation and audit, PII masked in every output, secrets only from the vault. → `docs/TEST_DATA_REGULATED.md`.

**Q: Walk me through BVA from a user story.**
"Amount 0.01–50,000.00": ε = 0.01, so the cases are 0.00 ✗, 0.01 ✓, 0.02 ✓, nominal ✓, 49,999.99 ✓, 50,000.00 ✓, 50,000.01 ✗. Add equivalence classes for format (letters, 3 dp, negative, exponent). Internal thresholds (approval above 10,000.00) are boundaries too. Run the full matrix at the API layer and the extreme edges in the UI. → `BoundaryValues`, `PaymentBoundaryValueTest`, `docs/USER_STORIES.md`.

## CI/CD

**Q: Describe your pipeline.**
Build + unit tests (no browser) → 4 E2E shards in the pinned Playwright container with retries on → publish the JUnit check and flake summary → nightly cross-browser + quarantine lane → manual UAT smoke behind a protected environment with scoped secrets. Artifacts only on failure; never auth state. → `docs/CI_CD.md`.

**Q: Tell me about a bug your automation found.**
See README §4. For example: Back after sign-out showed account data because API responses were cacheable (`Cache-Control: no-store` was missing); a malformed JSON body dropped the TCP connection instead of returning 400; a response field violated its published schema.
