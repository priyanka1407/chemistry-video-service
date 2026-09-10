# Chemistry Video Request Service

A FastAPI backend that turns a learner's chemistry question into a short
explainer video (slides + narration + audio). Only 3 concepts are supported,
generated once at startup and cached forever:

- How does the pH scale work?
- Why do atoms form covalent bonds?
- What is the difference between ionic and covalent bonding?

Any other query is matched against those 3 via embeddings + cosine
similarity: a semantically close paraphrase ("explain PH") gets that seed's
video back instantly; anything unrelated ("who won the 2026 World Cup?") is
rejected. **No video is ever generated on demand** -- generation only
happens for the 3 seeds at startup. See [ARCHITECTURE.md](ARCHITECTURE.md)
for why, and for the full job lifecycle / persistence / AI-boundary design.

## Prerequisites

- Python 3.11 (3.10-3.12 should also work; avoid 3.13+ until dependencies catch up)
- **ffmpeg + ffprobe on PATH** (the local renderer's video encoder and the QC
  validator's media inspector). On Windows: `winget install -e --id Gyan.FFmpeg`
- PostgreSQL reachable (optional -- falls back to a local SQLite file if not; see below)
- A Google AI Studio API key, if you want real LLM-scripted videos and
  embedding-based matching instead of the curated/lexical fallback path
  (the service runs and produces real videos without one -- see Reliability below)

## Setup

```bash
python -m venv .venv
.venv\Scripts\Activate.ps1      # Windows PowerShell
pip install --upgrade pip
pip install -r requirements.txt
copy .env.example .env          # then edit .env
```

## Run

```bash
uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

On startup the service creates the `video_jobs` table if it doesn't exist,
then checks for a successful video for each of the 3 seed questions and
renders any that are missing -- this blocks until done (a few seconds to a
couple minutes depending on provider), so the app never claims to be ready
before its 3 required videos actually exist. Once you see
`Application startup complete`, open http://127.0.0.1:8000/docs.

## API

| Endpoint | Method | Purpose |
|---|---|---|
| `/generate` | POST | Submit a learner query. Returns a job immediately (guardrail/match is synchronous and fast; rendering never happens here). |
| `/jobs/{id}` | GET | Poll a job's current status/stage. |
| `/jobs` | GET | List recent jobs. |
| `/jobs/{id}/video` | GET | Download/stream the video file for a completed job. |
| `/topics` | GET | The 3 supported concepts and example phrasings. |
| `/health` | GET | DB/provider/topic readiness. |

```bash
# A supported concept, worded differently -- cache hit
curl -s -X POST http://127.0.0.1:8000/generate \
  -H "Content-Type: application/json" -d "{\"query\": \"explain PH\"}"

# Out of scope -- rejected, no video generated
curl -s -X POST http://127.0.0.1:8000/generate \
  -H "Content-Type: application/json" \
  -d "{\"query\": \"who is the winner of world cup 2026?\"}"

# Poll a job
curl -s http://127.0.0.1:8000/jobs/<job_id>

# Fetch the finished video
curl -s -o out.mp4 http://127.0.0.1:8000/jobs/<job_id>/video
```

## Configuration

Every tunable lives in `.env` (see `.env.example` for the full, commented
list) and is read once in `app/config.py` -- nothing is hardcoded in
application code.

| Variable | Default | Meaning |
|---|---|---|
| `DATABASE_URL` | `postgresql+psycopg2://postgres:postgres@localhost:5432/postgres` | Primary datastore |
| `DB_ALLOW_SQLITE_FALLBACK` | `true` | Fall back to a local SQLite file if Postgres is unreachable at startup, instead of refusing to start |
| `LLM_PROVIDER` | `google` | `google` \| `openai` \| `anthropic` -- see "Swapping the LLM provider" below |
| `GOOGLE_API_KEY` / `OPENAI_API_KEY` / `ANTHROPIC_API_KEY` | empty | API key for the selected provider |
| `EMBEDDING_MODEL` | `gemini-embedding-001` | Embedding model id |
| `SCRIPT_MODEL` | `gemini-2.5-flash` | Chat model id used to write seed video scripts |
| `VIDEO_PROVIDER` | `local` | `local` (Pillow+TTS+ffmpeg, ~$0.0005/video) \| `veo` (Google Veo, ~$1.20/video for an 8s clip) |
| `FALLBACK_TO_LOCAL` | `true` | If `veo` fails, retry with the local renderer instead of failing the seed |
| `TTS_PROVIDER` | `gtts` | `gtts` (network) \| `pyttsx3` (offline) \| `silent` (last resort) |
| `SEMANTIC_SIMILARITY_THRESHOLD` | `0.80` | Cosine similarity required to accept a query (embedding path) |
| `LEXICAL_SIMILARITY_THRESHOLD` | `0.35` | Same, for the offline lexical fallback (different scale) |
| `RATE_LIMIT_PER_MINUTE` | `30` | Per-IP request cap |
| `MAX_SCRIPT_ATTEMPTS` / `MAX_RENDER_ATTEMPTS` | `3` / `2` | Retries before a fallback/failure kicks in |
| `COST_*` | see `.env.example` | Per-unit cost rates used for the `cost_usd` field on every job |

### Swapping the LLM provider

All text/embedding calls go through LangChain (`app/llm/factory.py`), so
moving off Gemini is:

```
LLM_PROVIDER=openai
OPENAI_API_KEY=sk-...
pip install langchain-openai   # commented out in requirements.txt by default
```

Nothing else in the codebase changes. **Video generation is the one
exception**: LangChain has no video-generation abstraction, so the Veo
provider (`app/video/veo_provider.py`) calls `google-genai` directly --
that's the deliberate, documented seam where a real provider is plugged in.

## Cost and quality tradeoffs

The default path (`VIDEO_PROVIDER=local`) costs essentially nothing per
video: Pillow-rendered slides, gTTS narration (free), ffmpeg muxing. A
3-slide, ~50-second video costs a fraction of a cent -- mostly the embedding
call. `VIDEO_PROVIDER=veo` trades that for a fixed per-second Veo rate
(`COST_VEO_PER_SECOND`, default $0.15/s -> ~$1.20 for an 8s clip) in exchange
for actual AI-generated video motion instead of static slides. Every job's
`cost_usd` field reflects exactly what was spent producing it (or, for a
cache hit, just the embedding call).

**Since only 3 videos are ever generated (all at startup) and every other
request is served from cache or rejected, the marginal cost of arbitrary
request volume is one embedding call each** -- the guardrail chain (rate
limit -> rule-based filters -> semantic threshold) keeps that call cheap and
bounded even under load. See ARCHITECTURE.md for the full reliability
design (retries, fallbacks, validation) that keeps output consistent across
repeated runs despite the LLM/media pipeline being non-deterministic.

## Testing

```bash
pytest
```

27 tests, fully offline: SQLite instead of Postgres, deterministic fake
embeddings/chat model, a stub video file with QC bypassed for the
integration tests, and the *real* ffprobe-based QC validator exercised
directly (via real ffmpeg-generated clips) in `tests/test_qc_validator.py`
-- skipped automatically if ffmpeg isn't on PATH.

## Submission video export

Once the app has started and all 3 seeds show `SUCCESS` (check `/health`),
copy the final videos into the tracked `submission/videos/` folder:

```bash
python scripts/export_submission_videos.py
```

This writes `submission/videos/<topic_id>.mp4` for each seed plus a
`manifest.json` mapping each learner query to its video, provider, cost, and
validation result.
