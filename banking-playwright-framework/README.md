# Banking Playwright Framework (Java)

A Playwright + Java 21 + JUnit 5 test-automation framework for **authorization, payments and settlement**. It ships with its own sample banking web app, **Northbridge Bank · Corporate**, so every concept runs out of the box with no external dependencies.

```
187 tests · ~40 s on 4 parallel workers · 0 flaky in repeated runs · shardable across CI machines
```

---

## 1. Quick start

```bash
# prerequisites: JDK 21, Maven 3.9+
cd banking-playwright-framework

mvn test                                   # everything (unit + E2E), headless, 4 parallel workers
mvn test -Dgroups=unit                     # fast, browser-free tests only
mvn test -Dgroups=smoke                    # smoke pack
mvn test -Dtest=LocatorDeepDiveTest        # one class
mvn test -Dheadless=false -Dslow.mo.ms=300 # watch it run
mvn test -Dbrowser=firefox                 # other engine
mvn test -Pci -Dshard=2/4                  # what a CI shard runs (retries on)
PWDEBUG=1 mvn test -Dtest=PaymentInitiationTest   # Playwright Inspector, step through

mvn -q exec:java                           # run the sample bank on http://localhost:8085
mvn -q exec:java -Dexec.mainClass=com.bank.qa.sampleapp.PrintTotp   # current MFA codes for manual login
```

Users are `viewer.user`, `maker.user`, `checker.user` and `admin.user`, all with password `Test@12345` (sample app only). MFA is TOTP.

> **Browsers.** Playwright downloads the browser builds that match the pinned version on first run. On machines where they are pre-installed (the CI container, or corporate images without internet access), set `PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1`.

View a failure trace (saved automatically for failed tests):

```bash
mvn exec:java -e -Dexec.mainClass=com.microsoft.playwright.CLI \
  -Dexec.args="show-trace target/playwright-artifacts/traces/<test>.zip"
```

---

## 2. Project structure

```
banking-playwright-framework/
├── pom.xml                         pinned Playwright 1.56.0 (= pinned browser builds), profiles ci / quarantine
├── Dockerfile · Jenkinsfile        reproducible runner + Jenkins pipeline  (GitHub workflow: /.github/workflows)
├── docs/                           deep dives + interview guide
└── src
    ├── main/java/com/bank/qa
    │   ├── config/      FrameworkConfig (layered config), Secrets (vault/env only)
    │   ├── core/        BrowserManager (ThreadLocal Playwright/Browser, context factory), AppUnderTest
    │   ├── auth/        Role (RBAC matrix = test oracle), AuthStateManager (storage state), TotpGenerator (MFA)
    │   ├── pages/       Page Objects: Login, Dashboard, Payment, Approvals, CardAuthorization, Settlements…
    │   ├── components/  Combobox (dynamic dropdown), DataTable, TextPatterns (JS-safe regex)
    │   ├── api/         BankApiClient (APIRequestContext), SchemaValidator (JSON Schema 2020-12)
    │   ├── mock/        NetworkMocks (route/fulfill/abort/fetch/hold/HAR), MockSchemaGenerator
    │   ├── data/        SyntheticDataFactory, FinancialIdentifiers (Luhn, IBAN), PiiMasker, BoundaryValues
    │   ├── wait/        Poller (async / eventual consistency)
    │   └── sampleapp/   the system under test (embedded HTTP server + HTML/JS)
    ├── main/resources/config      default / local / ci / uat .properties
    └── test
        ├── java/com/bank/qa/extensions   PlaywrightExtension (fixture), @AsRole, UiSession, ShardFilter
        ├── java/com/bank/qa/tests
        │   ├── auth/            login + MFA, storage state, logout, idle timeout (fake clock)
        │   ├── rbac/            UI + API permission matrix (68 cases)
        │   ├── payments/        E2E initiation, dynamic dropdown, BVA, maker-checker
        │   ├── authorization/   card auth, ISO 8583 codes, 3-D Secure iframe
        │   ├── settlement/      async queue, reconciliation, multi-tab, download, live table
        │   ├── api/             contract, idempotency, error envelope, 401s
        │   ├── mocking/         network-mocking deep dive (9 techniques)
        │   ├── locators/        locator deep dive (11 patterns)
        │   ├── flakiness/       anti-patterns vs patterns
        │   ├── boilerplate/     raw Playwright lifecycle without the framework
        │   └── data/            unit tests for generators, masking, BVA, mock generation, TOTP
        └── resources            junit-platform.properties (parallelism), schemas/*.json
```

### Test lifecycle (what `@PlaywrightTest` does)

```
JVM   ─ AppUnderTest: start sample bank once on a random port
thread ─ BrowserManager: 1 Playwright + 1 Browser per worker thread (ThreadLocal)
test  ─ PlaywrightExtension.beforeEach
          @AsRole(MAKER) → AuthStateManager.storageStateFor(MAKER)  (API login + TOTP once, cached, locked, verified)
          → browser.newContext(storageState, locale, timezone, viewport, baseURL) → tracing.start → newPage
        test body receives Page / BrowserContext / UiSession / BankApiClient / SyntheticDataFactory
        afterEach: failed? → screenshot + trace.zip (+video) ; close every context the test opened
```

---

## 3. Where each concept lives

| Concept | Where to read it | Key idea |
|---|---|---|
| **Playwright boilerplate** | `tests/boilerplate/RawPlaywrightBoilerplateTest`, `extensions/PlaywrightExtension` | Playwright → Browser → Context → Page, plus who owns which lifecycle |
| **Configuration** | `config/FrameworkConfig`, `resources/config/*.properties` | default < env file < `-D` < `BANK_*` env var. No secrets in files |
| **Parallelization** | `core/BrowserManager`, `junit-platform.properties`, `extensions/ShardFilter` | Thread-local browser, context per test, `@ResourceLock` for shared state, CI shards |
| **Consistent browser version** | `pom.xml` header, `Dockerfile`, workflow `container:` | Playwright version = browser build. Pin the pom and the image tag together |
| **Built-in auto-waiting** | `pages/BasePage` Javadoc, `LocatorDeepDiveTest#actionability…` | Attached, visible, stable, receives events, enabled. Web-first assertions retry |
| **Locators deep dive** | `docs/LOCATORS_AND_NETWORK_MOCKING.md`, `LocatorDeepDiveTest`, `components/*` | role > label/text > test-id > CSS > XPath. Strictness, filter/has/and/or, frames, shadow DOM |
| **Dynamic dropdown** | `components/Combobox`, `PaymentInitiationTest#dynamic_dropdown…` | async debounced ARIA combobox with random option ids, plus a native select with async options |
| **Network mocking deep dive** | `mock/NetworkMocks`, `NetworkMockingDeepDiveTest` | fulfill / abort / fetch+patch / hold / precedence / times / fallback / HAR |
| **Mock schema generation** | `mock/MockSchemaGenerator`, `schemas/*.json` | Mocks generated from the contract and re-validated, so they can't drift |
| **Storage state handling** | `auth/AuthStateManager` | Log in once per role, cache, lock across threads/JVMs, TTL + server verification |
| **Authentication & sessions** | `tests/auth/AuthenticationAndSessionTest` | MFA/TOTP, cookie flags, logout invalidation, bfcache, `page.clock()` idle timeout |
| **RBAC** | `auth/Role`, `tests/rbac/RbacMatrixTest` | Expected matrix from policy; asserted in both UI and API; side-effect-free probes |
| **Multi-tab handling** | `pages/SettlementsPage`, `SettlementUiTest` | `context.waitForPage(action)`, `bringToFront`, close popups; downloads |
| **Multi-user in one test** | `MakerCheckerApprovalTest` | Maker and checker in separate contexts; four-eyes principle |
| **Async queue processing** | `wait/Poller`, `SettlementQueueTest` | Enqueue everything, then poll with back-off to the SLA; assert history and reconciliation |
| **API validation** | `api/BankApiClient`, `PaymentApiContractTest` | `APIRequestContext`, schema contract, idempotency, error envelope, 401/403 |
| **Boundary value analysis** | `data/BoundaryValues`, `PaymentBoundaryValueTest`, `docs/USER_STORIES.md` | Acceptance criteria → min-ε/min/min+ε/nominal/max-ε/max/max+ε. Full matrix at API level, edges in UI |
| **Synthetic financial test data** | `data/SyntheticDataFactory`, `FinancialIdentifiers`, `PiiMasker` | Test-BIN PANs with Luhn, mod-97 IBANs, seeded/reproducible, unique refs, masked logs |
| **Test data in a regulated env** | `docs/TEST_DATA_REGULATED.md`, `config/Secrets` | No prod data, no secrets in git, per-role test identities, audit and cleanup |
| **Flakiness at scale** | `docs/FLAKINESS.md`, `FlakinessPatternsTest`, pom `rerunFailingTestsCount` | Root causes, retries as a signal, flake report, quarantine lane |
| **CI/CD** | `/.github/workflows/banking-playwright-ci.yml`, `Jenkinsfile`, `Dockerfile` | build+unit → 4 E2E shards in pinned container → report + flake summary → nightly/UAT |

---

## 4. Real defects the suite found while it was being built

These make good interview stories, because they show *why* each technique exists:

1. **Back after sign-out showed account data.** API responses had no `Cache-Control: no-store`, so Chrome served `/api/me` from the HTTP cache during history navigation. Fixed on the server. The app now also re-validates pages restored from the back/forward cache (`pageshow`). *Found by* `sign_out_invalidates_the_session_server_side`.
2. **500 on an empty payment body.** `List.of(...).contains(null)` throws a NullPointerException. *Found by* the RBAC API probes, which intentionally send `{}`.
3. **Malformed JSON dropped the connection** instead of returning 400. *Found by* `malformed_json_and_unknown_resources`.
4. **Contract drift.** `authId` contained a UUID dash that the published schema forbids. *Found by* schema validation in the card BVA test.
5. **Framework-side lessons:**
   - `Pattern.quote()` produces `\Q…\E`, which the browser's JavaScript RegExp does not understand (see `TextPatterns`).
   - A bare `<th>` is not a `columnheader` role; you need `scope="col"`.
   - The **hydration race**: the page is visible before its JS listeners are attached (see `PaymentPage#open`).
   - A cached storage state can be fresh by age yet already dead on the server (see `AuthStateManager#acceptedByServer`).

---

## 5. Further reading in this repo

- [`docs/LOCATORS_AND_NETWORK_MOCKING.md`](docs/LOCATORS_AND_NETWORK_MOCKING.md): the technical deep dive
- [`docs/FLAKINESS.md`](docs/FLAKINESS.md): flakiness at scale, as a process
- [`docs/TEST_DATA_REGULATED.md`](docs/TEST_DATA_REGULATED.md): test data and secrets under PCI-DSS, GDPR and SOX
- [`docs/USER_STORIES.md`](docs/USER_STORIES.md): acceptance criteria and their BVA tables
- [`docs/CI_CD.md`](docs/CI_CD.md): pipeline stages explained
- [`docs/INTERVIEW_GUIDE.md`](docs/INTERVIEW_GUIDE.md): questions and model answers mapped to this code
