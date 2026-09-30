# Test data and secrets in a regulated environment

Relevant regimes for a bank: **PCI-DSS** (card data), **GDPR / UK GDPR** (personal data), **banking secrecy**, **SOX / SOC 2** (change and audit controls), **FCA SYSC / PRA** operational-resilience expectations. The test estate is in scope too. Auditors ask where test data comes from, who can see it, and how it's destroyed.

## Principles implemented in this framework

| # | Principle | Implementation |
|---|---|---|
| 1 | **No production data in non-prod.** Not even "masked" copies: masking is error-prone and re-identification is real. | `SyntheticDataFactory` generates everything |
| 2 | **Structurally valid, provably fake.** Data must pass edge validation (check digits) to reach the logic under test. | Luhn-valid PANs in **test BIN ranges** (400000 / 400005 / 400051); IBANs with mod-97 check digits and a fictitious bank code `TEST`; sort codes in the 99-xx-xx range |
| 3 | **Outcome-driven test data.** | Card BIN → issuer-simulator outcome (approve / 05 / 51), like real scheme test cards |
| 4 | **Deterministic and reproducible.** | Run seed logged; per-test seed = run seed ⊕ test id, so data doesn't depend on parallel scheduling. Re-run with `-Ddata.seed` |
| 5 | **Unique, owned, traceable.** Every record created by automation is identifiable. | `uniqueReference()` = `QA-<runId>-<seq>-<rand>` (≤ 18 chars, per the business rule). The `QA` prefix supports audit queries and cleanup jobs |
| 6 | **Isolation for parallel runs.** | Unique references; assertions by business key; "contains mine", never "only mine" |
| 7 | **Masking at every output.** | `PiiMasker` on API logs (PAN 6+4, IBAN, password/otp/cvv/token fields); UI and API assert that only masked PANs are shown or returned (`card-authorization.schema.json` forbids `pan`/`cvv` in responses) |
| 8 | **Secrets never in the repo.** | `Secrets` reads `BANK_AUTH_<ROLE>_{USERNAME,PASSWORD,TOTP_SECRET}` from the environment (CI secret store / Vault agent). Fallbacks exist **only** for the in-process sample app; any other env fails fast. `Credential.toString()` is redacted |
| 9 | **Functional test identities per role.** | One non-personal account per role, so every action in the audit log is attributable to automation. MFA stays on, via TOTP seeds held in the vault |
| 10 | **Session material is sensitive.** | `target/.auth/*.json` hold live cookies. They're git-ignored, never uploaded as CI artifacts, and wiped after Jenkins builds |
| 11 | **HAR files and traces can leak.** | HARs are git-ignored by default; traces are kept only for failures, with short retention (7 days) |
| 12 | **Side-effect-free probes on shared envs.** | RBAC probes use invalid bodies or unknown ids, so authorised calls return 422/404 and create nothing |

## Data lifecycle on a shared UAT environment

```
seed (API, not UI) → use (unique refs) → verify → clean up / expire
       ▲                                           │
       └── reference data (accounts, beneficiaries) is provisioned once per env by a data job,
           versioned, and treated as read-only by tests
```

- Prefer **creating data through APIs** over the UI: faster, and not dependent on UI flakiness.
- Prefer **idempotent seeding** (upserts keyed by a stable id) so re-runs don't accumulate junk.
- Money: `BigDecimal` in Java, **strings on the wire**, and never `double` (0.1 + 0.2 ≠ 0.3).
- Time: always an explicit zone (`Europe/London`). Cut-offs and "today" are business-calendar concepts.
