"""Copies the current successful seed videos into submission/videos/ (which,
unlike artifacts/, IS tracked in git) with a manifest mapping each learner
query to the video it produced -- the "3 best generated videos... along with
the input learner query that produced each" deliverable.

Run this after the app has started at least once and all 3 seeds rendered
successfully:

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
        manifest.append(
            {
                "topic_id": topic_id,
                "learner_query": topic.question if topic else job.query,
                "video_file": dest_name,
                "provider": job.provider,
                "cost_usd": float(job.cost_usd),
                "duration_seconds": job.duration_seconds,
                "validation_passed": job.validation_passed,
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
