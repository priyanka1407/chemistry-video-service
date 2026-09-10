# Architecture

This note covers the three things the service is organized around: the job
lifecycle, the persistence/artifact boundary, and the AI/video-generation
boundary -- plus the reliability design that keeps output consistent despite
the LLM/media pipeline being inherently non-deterministic.

## Job lifecycle

```
                         ┌── guardrail reject ──► REJECTED
                         │   (rate limit / spam / injection,
                         │    no API call made)
PENDING ──► PROCESSING ──┤
                         ├── below similarity ──► REJECTED
                         │   threshold (out of scope)
                         │
                         ├── matched, no seed video yet ──► FAILED
                         │   (edge case: topic exists but
                         │    its seed never rendered)
                         │
                         └── matched, seed exists ──► SUCCESS
                             (cache hit -- returns the
                              existing video, nothing rendered)
```

`status` is the coarse machine-readable state (`PENDING / PROCESSING /
SUCCESS / REJECTED / FAILED`, `app/schemas.py::JobStatus`). `stage` is a
human-readable progress string *within* `PROCESSING`
(`Started -> Matching -> ...`) -- this is what `GET /jobs/{id}` exposes so a
polling client always has something concrete to show, not just "still
working." `SUCCESS`, `REJECTED`, `FAILED` are terminal.

Startup runs a **separate** lifecycle for the 3 seed rows
(`is_seed=true`): `Started -> Scripting -> Rendering (attempt N) ->
Validating -> Completed`, looping the render+validate step up to
`MAX_RENDER_ATTEMPTS` times before giving up. This is the only place actual
generation happens -- see below.

**Critical design decision: user queries never generate a video.** A
request either matches an already-rendered seed (served instantly from the
DB row's `video_location`) or is rejected. This is what keeps the pipeline's
behavior bounded and auditable: the expensive, non-deterministic
generation step runs exactly 3 times, once each, under controlled
conditions at startup with full retry/validation/fallback machinery; the
request-serving path is just an embedding call and a threshold check.

## Persistence / artifact boundary

One `video_jobs` table (`app/db/models.py`), one row per request (seed or
user), created automatically at startup if missing
(`Base.metadata.create_all` -- idempotent). A row never stores video bytes,
only `video_location` (a filesystem path today). Swapping that for a signed
S3/GCS URL later is a one-column, one-writer change -- nothing else in the
pipeline reads or writes artifact bytes directly; they only ever pass
through as a `Path` inside `RenderResult` between the video provider, the QC
validator, and the row write.

Every request is persisted, including ones rejected before any paid API
call -- so `GET /jobs/{id}` and `GET /jobs` give a complete, queryable audit
trail of everything the service was ever asked, matching what it decided,
and why (`stage`, `error_code`, `error_message`, `validation_details`).

Primary store is Postgres (`DATABASE_URL`). If it's unreachable at startup,
the service falls back to a local SQLite file
(`DB_ALLOW_SQLITE_FALLBACK=true`, the default) rather than refusing to
start, and reports the degraded state on `/health` -- set it to `false` to
make an unreachable Postgres a hard startup failure instead. The same
SQLAlchemy models back both, so this is a connection-string difference, not
a code path difference; the test suite uses this same mechanism to run
against a throwaway SQLite DB with no real Postgres involved at all.

## AI / video-generation boundary

Two independent seams, because they have different constraints:

**Text and embeddings go through LangChain** (`app/llm/factory.py`):
`get_embeddings()` and `get_chat_model()` return plain LangChain
`Embeddings` / `BaseChatModel` objects, picked by `LLM_PROVIDER`
(google/openai/anthropic). Every other module -- the semantic gate, the
script writer -- calls only these two functions and never imports a
provider SDK directly. Moving off Gemini is a `LLM_PROVIDER` env change plus
installing that provider's `langchain-<provider>` package; no other file
changes.

**Video generation is called directly**, not through LangChain, because
LangChain has no video-generation abstraction to standardize on.
`app/video/factory.py` picks between `LocalProvider` (Pillow + TTS +
ffmpeg) and `VeoProvider` (`google-genai` called directly) by
`VIDEO_PROVIDER`; both implement the same `VideoProvider.render(topic,
script, job_id) -> RenderResult` interface (`app/video/base.py`), so
`app/pipeline.py` never branches on which one is active. This is the one
place in the codebase that is provider-specific by necessity, and it's
isolated to exactly one file per provider.

Adding a 4th STEM topic later: add one `Topic` entry to `app/topics.py`
(question, phrasings, `must_mention` keywords, optionally a
`curated_script` fallback) and, if it should be pre-rendered, list its id in
`SEED_TOPIC_IDS`. Nothing else changes -- the semantic gate, guardrails,
pipeline, and both video providers are all topic-agnostic.

## Reliability under non-determinism

LLMs and media generation return something different every run, so nothing
downstream trusts a single successful call. Concretely, per stage:

| Stage | What could go wrong | Safeguard |
|---|---|---|
| Embedding call | API down/quota/no key | Falls back to a deterministic lexical (token-overlap) matcher; `match_method` on every job says which one actually ran, so degraded matching is never silent |
| Script generation | Malformed/off-topic/empty output | Structured-output schema validation (2-4 slides, non-empty fields) + a `must_mention` keyword check, retried up to `MAX_SCRIPT_ATTEMPTS`; if every attempt still fails, a hand-written `curated_script` per topic is used instead (`script_source=curated`) -- a flaky LLM can never fully block a seed from existing |
| Video rendering | ffmpeg/provider failure, timeout | Retried up to `MAX_RENDER_ATTEMPTS`; if `VIDEO_PROVIDER=veo` fails, optionally falls back to the local renderer (`FALLBACK_TO_LOCAL`) rather than failing the seed outright |
| TTS | Network failure (gTTS) | Falls back to offline `pyttsx3`, then to silent audio as a last resort (flagged in QC details) -- narration text is still recorded even if it couldn't be spoken |
| Rendered output | Wrong/missing audio track, corrupt file, off-topic narration slipped through | `app/qc/validator.py`: ffprobe-verified video+audio streams, sane duration/resolution/file size, AND a second independent `must_mention` content check against the narration that was actually produced -- both must pass before a job is marked `SUCCESS`; failure re-triggers the render retry loop above |
| One seed topic still fails everything | A third bad concept shouldn't break the other two | That row is marked `FAILED` with `error_message`; the app still starts; `/health` lists it under `topics_degraded`; a future user query matching that topic gets a clear "video unavailable" `FAILED` result instead of a silent wrong answer or a crash |
| Input abuse / cost/load control | Junk, spam, prompt-injection, request floods | A layered guardrail chain runs before any paid call: per-IP rate limit, then rule-based input checks (length, gibberish, repeated-character spam, injection-phrase denylist) -- all free and fast; only input that survives both reaches the embedding call |

The result: repeated runs of the same 3 concepts converge on a validated,
audio-bearing, on-topic video every time, and every failure mode has an
understandable, recorded state rather than a silent bad output or a crash.
