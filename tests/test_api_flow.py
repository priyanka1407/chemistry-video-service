"""End-to-end API behavior with the whole external boundary faked (see
conftest.py): startup warmup, cache-hit matching, out-of-scope rejection,
and job polling."""
from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app
from app.schemas import JobStatus


def test_startup_creates_three_successful_seed_rows():
    with TestClient(app) as client:
        resp = client.get("/jobs")
        assert resp.status_code == 200
        jobs = resp.json()
        seed_jobs = [j for j in jobs if j["is_seed"]]
        assert len(seed_jobs) == 3
        assert all(j["status"] == JobStatus.SUCCESS.value for j in seed_jobs)
        assert all(j["video_location"] for j in seed_jobs)


def test_health_reports_all_topics_ready():
    with TestClient(app) as client:
        resp = client.get("/health")
        body = resp.json()
        assert resp.status_code == 200
        assert sorted(body["topics_ready"]) == sorted(
            ["ph_scale", "covalent_bond_formation", "ionic_vs_covalent"]
        )
        assert body["topics_degraded"] == []


def test_paraphrase_is_a_cache_hit():
    with TestClient(app) as client:
        resp = client.post("/generate", json={"query": "explain PH"})
        assert resp.status_code == 200
        job = resp.json()
        assert job["status"] == JobStatus.SUCCESS.value
        assert job["cache_hit"] is True
        assert job["topic_id"] == "ph_scale"
        assert job["video_location"]
        assert job["similarity_score"] >= 0.8


def test_unrelated_query_is_rejected():
    with TestClient(app) as client:
        resp = client.post("/generate", json={"query": "who is the winner of world cup 2026?"})
        assert resp.status_code == 200
        job = resp.json()
        assert job["status"] == JobStatus.REJECTED.value
        assert job["video_location"] is None


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


def test_video_download_returns_the_mp4_with_correct_content_type():
    with TestClient(app) as client:
        seed = client.post("/generate", json={"query": "How does the pH scale work?"}).json()
        resp = client.get(f"/jobs/{seed['id']}/video")
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "video/mp4"
        assert "attachment" in resp.headers.get("content-disposition", "")
        assert resp.content == b"FAKE-MP4-BYTES-FOR-TESTS"


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
