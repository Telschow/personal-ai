"""Job-Agent HTTP endpoints: applications lifecycle, dashboard aggregates,
provider discovery telemetry. Hermetic — temp SQLite, TestClient, fakes."""

from __future__ import annotations

import sqlite3

import pytest
from fastapi.testclient import TestClient

DEFAULT_MODEL = "qwen3.5:9b"


def _job_agent_available() -> bool:
    from personal_ai.server import JOB_AGENT_AVAILABLE

    return JOB_AGENT_AVAILABLE


def _make_app_with_job_db(tmp_path, seed) -> TestClient:
    from job_agent import db as job_db
    from personal_ai.server import create_app

    job_file = tmp_path / "app-job.sqlite3"
    conn = job_db.connect(str(job_file))
    seed(conn)
    conn.close()

    class FakeAgent:
        def run(self, messages):
            return "ok"

    class FakeBuilt:
        def close(self):
            pass

    app = create_app(
        None,
        None,
        model=DEFAULT_MODEL,
        agent_factory=lambda: FakeBuilt(),
        job_db_path=job_file,
    )
    client = TestClient(app)
    client._job_file = job_file  # type: ignore[attr-defined]
    return client


def _seed_two_jobs(conn) -> None:
    from job_agent.models import Job

    from job_agent import db as job_db

    for i, (jid, title, company) in enumerate(
        [
            ("remotive:1", "AI Engineer", "Gamma"),
            ("remotive:2", "ML Engineer", "Omega"),
        ]
    ):
        job_db.upsert_job(
            conn,
            Job(
                id=jid,
                title=title,
                company=company,
                url=f"https://x/{i}",
                source="remotive",
                source_type="structured_data",
                location="Europe",
            ),
        )
    conn.commit()


@pytest.fixture
def client(tmp_path):
    if not _job_agent_available():
        pytest.skip("job_agent module not installed")
    test_client = _make_app_with_job_db(tmp_path, _seed_two_jobs)
    with test_client:
        yield test_client


# ---------------------------------------------------------------------------
# Applications lifecycle
# ---------------------------------------------------------------------------


def test_get_application_missing_404(client):
    res = client.get("/api/job-agent/applications/remotive:1")
    assert res.status_code == 404


def test_post_application_creates_stage(client):
    res = client.post(
        "/api/job-agent/applications/remotive:1",
        json={"stage": "APPLIED", "notes": "applied via workday"},
    )
    assert res.status_code == 200
    body = res.json()
    assert body["stage"] == "APPLIED"
    assert body["applied_at"] is not None


def test_post_progresses_through_stages(client):
    client.post("/api/job-agent/applications/remotive:1", json={"stage": "APPLIED"})
    client.post("/api/job-agent/applications/remotive:1", json={"stage": "RESPONDED"})
    res = client.post(
        "/api/job-agent/applications/remotive:1", json={"stage": "INTERVIEW"}
    )
    assert res.status_code == 200
    assert res.json()["stage"] == "INTERVIEW"


def test_terminal_stage_never_returns_to_active(client):
    client.post("/api/job-agent/applications/remotive:1", json={"stage": "APPLIED"})
    client.post("/api/job-agent/applications/remotive:1", json={"stage": "REJECTED"})
    res = client.post(
        "/api/job-agent/applications/remotive:1", json={"stage": "APPLIED"}
    )
    assert res.status_code == 400
    assert "Invalid application transition" in res.json()["error"]["message"]


def test_post_illegal_transition_rejected(client):
    # NOT_APPLIED -> RESPONDED is impossible without APPLIED first.
    res = client.post(
        "/api/job-agent/applications/remotive:1", json={"stage": "RESPONDED"}
    )
    assert res.status_code == 400
    assert "Invalid application transition" in res.json()["error"]["message"]


def test_patch_requires_existing_application(client):
    res = client.patch("/api/job-agent/applications/remotive:1", json={"notes": "n"})
    assert res.status_code == 404


def test_patch_updates_metadata_and_stage(client):
    client.post("/api/job-agent/applications/remotive:1", json={"stage": "APPLIED"})
    res = client.patch(
        "/api/job-agent/applications/remotive:1",
        json={
            "notes": "recruiter pinged",
            "interview_stage": "phone_screen",
            "follow_up_at": "2026-04-01",
        },
    )
    assert res.status_code == 200
    body = res.json()
    assert body["stage"] == "APPLIED"
    assert body["interview_stage"] == "phone_screen"
    assert body["follow_up_at"].startswith("2026-04-01")


def test_bad_dates_and_stages_rejected(client):
    client.post("/api/job-agent/applications/remotive:1", json={"stage": "APPLIED"})
    bad_date = client.patch(
        "/api/job-agent/applications/remotive:1", json={"follow_up_at": "not-a-date"}
    )
    assert bad_date.status_code == 400
    bad_stage = client.patch(
        "/api/job-agent/applications/remotive:1", json={"stage": "BOGUS"}
    )
    assert bad_stage.status_code == 400
    bad_interview = client.patch(
        "/api/job-agent/applications/remotive:1", json={"interview_stage": "bogus"}
    )
    assert bad_interview.status_code == 400


def test_unknown_job_404(client):
    res = client.post("/api/job-agent/applications/nope", json={"stage": "APPLIED"})
    assert res.status_code == 404


def test_list_applications_filter_and_limit(client):
    client.post("/api/job-agent/applications/remotive:1", json={"stage": "APPLIED"})
    client.post("/api/job-agent/applications/remotive:2", json={"stage": "APPLIED"})
    client.post("/api/job-agent/applications/remotive:2", json={"stage": "INTERVIEW"})
    all_rows = client.get("/api/job-agent/applications").json()["applications"]
    assert len(all_rows) == 2
    interviews = client.get(
        "/api/job-agent/applications", params={"stage": "INTERVIEW"}
    ).json()["applications"]
    assert [r["job_id"] for r in interviews] == ["remotive:2"]

    bad_stage = client.get("/api/job-agent/applications", params={"stage": "NOPE"})
    assert bad_stage.status_code == 400


def test_application_attached_to_job_detail(client):
    client.post("/api/job-agent/applications/remotive:1", json={"stage": "APPLIED"})
    body = client.get("/api/job-agent/jobs/remotive:1").json()
    assert body["application"]["stage"] == "APPLIED"


# ---------------------------------------------------------------------------
# Dashboard aggregates
# ---------------------------------------------------------------------------


def test_dashboard_content_free_aggregates(client):
    client.post("/api/job-agent/applications/remotive:1", json={"stage": "APPLIED"})
    body = client.get("/api/job-agent/dashboard").json()
    assert body["applications_by_stage"]["APPLIED"] == 1
    assert body["applications_total"] == 1
    assert body["jobs_by_user_status"] is not None
    assert body["provider_failures_total"] == 0
    assert body["last_run"] is None
    assert body["provider_runs"] == []


def test_dashboard_empty_db_defaults(client):
    body = client.get("/api/job-agent/dashboard").json()
    assert body["applications_total"] == 0
    assert body["last_run"] is None


# ---------------------------------------------------------------------------
# Discovery provider telemetry
# ---------------------------------------------------------------------------


def _discover_app(tmp_path, catalog_provider: bool = True):
    from job_agent.application import (
        ALL_STAGES,  # noqa: F401  (ensures module importable)
    )

    from job_agent import discovery_search, pipeline
    from personal_ai.server import create_app

    job_db = tmp_path / "discover-job.sqlite3"

    class FakeResult:
        def __init__(self):
            self.persisted_by_source = {"remote_remotive": 1}
            self.total_duplicates = 0
            self.jobs_seen = set()

    class FakePlan:
        def __init__(self):
            self.items = []
            self.locations = []
            self.budgets = {}

        def queries(self):
            return ["q1"]

        def summary(self):
            return {"query_count": 1, "locations": [], "queries_by_source": {}}

        def audit_items(self):
            return []

    class FakePacing:
        def __init__(self):
            self.providers = []

        def to_dict(self):
            return {"providers": self.providers, "totals": {}}

    def fake_discovery(*args, **kwargs):
        pacing = FakePacing()
        if catalog_provider:
            pacing.providers = [
                {
                    "source_id": "remote_remotive",
                    "provider": "remotive",
                    "status": "ok",
                    "requests": 1,
                    "hits": 1,
                    "candidate_jobs": 1,
                    "duplicates": 0,
                    "errors": [],
                    "latency_ms": 100,
                }
            ]
        return FakePlan(), [], [], [], pacing

    def fake_ingest(conn, jobs, profile, policy, provenance=None, run_started_at=None):
        return FakeResult()

    def fake_lifecycle(conn, cfg, seen_ids):
        return {"active": 0}

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(discovery_search, "run_planned_discovery", fake_discovery)
    monkeypatch.setattr(pipeline, "ingest_global_jobs", fake_ingest)
    monkeypatch.setattr(pipeline, "run_lifecycle", fake_lifecycle)

    class FakeAgent:
        def run(self, messages):
            return "ok"

    class FakeBuilt:
        def close(self):
            pass

    app = create_app(
        None,
        None,
        model=DEFAULT_MODEL,
        agent_factory=lambda: FakeBuilt(),
        job_db_path=job_db,
    )
    return TestClient(app), str(job_db), monkeypatch


def test_discover_returns_provider_telemetry_and_persists_runs(tmp_path):
    client, db_path, monkeypatch = _discover_app(tmp_path, catalog_provider=True)
    try:
        with client:
            res = client.post("/api/job-agent/discover", json={"limit_total": 4})
        assert res.status_code == 200
        body = res.json()
        assert body["provider_runs"] == [body["provider_runs"][0]]
        assert body["provider_runs"][0]["provider"] == "remotive"
        assert body["jobs_from_providers"] == 1
        assert body["jobs_from_search"] == 0
        assert body["dry_run"] is False
        assert body["pacing"]["providers"]

        raw = sqlite3.connect(db_path)
        runs = raw.execute("SELECT provider, status FROM provider_runs").fetchall()
        assert runs == [("remotive", "ok")]
        disc = raw.execute(
            "SELECT jobs_from_providers, jobs_from_search FROM discovery_runs"
        ).fetchone()
        assert disc == (1, 0)
        raw.close()
    finally:
        monkeypatch.undo()


def test_discover_dry_run_persists_nothing(tmp_path):
    client, db_path, monkeypatch = _discover_app(tmp_path, catalog_provider=True)
    try:
        with client:
            res = client.post(
                "/api/job-agent/discover", json={"limit_total": 2, "dry_run": True}
            )
        assert res.status_code == 200
        assert res.json()["dry_run"] is True
        # Telemetry is returned but dry-run ingests into an in-memory DB, so
        # the app database holds no job-agent run rows.
        assert [p["status"] for p in res.json()["provider_runs"]] == ["ok"]
        from job_agent import db as job_db

        raw = job_db.connect(db_path)
        assert raw.execute("SELECT COUNT(*) FROM provider_runs").fetchone()[0] == 0
        assert raw.execute("SELECT COUNT(*) FROM discovery_runs").fetchone()[0] == 0
        assert raw.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 0
        raw.close()
    finally:
        monkeypatch.undo()


def test_discover_without_providers_backward_compatible(tmp_path):
    client, db_path, monkeypatch = _discover_app(tmp_path, catalog_provider=False)
    try:
        with client:
            res = client.post("/api/job-agent/discover", json={"limit_total": 4})
        assert res.status_code == 200
        body = res.json()
        assert body["provider_runs"] == []
        assert body["jobs_from_providers"] == 0
        raw = sqlite3.connect(db_path)
        assert raw.execute("SELECT COUNT(*) FROM provider_runs").fetchone()[0] == 0
        raw.close()
    finally:
        monkeypatch.undo()
