"""Copies the current cached topic-master videos into submission/videos/
(which, unlike artifacts/, IS tracked in git) with a manifest mapping each
learner query to the video(s) it produced, its quality-gate decision, and
its faithfulness/teaching-quality scores.

Videos are no longer generated at startup -- request each of the 3 supported
questions once first (via POST /generate, with a Celery worker running, or
with CELERY_TASK_ALWAYS_EAGER=true for a quick local run), THEN export:

    python scripts/export_submission_videos.py
"""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db.session import get_session, init_engine  # noqa: E402
from app.db.repository import list_seed_status  # noqa: E402
from app.topics import SEED_TOPIC_IDS, get_topic  # noqa: E402

OUT_DIR = Path(__file__).resolve().parent.parent / "submission" / "videos"


def main() -> int:
    init_engine()
    db = get_session()
    try:
        seed_status = list_seed_status(db)
    finally:
        db.close()

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    manifest = []
    missing = []

    for topic_id in SEED_TOPIC_IDS:
        job = seed_status.get(topic_id)
        topic = get_topic(topic_id)
        if job is None or job.status != "SUCCESS" or not job.video_location:
            missing.append(topic_id)
            continue

        src = Path(job.video_location)
        if not src.exists():
            missing.append(topic_id)
            continue

        dest_name = f"{topic_id}.mp4"
        shutil.copy(src, OUT_DIR / dest_name)

        variants = {}
        for variant, location in (("local", job.local_video_location), ("veo", job.veo_video_location)):
            if location and Path(location).exists():
                variant_name = f"{topic_id}_{variant}.mp4"
                shutil.copy(location, OUT_DIR / variant_name)
                variants[variant] = variant_name

        manifest.append(
            {
                "topic_id": topic_id,
                "learner_query": topic.question if topic else job.query,
                "video_file": dest_name,
                "variants": variants,
                "provider": job.provider,
                "cost_usd": float(job.cost_usd),
                "duration_seconds": job.duration_seconds,
                "validation_passed": job.validation_passed,
                "faithfulness_score": job.faithfulness_score,
                "teaching_quality_score": job.teaching_quality_score,
                "gate_decision": job.gate_decision,
                "source_chunk_ids": job.source_chunk_ids,
                "job_id": job.id,
            }
        )

    (OUT_DIR / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    print(f"Exported {len(manifest)} video(s) to {OUT_DIR}")
    if missing:
        print(f"WARNING: topics with no successful video to export: {missing}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
