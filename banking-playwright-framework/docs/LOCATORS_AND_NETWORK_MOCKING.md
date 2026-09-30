# Technical deep dive: locators and network mocking

Executable companions: `LocatorDeepDiveTest`, `NetworkMockingDeepDiveTest`, `components/Combobox`, `mock/NetworkMocks`.

---

## Part A: Locators

### A1. What a Locator is (and isn't)

A `Locator` is a **lazy query**: a serialisable chain of selector parts plus options. Creating one does nothing. Each time you act (`click`, `fill`) or assert (`assertThat(loc).hasText`), Playwright:

1. sends the selector chain to an **injected script** in the page,
2. resolves it against the *current* DOM (piercing open shadow roots),
3. enforces **strictness**: an action needs exactly one match, otherwise `strict mode violation`,
4. runs **actionability checks** (below),
5. performs the action, and on failure **retries from step 2** until the timeout.

Consequences:

- **No stale elements.** Contrast with `ElementHandle`, which pins one DOM node (see `locators_never_go_stale_but_element_handles_do`).
- Page objects can declare locators before the elements exist.
- Scoping by chaining (`row.getByRole(BUTTON, "Approve")`) is cheap and precise.

### A2. Actionability, i.e. built-in auto-waiting

| Action | attached | visible | stable | receives events | enabled | editable |
|---|:-:|:-:|:-:|:-:|:-:|:-:|
| `click`, `dblclick`, `check`, `hover`, `tap` | ✓ | ✓ | ✓ | ✓ | ✓ (not hover) | |
| `fill`, `clear` | ✓ | ✓ | | | ✓ | ✓ |
| `selectOption` | ✓ | ✓ | | | ✓ | *(and waits until the requested options exist)* |
| `textContent`, `getAttribute`, `count`, `allInnerTexts` | *(`count`/`all*` do not wait at all)* | | | | | |

- **"Stable"** means the same bounding box in two consecutive animation frames, so the click doesn't land mid-animation.
- **"Receives events"** means a hit-test at the click point returns the element (not a modal backdrop or cookie banner).
- If a check fails, Playwright waits and retries. If the element is replaced (React re-render), it re-resolves.
- **Not covered by auto-waiting:**
  - business-level eventual consistency (a status changing later): use web-first assertions with a timeout, or `Poller`.
  - **hydration** (element visible before its JS listener is attached): wait for an app readiness signal (`PaymentPage#open`).
  - non-retrying reads like `count()` and `allInnerTexts()`. Wait first (`assertThat(loc).hasCount(n)`), then read.

**Web-first assertions** (`PlaywrightAssertions.assertThat(locator|page|apiResponse)`) poll until they pass or time out. `assertThat(loc).isVisible()` is correct. `assertTrue(loc.isVisible())` is a flaky one-shot check.

### A3. Locator priority

```
1  getByRole(role, name)        ← default choice: user-facing, a11y-enforcing, survives refactors
2  getByLabel / getByPlaceholder / getByText / getByAltText / getByTitle
3  getByTestId("...")           ← explicit contract when there's no good accessible name
4  locator("css")               ← scoped, meaningful attributes only: tr[data-batch-id='STL-…']
5  locator("xpath=…")           ← last resort: verbose, ordering-sensitive, does NOT pierce shadow DOM
```

**Accessible name** is computed like a screen reader does: `aria-labelledby` › `aria-label` › `<label for>` / wrapping label › text content › `title`. That's why the sample app uses `aria-label` on tables, `aria-labelledby` on sections and dialogs, and `scope="col"` on header cells. Good markup is what makes good locators possible.

### A4. Matching rules

| API | Default | `setExact(true)` | `Pattern` |
|---|---|---|---|
| `getByText`, `getByLabel`, role `name`… | substring, case-insensitive, whitespace-normalised (`&nbsp;` → space) | full string, case-sensitive | JS regex |
| `filter(hasText)` | substring over the element's **whole** text | — | JS regex over the whole text |
| `assertThat(loc).hasText` | full, normalised | — | regex |
| `assertThat(loc).containsText` | substring | — | regex |

Two gotchas, both hit and fixed while building this suite:

- **Java `Pattern`s run as JavaScript RegExps in the browser.** `Pattern.quote()` emits `\Q…\E`, which JS doesn't support, so the regex silently matches nothing. Use `TextPatterns.escape()`.
- **`hasText` on a row sees the concatenated row text** (`2026-09-01Card payment - Tesco£54.20Posted…`). An anchored `Tesco$` never matches. Filter by an exact **cell** instead (`filter(has(cell "… Tesco" exact))`).

### A5. Composition toolbox

```java
table.getByRole(ROW)                                   // all rows
     .filter(new FilterOptions().setHasText("Brightwater"))        // by text
     .filter(new FilterOptions().setHas(page.getByRole(CELL, name("Pending").setExact(true))))  // by child
     .filter(new FilterOptions().setHasNot(page.getByRole(COLUMNHEADER)))                       // exclusion
     .getByRole(BUTTON, name("Dispute"));              // scoped action target

page.getByRole(BUTTON, name("Dispute")).and(page.locator(":enabled"))   // intersection (same element)
page.getByText("Posted").or(page.getByText("Pending"))                  // union (either)
locator.first() / last() / nth(i)                                       // positional: avoid for actions
page.frameLocator("iframe[title='3-D Secure challenge']").getByLabel("One-time passcode")
page.getByTitle("E-signature widget").contentFrame().locator("#done")  // 1.43+: element → its frame
```

### A6. Pattern catalogue (all in `playground.html` / the app)

| UI pattern | Robust locator | Test |
|---|---|---|
| Random ids / hashed CSS classes | `getByRole(BUTTON, "Generate statement")`, `getByTestId`, `[id^='btn-']` | `dynamic_ids…` |
| Web component (open shadow DOM) | `getByText` / `getByTestId` / CSS pierce automatically; XPath doesn't | `shadow_dom…` |
| Many similar buttons ("Pay", "Pay now", "Pay later") | `setExact(true)`; strictness catches ambiguity | `text_matching…` |
| Content that appears later | web-first assertion, `waitFor()` | `auto_waiting…` |
| Row action in a table | row by business key, then button inside the row | `tables…`, `ApprovalsPage` |
| Disabled until consent | `isDisabled`, then `check()`, then `isEnabled`. `click` waits for enabled | `actionability…` |
| iframe (3-D Secure, e-signature) | `frameLocator` / `contentFrame` | `iframes…`, `CardAuthorizationTest` |
| Tooltip | `hover()` then `getByRole(TOOLTIP)` | `hover…` |
| Native `<select multiple>`, radios | `selectOption(String[])` / `SelectOption.setLabel`; `getByRole(RADIO).check()` | `native_multi_select…` |
| Div-soup dropdown (no ARIA) | scoped CSS container + text; raise an a11y defect | `legacy_div_dropdown…` |
| Re-rendered list | Locator re-resolves; ElementHandle goes stale | `locators_never_go_stale…` |
| **Async ARIA combobox** (dynamic dropdown) | `getByRole(COMBOBOX)` → `fill` → `getByRole(OPTION, name ^…)` → `click` | `Combobox`, `dynamic_dropdown…` |
| Native select with async options | `selectOption` waits for the option to exist | `PaymentPage#fill` |

### A7. Dynamic dropdown anatomy (`Combobox`)

```
fill("Acme") ──► input event ──► 250 ms debounce ──► GET /api/beneficiaries?q=Acme (250-700 ms)
                                      │                       │
                           aria-expanded=true       race guard drops stale responses
                           "Searching…" row          options rendered with random ids
```

1. Wait for `aria-expanded="true"`. This proves the listener fired, which covers the hydration race.
2. Target the option by **role + accessible name with a start anchor** (`^Acme Supplies Ltd`). The name also contains the masked IBAN, so an exact match would fail. The anchor stops "Acme Supplies Ltd (Old)" from matching too.
3. `click()` auto-waits for the option to be attached, visible and stable.
4. Verify the outcome: `input` has the value and the listbox collapsed.
5. For counts, use `assertThat(options).hasCount(2)` (retries), never `options.count()` right after typing.
6. Keyboard path (`ArrowDown` / `Enter`) for accessibility compliance.

---

## Part B: Network mocking

### B1. Mechanics

`page.route(matcher, handler)` or `context.route(...)` registers an interceptor in the **browser's network stack** (CDP `Fetch` in Chromium, equivalents in Firefox and WebKit). A matching request is **paused** and handed to your handler, which must finish it **exactly once**:

| Call | Effect | Typical use |
|---|---|---|
| `route.fulfill(opts)` | synthetic response; the server is never hit | stub a 3rd party, error states, schema-generated bodies |
| `route.resume(opts)` | continue to the real server, optionally changing url/method/headers/postData | inject headers, feature flags, rewrite env URLs |
| `route.abort(code)` | network-level failure (`timedout`, `connectionrefused`, `internetdisconnected`, `failed`…) | offline and timeout behaviour |
| `route.fetch()` + `fulfill(setResponse(real).setBody(patched))` | real call, then modify the response | edge values in otherwise real data |
| `route.fallback()` | hand over to the next matching handler | layered/conditional handlers |

A handler that calls none of these leaves the request **hanging**. That's a common bug; it's also used deliberately by `NetworkMocks.hold()` to test loading states deterministically.

### B2. Matching

- **Glob:** `*` = any characters except `/`, `**` = any characters including `/`, `?` = a **literal** `?`. `"**/external/fx/rates**"` matches with or without a query string.
- **`Pattern`:** a full-URL regex (a JS RegExp, see A4).
- **`Predicate<String>`:** arbitrary logic on the URL.

### B3. Precedence, scope and lifetime

- **Last registered, first evaluated.** A later, more specific handler can `fallback()` to an earlier, generic one.
- **Page routes are checked before context routes.** Context routes also cover popups/new tabs and requests made before a `Page` exists, so use them for suite-wide stubs (`blockTrackers`).
- `new Page.RouteOptions().setTimes(n)` removes a handler after n uses. `unroute(url)` / `unrouteAll()` remove handlers explicitly.
- **Not intercepted:** `APIRequestContext` traffic (it isn't browser traffic), and requests served by a Service Worker unless you set `NewContextOptions.setServiceWorkers(BLOCK)`.
- **Java threading:** handlers run on the test thread whenever it is inside a Playwright call. **Never `Thread.sleep` in a handler**, because it freezes the event loop. Use `hold()` + `page.waitForCondition()` instead.

### B4. Observation without mocking

```java
Request req = page.waitForRequest(r -> r.url().endsWith("/api/payments") && r.method().equals("POST"),
                                  payments::confirm);          // arm the listener BEFORE the action
req.postData(); req.headerValue("idempotency-key"); req.response().status();
page.onRequest(...) / page.onResponse(...) / NetworkMocks.recordRequests(page, pattern)
```

Used to prove *what the UI sent*: amounts normalised to strings, Idempotency-Key present, card data never in URLs or sent to third parties.

### B5. Contract-safe mocks: `MockSchemaGenerator`

```java
JsonNode fx = mocks.generateValid("fx-rates.schema.json", Map.of("/rates/EUR", 1.2345));
NetworkMocks.fulfillJson(context, "**/external/fx/rates**", 200, fx);
```

- Walks the JSON Schema: `type`, `properties`, `items`/`minItems`, `enum`, `const`, `examples`, `default`, `format`, `minimum`/`maximum`, local `$ref`.
- The vendor keyword **`x-mock`** maps a field to a domain generator: `iban`, `company`, `amount`, `reference`, `id:PAY-`. Values look like banking data rather than `"string"`.
- **JSON-Pointer overrides** keep each test's intent to one line.
- **Validates its own output.** When the provider adds a required field, every mock fails at once, instead of the suite staying green against a contract that no longer exists (`mock_that_drifts_from_the_contract_is_rejected`).
- `generateUnchecked` is for deliberately malformed payloads (negative tests).

### B6. HAR record and replay

`context.routeFromHAR(har, setUrl(glob).setUpdate(true))` records; the HAR is written when the context closes. `setUpdate(false)` replays. `setNotFound(FALLBACK | ABORT)` decides what happens to unmatched requests. **Scrub HARs before committing**: they contain cookies, tokens and PII.

### B7. What to mock in a bank, and what not to

| Mock | Keep real |
|---|---|
| FX/market-data providers, credit bureaus, card schemes, SMS/e-mail gateways | the SUT's own payment/settlement APIs in E2E tests |
| error states: 5xx, timeouts, malformed payloads | authentication (use real login plus storage state) |
| rare UI states (zero accounts) in *component-level* UI tests | anything a regulator asks you to evidence end to end |
| analytics, trackers, fonts (speed and determinism) | |
