# Architecture

This note covers the job lifecycle, the async pipeline, the retrieve-then-
generate + verification design, the dual-provider persistence/determinism
boundary, and the reliability design that keeps output consistent despite
the LLM/media/judge pipeline being inherently non-deterministic.

## Job lifecycle

```
POST /generate
    │
    ├── rate limit / guardrail reject ──────────────────────────► REJECTED
    │   (no API call made at all)
    │
    ├── semantic match below threshold ─────────────────────────► REJECTED
    │   (out of scope -- only the 3 supported concepts are ever generated)
    │
    ├── matched topic already has a SUCCESS master row ─────────► SUCCESS (cache hit,
    │   (DB read only, no generation)                                        instant)
    │
    └── matched topic, no master yet -> enqueue Celery task, return 202
             │
             ▼
        QUEUED → PROCESSING (Retrieving → Scripting ⇄ Verifying → Rendering → Checking)
             │
             ├── gates never pass after MAX_REGENERATION_ATTEMPTS ─────────► FAILED_VERIFICATION
             ├── rendering fails after MAX_RENDER_ATTEMPTS ─────────────────► FAILED_RENDER
             ├── gate close to threshold, or a judge was unavailable ───────► HOLD_FOR_REVIEW
             ├── gate = DELIVER ────────────────────────────────────────────► SUCCESS
             └── Celery task itself exhausts MAX_TASK_RETRIES (API timeouts,
                 rate limits) ─────────────────────────────────────────────► FAILED_PERMANENT
                 (+ a row in dead_letter_jobs with the last exception)
```

`status` is the coarse machine-readable state (`app/schemas.py::JobStatus`).
`stage` is a human-readable label *within* `PROCESSING`, and `progress`
(0-100) is its numeric counterpart -- together, what `GET /jobs/{id}` exposes
as the progress bar a client renders. Every stage transition is persisted
with a timestamp, so stage latency is measurable after the fact.

**Critical design decision, unchanged in spirit from before, stronger in
practice: no video is ever generated at startup, and a topic is generated at
most once.** The first real request for a supported concept becomes that
concept's cached "master" job row (`is_seed=True` in the DB -- the original
seed-at-startup vocabulary, now meaning "the cached row for this topic",
created on first demand instead of at process start). Every later request
that matches the same topic reads that row and returns immediately; nothing
downstream of the semantic gate runs for it. This is what keeps the
pipeline's behavior bounded: the expensive, non-deterministic
generate+verify+render step runs at most 3 times ever, under full
retry/gate/fallback machinery, triggered by real demand rather than blocking
app startup.

## Async job pipeline (Celery)

`POST /generate` never blocks on generation. The guardrail chain, semantic
match, and cache-hit lookup are synchronous (fast, bounded cost); if none of
those resolve the request, `app/pipeline.py::handle_query` calls
`generate_topic_video_task.delay(job_id)` (`app/tasks.py`) and returns the
`QUEUED` job immediately (`202`). All actual work -- retrieval, scripting,
judging, rendering, QC -- runs on a Celery worker process, broker and result
backend both Redis (`REDIS_URL`).

- **Idempotency**: `POST /generate` accepts an `Idempotency-Key` header;
  the same key returns the existing job rather than creating a duplicate
  (`VideoJob.idempotency_key`, unique).
- **Retries with backoff**: the task retries itself (`self.retry(...)`) with
  exponential backoff (`TASK_RETRY_BACKOFF_SECONDS * 2^attempt`) on any
  unhandled exception (API timeouts, rate limits), up to `MAX_TASK_RETRIES`.
- **Dead-letter table**: a task that exhausts its retries writes a row to
  `dead_letter_jobs` (job id, topic, attempt count, the last exception) and
  the job is marked `FAILED_PERMANENT` -- never silently dropped.
- **Graceful shutdown**: `task_acks_late=True` (`app/celery_app.py`) means a
  worker that dies mid-task never acks it, so Celery redelivers it to
  another worker instead of losing it.
- **Tests run with `CELERY_TASK_ALWAYS_EAGER=true`**: the task executes
  synchronously in-process, so the test suite needs no Redis broker and no
  separate worker at all -- same "fully offline" philosophy as the rest of
  the suite.

## Retrieve-then-generate (`app/rag/`)

Script generation is grounded in retrievable material, not free-form LLM
output, and the ordering is mandatory: **retrieve, then generate** -- never
generate first and search for supporting material afterward (that produces
confirmation, not verification).

1. `app/rag/loader.py` parses `source_material/chemistry_source.pdf` (the
   single, committed source PDF) into topic-scoped chunks, split on
   `## <topic_id>` markers and then into ~500-char sentence-aligned chunks.
   Parsed once per process and cached.
2. `app/rag/store.py::retrieve(topic_id, query, k)` returns the top-k chunks
   for a topic most relevant to a query (embedding cosine similarity via the
   same LangChain embeddings boundary the semantic gate uses, with the same
   lexical-fallback degradation if no embedding backend is configured).
3. `app/llm/script_writer.py::generate_script` is hand the topic's *whole*
   indexed section (a handful of chunks for a corpus this small) and
   instructed to state nothing not present in it. The exact chunk ids used
   are threaded through and persisted on the job row (`source_chunk_ids`) --
   the source snapshot a script was written from is reproducible later.
4. The same chunks are what `app/judge/grounding.py` retrieves against when
   checking each generated claim -- generation and verification are grounded
   in the identical underlying text.

## The verification layer (`app/judge/`)

See README.md's "The verification layer" section for the full 7-step
pipeline (claim extraction → deterministic pre-check → grounding judge →
teaching-quality judge → mechanical QC → output-review judge → gate
aggregation) and the documented known limitations. Architecturally, the
points worth calling out:

- **One OpenAI wrapper module**: `app/llm/openai_judge_client.py` is the only
  place that calls OpenAI directly, with retry, timeout, and token
  accounting -- every judge module calls `judge_structured()`, never the SDK
  directly.
- **Independent of LLM_PROVIDER**: generation can run on Gemini, OpenAI, or
  Anthropic (`LLM_PROVIDER`); judging always runs on OpenAI, because the
  requirement is specifically an OpenAI LLM-as-judge.
- **No check is ever silently skipped**: a missing `OPENAI_API_KEY` makes
  `judge_structured()` raise `JudgeUnavailable`; every judge module catches
  that and returns a report with `skipped=True` and a `skip_reason`, which
  `app/judge/report.py` turns into `HOLD_FOR_REVIEW`, never a silent `DELIVER`.
- **Version-controlled rubric**: `rubrics/teaching_quality.yaml` is a data
  file, not an inline prompt string -- a rubric change is a reviewable diff.
- **QualityReport is the only thing gating decisions are made from**
  (`app/judge/report.py::build_quality_report`), and it's persisted whole on
  the job row (`quality_report` JSON column), so `GET /jobs/{id}/report`
  never re-runs anything.

## Dual-provider generation + determinism (`app/video/`, `app/db/`)

When a topic is generated, `app/video/factory.py::render_both` renders it
with **both** providers:

- **local** (Pillow slides + gTTS + ffmpeg) -- required; cheap; deterministic.
- **veo** (Google Veo) -- best-effort. A Veo failure is recorded
  (`veo_error` on the job row) and does not block delivery of the local
  video; "no check may be skipped silently" applies here too, so the failure
  reason is stored, not just an absent field.

A single Veo generation call only produces a short, silent clip
(`VEO_DURATION_SECONDS`, default 6s) -- looping that one clip for an entire
script's narration would show the same few seconds on repeat for the whole
video, and bill Veo's per-second rate for however long that loop runs (a
long script becomes an expensive video of one repeating clip). Instead,
`VeoProvider` generates one distinct clip **per script slide** (capped at
`VEO_MAX_SEGMENTS`, default 3), each prompted with that slide's own
heading/bullets so the visual tracks what's actually being said, muxed with
that slide's own narration, then concatenated (`app/video/veo_provider.py`
mirrors the same per-segment mux + ffmpeg-concat pattern the local provider
already uses for its slides). The concatenated result is then looped up to
`VEO_MIN_DURATION_SECONDS` (16s) if short, or trimmed down to
`VEO_MAX_DURATION_SECONDS` (20s) if long -- so both the visible repetition
*and* the cost are bounded regardless of script length: Veo only ever bills
for `min(slide_count, VEO_MAX_SEGMENTS) * VEO_DURATION_SECONDS` seconds of
raw generation, a fixed number independent of how long the narration is
(`RenderResult.billed_seconds` carries this for cost accounting, since it
can differ from the final clip's `duration_seconds`).

This doesn't eliminate repetition entirely -- within a single segment, the
raw clip is still looped to cover that segment's own (now much shorter)
narration chunk, since one Veo call can't natively extend a clip. Doing
that properly would need Veo's video-extension API, which is out of scope
here; splitting into multiple segments is what keeps this app's known
limitation bounded rather than fixing the underlying constraint.

Both renders, their metrics (duration, size, cost), and their own mechanical
QC results are persisted on the same job row
(`local_video_location`/`local_*` and `veo_video_location`/`veo_*` columns) --
one row never stores video bytes, only filesystem paths (swapping to a
signed S3/GCS URL later is a column-level change). A row for a topic that
already has a `SUCCESS` master (`app/db/repository.get_topic_master`) is
never regenerated: `handle_query` copies its fields onto the new request's
job row and returns immediately. **This is the determinism guarantee**: the
same topic always resolves to the same generated artifact and the same
quality scores, because it is generated exactly once.

## Persistence boundary

One `video_jobs` table (`app/db/models.py`), one row per request (topic
master or ordinary request), created automatically at startup if missing.
Every request is persisted, including ones rejected before any paid API call
-- `GET /jobs/{id}` and `GET /jobs` give a complete, queryable audit trail of
everything the service was ever asked, what it decided, and why (`stage`,
`error_code`, `error_message`, `quality_report`). A second table,
`dead_letter_jobs`, holds jobs whose Celery task exhausted every retry.

Primary store is Postgres (`DATABASE_URL`); falls back to a local SQLite
file if unreachable at startup (`DB_ALLOW_SQLITE_FALLBACK=true`, the
default) rather than refusing to start. The same SQLAlchemy models back
both; the test suite runs against a throwaway SQLite DB with no Postgres
involved.

## AI / video-generation boundaries

Three independent seams, because they have different constraints:

- **Text/embedding generation** goes through LangChain (`app/llm/factory.py`):
  `get_embeddings()` / `get_chat_model()`, picked by `LLM_PROVIDER`.
- **Judging** goes through `app/llm/openai_judge_client.py`, always OpenAI,
  independent of `LLM_PROVIDER` (see above).
- **Video generation** is called directly, not through LangChain (it has no
  video-generation abstraction): `app/video/factory.py` picks between
  `LocalProvider` and `VeoProvider` by `VIDEO_PROVIDER`, and `render_both`
  runs both regardless, for the dual-generation requirement. Both implement
  the same `VideoProvider.render(topic, script, job_id) -> RenderResult`
  interface, so nothing downstream branches on which one ran.

## Reliability under non-determinism

| Stage | What could go wrong | Safeguard |
|---|---|---|
| Embedding call | API down/quota/no key | Deterministic lexical (token-overlap) fallback; `match_method` on every job records which one ran |
| Script generation | Malformed/off-topic/ungrounded output | Structured-output schema validation + `must_mention` keyword check + retrieve-then-generate grounding, retried with explicit correction context up to `MAX_REGENERATION_ATTEMPTS`; a hand-written `curated_script` per topic is the last-resort fallback |
| Grounding / teaching-quality judge | OpenAI down/no key | Reports `skipped=True`; gate decision becomes `HOLD_FOR_REVIEW`, never a silent `DELIVER` |
| Video rendering | ffmpeg/provider failure, timeout | Retried up to `MAX_RENDER_ATTEMPTS`; Veo failure doesn't block the local variant (dual generation) |
| TTS | Network failure (gTTS) | Falls back to offline `pyttsx3`, then silent audio as a last resort (flagged in QC details) |
| Rendered output | Wrong/missing audio, corrupt file, off-topic narration | `app/qc/validator.py` (ffprobe + content check) AND `app/judge/output_review.py` (LLM coherence check) both feed the same gate |
| Celery task itself fails repeatedly | Transient infra failure (Redis blip, API timeout) | Exponential-backoff retry, then `dead_letter_jobs` + `FAILED_PERMANENT` -- never vanishes |
| Worker killed mid-task | Process crash / deploy | `task_acks_late=True` -- Celery redelivers the task instead of losing it |
| Input abuse / cost/load control | Junk, spam, prompt-injection, request floods | Rate limit -> rule-based input checks -- all free and fast, run before any paid call |

The result: repeated requests for the same 3 concepts converge on one
validated, quality-gated, audio-bearing, on-topic video each, generated
exactly once, and every failure mode has an understandable, recorded state
rather than a silent bad output or a crash.
