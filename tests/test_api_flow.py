"""End-to-end API behavior with the whole external boundary faked (see
conftest.py) and Celery forced into eager (synchronous) mode: no startup
generation, on-demand generation on first request per topic, deterministic
cache-hit on a repeat request, out-of-scope rejection, and job polling."""
from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app
from app.schemas import JobStatus


def test_no_videos_exist_at_startup():
    with TestClient(app) as client:
        resp = client.get("/jobs")
        assert resp.status_code == 200
        assert resp.json() == []

        health = client.get("/health").json()
        assert health["topics_ready"] == []
        assert sorted(health["topics_degraded"]) == sorted(
            ["ph_scale", "covalent_bond_formation", "ionic_vs_covalent"]
        )


def test_first_request_for_a_topic_generates_and_delivers_it():
    with TestClient(app) as client:
        resp = client.post("/generate", json={"query": "How does the pH scale work?"})
        assert resp.status_code == 200  # eager Celery -> finishes within the request in tests
        job = resp.json()
        assert job["status"] == JobStatus.SUCCESS.value
        assert job["topic_id"] == "ph_scale"
        assert job["cache_hit"] is False
        assert job["progress"] == 100
        assert job["local_video_available"] is True
        assert job["veo_video_available"] is True
        assert job["gate_decision"] == "DELIVER"
        assert job["faithfulness_score"] == 1.0
        assert job["source_chunk_ids"]

        health = client.get("/health").json()
        assert "ph_scale" in health["topics_ready"]


def test_second_request_for_same_topic_is_a_deterministic_cache_hit():
    with TestClient(app) as client:
        first = client.post("/generate", json={"query": "How does the pH scale work?"}).json()
        second = client.post("/generate", json={"query": "explain PH"}).json()

        assert second["status"] == JobStatus.SUCCESS.value
        assert second["cache_hit"] is True
        assert second["topic_id"] == "ph_scale"
        assert second["video_location"] == first["video_location"]
        assert second["id"] != first["id"]


def test_unrelated_query_is_rejected_and_never_queues_generation():
    with TestClient(app) as client:
        resp = client.post("/generate", json={"query": "who is the winner of world cup 2026?"})
        assert resp.status_code == 200
        job = resp.json()
        assert job["status"] == JobStatus.REJECTED.value
        assert job["video_location"] is None
        assert job["error_code"] == "OUT_OF_SCOPE"


def test_quality_report_is_available_after_generation():
    with TestClient(app) as client:
        job = client.post("/generate", json={"query": "Why do atoms form covalent bonds?"}).json()
        report = client.get(f"/jobs/{job['id']}/report")
        assert report.status_code == 200
        body = report.json()
        assert body["gate_decision"] == "DELIVER"
        assert body["faithfulness_score"] == 1.0
        assert body["output_checks"]


def test_polling_job_by_id_reflects_final_state():
    with TestClient(app) as client:
        created = client.post("/generate", json={"query": "Why do atoms form covalent bonds?"}).json()
        polled = client.get(f"/jobs/{created['id']}")
        assert polled.status_code == 200
        assert polled.json()["status"] == JobStatus.SUCCESS.value
        assert polled.json()["id"] == created["id"]


def test_unknown_job_id_is_404():
    with TestClient(app) as client:
        resp = client.get("/jobs/does-not-exist")
        assert resp.status_code == 404


def test_video_download_returns_the_local_variant_by_default():
    with TestClient(app) as client:
        seed = client.post("/generate", json={"query": "How does the pH scale work?"}).json()
        resp = client.get(f"/jobs/{seed['id']}/video")
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "video/mp4"
        assert "attachment" in resp.headers.get("content-disposition", "")
        assert resp.content == b"FAKE-MP4-BYTES-local"


def test_video_download_veo_variant():
    with TestClient(app) as client:
        seed = client.post("/generate", json={"query": "How does the pH scale work?"}).json()
        resp = client.get(f"/jobs/{seed['id']}/video", params={"variant": "veo"})
        assert resp.status_code == 200
        assert resp.content == b"FAKE-MP4-BYTES-veo"


def test_video_download_404_for_unknown_job():
    with TestClient(app) as client:
        resp = client.get("/jobs/does-not-exist/video")
        assert resp.status_code == 404


def test_video_download_409_when_job_has_no_video():
    with TestClient(app) as client:
        rejected = client.post("/generate", json={"query": "who is the winner of world cup 2026?"}).json()
        resp = client.get(f"/jobs/{rejected['id']}/video")
        assert resp.status_code == 409


def test_guardrail_rejects_before_any_match():
    with TestClient(app) as client:
        resp = client.post("/generate", json={"query": "!!!"})
        assert resp.status_code == 200
        job = resp.json()
        assert job["status"] == JobStatus.REJECTED.value
        assert job["error_code"] == "GUARDRAIL_REJECTED"


def test_idempotency_key_returns_the_same_job():
    with TestClient(app) as client:
        headers = {"Idempotency-Key": "abc-123"}
        first = client.post("/generate", json={"query": "How does the pH scale work?"}, headers=headers).json()
        second = client.post("/generate", json={"query": "How does the pH scale work?"}, headers=headers).json()
        assert first["id"] == second["id"]


def test_progress_page_is_served():
    with TestClient(app) as client:
        resp = client.get("/")
        assert resp.status_code == 200
        assert "progress" in resp.text.lower() or "bar" in resp.text.lower()
