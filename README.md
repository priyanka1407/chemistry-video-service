# Chemistry Video Request Service

A FastAPI backend that turns a learner's chemistry question into a short
explainer video (slides + narration + audio). Only 3 concepts are supported:

- How does the pH scale work?
- Why do atoms form covalent bonds?
- What is the difference between ionic and covalent bonding?

**Generation happens on demand, never at startup.** The first request for a
supported concept triggers a full retrieve-then-generate-then-verify
pipeline on a Celery worker (script written only from
`source_material/chemistry_source.pdf`, checked by an OpenAI grounding judge
and a teaching-quality judge, rendered with **both** gTTS and Veo, mechanically
QC'd) and returns a job id immediately. Every later request for that same
concept (or a semantically close paraphrase, e.g. "explain PH") is served
straight from the database -- deterministic, no regeneration. Any other
query is rejected. See [ARCHITECTURE.md](ARCHITECTURE.md) for the full
pipeline design and [`app/judge/`](app/judge) for the verification layer.

## Prerequisites

- Python 3.11 (3.10-3.12 should also work; avoid 3.13+ until dependencies catch up)
- **ffmpeg + ffprobe on PATH** (the local renderer's video encoder and the QC
  validator's media inspector). On Windows: `winget install -e --id Gyan.FFmpeg`
- **Redis** running locally (broker + result backend for the async job pipeline)
- PostgreSQL reachable (optional -- falls back to a local SQLite file if not; see below)
- A Google AI Studio API key (`GOOGLE_API_KEY`) for script generation + embeddings
  (the service runs on the curated/lexical fallback path without one -- see Reliability below)
- An **OpenAI API key** (`OPENAI_API_KEY`) for the LLM-as-judge (grounding/faithfulness,
  teaching quality, final output review). Without one, those gates are recorded as
  `SKIP` (never silently passed) and jobs land in `HOLD_FOR_REVIEW`.

## Setup

```bash
python -m venv .venv
.venv\Scripts\Activate.ps1      # Windows PowerShell
pip install --upgrade pip
pip install -r requirements.txt
copy .env.example .env          # then edit .env
python scripts/generate_source_material.py   # (re)builds source_material/chemistry_source.pdf
redis-server &                                # broker/result backend
```

## Run

Two processes: the API, and a Celery worker that does the actual generation.

```bash
# terminal 1
celery -A app.celery_app worker --loglevel=info

# terminal 2
uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

**On Windows**, Celery's default worker pool (`prefork`) relies on Unix
`fork()` and fails with `PermissionError: [WinError 5] Access is denied`.
Use the single-process `solo` pool instead (fine for local dev -- only one
job runs at a time either way):

```powershell
celery -A app.celery_app worker --loglevel=info --pool=solo
```

There's also no native Windows build of `redis-server` -- run it via WSL,
Docker (`docker run -p 6379:6379 redis`), or Memurai instead of installing
it directly.

Startup only ensures the `video_jobs` table exists -- **no video is generated
at startup**. Open http://127.0.0.1:8000/ for a minimal page with a progress
bar (polls `GET /jobs/{id}` every 1.5s), or http://127.0.0.1:8000/docs for
the full API.

For a quick local run without Redis/a separate worker at all (e.g. on
Windows, to sidestep both quirks above), set `CELERY_TASK_ALWAYS_EAGER=true`
-- `POST /generate` then runs the whole pipeline synchronously inline (this
is what the test suite does), and you only need `uvicorn` running.

## API

| Endpoint | Method | Purpose |
|---|---|---|
| `/generate` | POST | Submit a learner query (`{query, audience_age?}`). `Idempotency-Key` header supported. Returns a job immediately: `200` if served from cache/rejected, `202` if queued for generation. |
| `/jobs/{id}` | GET | Poll a job: `status`, `stage`, `progress` (0-100, the progress-bar field), quality scores once available, artifact locations. |
| `/jobs/{id}/report` | GET | Full `QualityReport`: faithfulness score, contradicted claims, per-dimension teaching-quality scores, mechanical + LLM output checks, gate decision + reason. |
| `/jobs/{id}/video` | GET | Download the video (`?variant=local\|veo`, defaults to `DEFAULT_VIDEO_DELIVERY_PROVIDER`). |
| `/jobs/{id}` | DELETE | Cancel a queued/processing job. |
| `/jobs` | GET | List recent jobs. |
| `/topics` | GET | The 3 supported concepts and example phrasings. |
| `/health` | GET | DB/provider readiness, and which topics already have a generated+gated video. |
| `/` | GET | Minimal HTML page: pick a question, submit, watch the progress bar, then the video. |

```bash
# First request for a topic -- generates it (slow: scripting + 2 judge passes + dual render + QC)
curl -s -X POST http://127.0.0.1:8000/generate \
  -H "Content-Type: application/json" -d "{\"query\": \"How does the pH scale work?\"}"

# A paraphrase of an already-generated topic -- instant cache hit
curl -s -X POST http://127.0.0.1:8000/generate \
  -H "Content-Type: application/json" -d "{\"query\": \"explain PH\"}"

# Out of scope -- rejected, nothing queued
curl -s -X POST http://127.0.0.1:8000/generate \
  -H "Content-Type: application/json" \
  -d "{\"query\": \"who is the winner of world cup 2026?\"}"

# Poll a job (progress bar fields: status, stage, progress)
curl -s http://127.0.0.1:8000/jobs/<job_id>

# Full quality report
curl -s http://127.0.0.1:8000/jobs/<job_id>/report

# Fetch the finished video (gTTS by default; ?variant=veo for the Veo clip)
curl -s -o out.mp4 http://127.0.0.1:8000/jobs/<job_id>/video
```

## Source material (`source_material/`)

`source_material/chemistry_source.pdf` is the **only** material the pipeline
is allowed to write a script from. It has one section per supported topic
(marked with an invisible `## <topic_id>` tag the loader parses on), each a
few short paragraphs of accurate chemistry content. Regenerate it with:

```bash
python scripts/generate_source_material.py
```

`app/rag/loader.py` parses the PDF into topic-scoped chunks at first use;
`app/rag/store.py` retrieves the most relevant chunks for a given question or
claim (embedding cosine similarity, with the same lexical fallback the
semantic gate uses if no embedding backend is configured). Script generation
(`app/llm/script_writer.py`) is handed the topic's whole indexed section
(a handful of chunks) and instructed to state nothing that isn't in it; the
exact chunk ids used are persisted on the job row (`source_chunk_ids`) so the
source snapshot a script was written from is reproducible and auditable
later.

## The verification layer (`app/judge/`)

This is the part of the system that decides whether a rendered video is
allowed to ship -- a job is not "done" because rendering succeeded, it's done
because it passed its quality gates. Every job's full `QualityReport` is
persisted (`GET /jobs/{id}/report`), so "how many of the last N videos were
factually grounded" is a query, not a re-watch.

1. **Claim extraction** (`app/judge/claims.py`) -- an OpenAI structured-output
   call decomposes the narration into atomic claims, tagging each
   `is_factual` (opinion/framing/transitions are excluded from scoring but
   still logged).
2. **Deterministic pre-check** (`app/judge/grounding.py::deterministic_pre_check`) --
   every number in the script must literally appear in the source material.
   Free, exact, catches the worst hallucination class before spending a
   single judge token.
3. **Grounding / faithfulness judge** (`app/judge/grounding.py`) -- for each
   factual claim, retrieve its top-k source chunks and ask OpenAI for a
   structured `SUPPORTED | CONTRADICTED | NOT_FOUND` verdict, bounded
   concurrency. `faithfulness = supported / total_factual_claims`; any
   `CONTRADICTED` is a hard fail regardless of the aggregate.
4. **Teaching-quality judge** (`app/judge/teaching_quality.py`) -- scores the
   script 1-5 on 5 dimensions from the version-controlled
   [`rubrics/teaching_quality.yaml`](rubrics/teaching_quality.yaml) (explicit
   band descriptors + few-shot examples, not "score 5 if excellent"),
   weighted into an aggregate.
5. **Mechanical output QC** (`app/qc/validator.py`, unchanged, no LLM) --
   ffprobe-verified video/audio streams, sane duration, on-topic keyword check.
6. **Output review judge** (`app/judge/output_review.py`) -- an OpenAI review
   of the finished script + narration + mechanical QC facts, checking the
   result reads as coherent and on-topic (a text-only proxy for the spec's
   optional vision-based frame spot-check -- see Known limitations).
7. **Gate aggregation** (`app/judge/report.py::build_quality_report`) --
   rolls all of the above into one `QualityReport` with a `gate_decision`:
   `DELIVER`, `REGENERATE` (below threshold -> up to `MAX_REGENERATION_ATTEMPTS`
   retries, feeding the specific failed claims/dimensions back as correction
   context), `HOLD_FOR_REVIEW` (close to threshold, or a judge was
   unavailable -- never silently treated as a pass), or `REJECT` (hard
   contradiction).

**Known limitations:**
- **Faithfulness vs. truth.** The grounding judge measures faithfulness to
  `source_material/chemistry_source.pdf`, not objective truth. If that PDF
  were wrong, a script faithful to it would still score 1.0. That is the
  correct trade-off here -- the PDF is the one artifact a human can audit and
  correct -- but it must be stated, not assumed.
- **Judge variance.** LLM judges are not perfectly consistent between calls;
  no golden-set calibration harness is included in this iteration (only the
  gating/aggregation logic is unit-tested against constructed reports, see
  `tests/test_judge.py`).
- **Output review has no eyes.** `app/judge/output_review.py` never inspects
  actual video frames -- it cannot catch wrong visuals or garbled on-screen
  text, only an incoherent/off-topic narration.
- **Race on a topic's very first two concurrent requests.** Two requests for
  the same brand-new topic arriving before either has finished generating
  will not deduplicate perfectly (see `app/db/repository.get_pending_topic_master`) --
  acceptable given there are only 3 topics, each generated once.

## Configuration

Every tunable lives in `.env` (see `.env.example` for the full, commented
list) and is read once in `app/config.py` -- nothing is hardcoded in
application code. Highlights beyond the original LLM/video-provider knobs:

| Variable | Default | Meaning |
|---|---|---|
| `OPENAI_API_KEY` | empty | Required for the LLM-as-judge (grounding, teaching-quality, output review) |
| `OPENAI_JUDGE_MODEL` | `gpt-4o-mini` | Judge model id |
| `FAITHFULNESS_THRESHOLD` | `0.8` | Minimum `supported/total_factual_claims` to pass grounding |
| `TEACHING_QUALITY_THRESHOLD` | `3.5` | Minimum weighted rubric aggregate (1-5 scale) |
| `MAX_REGENERATION_ATTEMPTS` | `2` | Regeneration attempts after a failed gate, before `FAILED_VERIFICATION` |
| `REDIS_URL` | `redis://localhost:6379/0` | Celery broker + result backend |
| `CELERY_TASK_ALWAYS_EAGER` | `false` | `true` runs generation synchronously inline, no worker/Redis needed (used by tests) |
| `ENABLE_DUAL_VIDEO_GENERATION` | `true` | Render both gTTS(local) and Veo for every generated topic |
| `DEFAULT_VIDEO_DELIVERY_PROVIDER` | `local` | Which variant `GET /jobs/{id}/video` streams by default |
| `VEO_MAX_SEGMENTS` | `3` | Up to this many distinct visuals are generated (one per script slide) instead of looping a single clip |
| `VEO_DURATION_SECONDS` | `6` | Seconds requested from the Veo API *per segment* -- this is what's actually billed, fixed regardless of script length |
| `VEO_MIN_DURATION_SECONDS` / `VEO_MAX_DURATION_SECONDS` | `16` / `20` | The final concatenated clip is looped up to at least the min and trimmed down to at most the max |

## Swapping the LLM provider

All text/embedding *generation* calls go through LangChain
(`app/llm/factory.py`), so moving off Gemini is:

```
LLM_PROVIDER=openai
OPENAI_API_KEY=sk-...
pip install langchain-openai   # commented out in requirements.txt by default
```

Nothing else in the codebase changes. **Judging is a separate seam and always
uses OpenAI directly** (`app/llm/openai_judge_client.py`), regardless of
`LLM_PROVIDER` -- per the requirement to use OpenAI specifically as the
LLM-as-judge. **Video generation is the other exception**: LangChain has no
video-generation abstraction, so the Veo provider
(`app/video/veo_provider.py`) calls `google-genai` directly.

## Cost and quality tradeoffs

Each newly-generated topic now costs: 1 embedding call, 1+ script calls
(regenerated up to `MAX_REGENERATION_ATTEMPTS` times on a failed gate), 1
claim-extraction judge call, ~4-8 per-claim grounding judge calls, 1
teaching-quality judge call, 1 output-review judge call, a local render
(fraction of a cent), and a Veo render. The Veo cost is now **fixed**
regardless of script length: `VEO_MAX_SEGMENTS * VEO_DURATION_SECONDS *
COST_VEO_PER_SECOND` (~$2.70 at the defaults, 3 segments x 6s x $0.15/s) --
Veo only ever bills for the raw per-segment generation, never for however
long the final clip ends up being looped/trimmed to. **Since only 3 topics
are ever generated -- once each, the first time they're requested -- and
every later request for them is a DB read, the marginal cost of arbitrary
request volume is one embedding call.** See ARCHITECTURE.md for the full
reliability design.

## Testing

```bash
pytest
```

Fully offline: SQLite instead of Postgres, deterministic fake
embeddings/chat model, a deterministic fake OpenAI judge (exercises the real
gating/aggregation logic against a controlled input -- see `tests/test_judge.py`
for gate-decision unit tests against constructed reports, including a
`CONTRADICTED`/hard-fail case), Celery forced into `task_always_eager` mode
(no Redis/worker needed), a stub video file with QC bypassed for the
integration tests, and the *real* ffprobe-based QC validator exercised
directly (via real ffmpeg-generated clips) in `tests/test_qc_validator.py`
-- skipped automatically if ffmpeg isn't on PATH. `tests/test_rag.py` parses
the real committed source PDF (no mocking -- it's a local file).

## Submission video export

Once you've requested each of the 3 supported questions at least once (so
each has a cached, gated master row), copy the final videos into the tracked
`submission/videos/` folder:

```bash
python scripts/export_submission_videos.py
```

This writes `submission/videos/<topic_id>.mp4` (the delivered variant) plus
`<topic_id>_local.mp4` / `<topic_id>_veo.mp4` for each seed, and a
`manifest.json` mapping each learner query to its video(s), provider, cost,
faithfulness/teaching-quality scores, gate decision, and source chunk ids.
