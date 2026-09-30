# CI/CD pipeline

Files: `/.github/workflows/banking-playwright-ci.yml` (GitHub Actions), `Jenkinsfile`, `Dockerfile`.

```
 PR / push to main
 ┌──────────────────┐     ┌──────────────────────────────────────────────┐     ┌─────────────────────────┐
 │ build-and-unit    │ ──► │ e2e  (matrix shard 1..4, fail-fast: false)   │ ──► │ report                  │
 │ compile + unit    │     │ container playwright/java:v1.56.0 (pinned)   │     │ JUnit check + flakes    │
 │ (no browser)      │     │ -Pci retries=2, traces on failure            │     │ summary in job summary  │
 └──────────────────┘     └──────────────────────────────────────────────┘     └─────────────────────────┘
 nightly (cron):  + cross-browser (firefox, webkit)   + quarantine lane (continue-on-error)
 manual:          UAT smoke → protected GitHub Environment "uat" (required reviewers, env-scoped secrets)
```

| Design choice | Why |
|---|---|
| Unit stage first, without a browser | fail in about 1 minute if generators, BVA or schemas are broken; saves E2E minutes |
| Pinned Playwright container | the same browser build as the pom, so no "passed locally" drift. Bump the pom and image in one PR |
| `--ipc=host` | Chromium needs more shared memory than Docker's 64 MB default, otherwise it crashes randomly |
| 4 shards + in-JVM parallelism 4 | 16 concurrent contexts; wall time ≈ longest shard |
| `fail-fast: false` | see every failing shard in one run |
| `concurrency: cancel-in-progress` | superseded PR pushes don't waste runners |
| Retries only in CI, reported as flaky | green builds stay trustworthy; flakes remain visible |
| Artifacts only on failure, 7-day retention | traces can contain data; keep less, keep it for less time |
| Never upload `target/.auth` | live session cookies |
| Secrets as env vars from the secret store | read by `Secrets`, masked in logs, never written to disk |
| UAT job behind a protected Environment | change control: approvals and an audit trail for runs against shared envs |

Jenkins mirrors this: one Docker agent per shard (separate workspaces), `withCredentials` for UAT, `junit` + `archiveArtifacts` in `post`, and cleanup of `.auth`.

Local equivalent of a CI shard:

```bash
docker build -t nb-e2e banking-playwright-framework
docker run --rm --ipc=host nb-e2e mvn -B -o test -Pci -Dshard=1/4 -DexcludedGroups=unit,quarantine
```
