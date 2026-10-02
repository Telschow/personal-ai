"""Job-Agent HTTP endpoints: applications lifecycle, dashboard aggregates,
provider discovery telemetry. Hermetic — temp SQLite, TestClient, fakes."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

DEFAULT_MODEL = "qwen3.5:9b"


def _job_agent_available() -> bool:
    """True only when Job-Agent's HTTP surface is actually usable.

    ``personal_ai.server.JOB_AGENT_AVAILABLE`` is a shallow probe: it imports
    ``job_agent.db``, which needs none of the discovery stack's third-party
    dependencies. The endpoints exercised here additionally pull in
    ``job_agent.sources`` and friends, so this imports the real modules and
    treats a missing dependency as "not installed" rather than failing later
    deep inside a request handler.
    """
    from personal_ai.server import JOB_AGENT_AVAILABLE

    if not JOB_AGENT_AVAILABLE:
        return False
    try:
        from job_agent.application import ALL_STAGES  # noqa: F401

        from job_agent import db, discovery_search, pipeline  # noqa: F401
    except ImportError:
        return False
    return True


pytestmark = pytest.mark.skipif(
    not _job_agent_available(),
    reason="job_agent not importable (missing runtime dependencies)",
)


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
# Job listing: filters must be applied before pagination
# ---------------------------------------------------------------------------


def _seed_mixed_jobs(conn) -> None:
    """Six jobs spanning location, fit score and analyzed state."""
    from job_agent.models import Job

    from job_agent import db as job_db

    rows = [
        ("s:1", "Project Manager", "Berlin", None),
        ("s:2", "Data Engineer", "Munich", 70),
        ("s:3", "Program Manager", "Remote", None),
        ("s:4", "Autonomous Driving Engineer", "Munich", 30),
        ("s:5", "AI/ML Engineer", "Berlin", 85),
        ("s:6", "Robotics Lead", "Munich", None),
    ]
    for i, (jid, title, loc, total) in enumerate(rows):
        job_db.upsert_job(
            conn,
            Job(
                id=jid,
                title=title,
                company=f"Company{i}",
                url=f"https://example.test/{i}",
                source="s",
                source_type="structured_data",
                location=loc,
                status="active",
            ),
        )
        if total is not None:
            conn.execute(
                "INSERT OR REPLACE INTO evaluations (job_id, total, decision) VALUES (?, ?, ?)",
                (jid, total, "APPLY"),
            )
    conn.commit()


@pytest.fixture
def list_client(tmp_path):
    if not _job_agent_available():
        pytest.skip("job_agent module not installed")
    test_client = _make_app_with_job_db(tmp_path, _seed_mixed_jobs)
    with test_client:
        yield test_client


def _ids(client, **params):
    body = client.get("/api/job-agent/jobs", params=params).json()
    return [j["id"] for j in body["jobs"]], body["count"]


def test_job_list_filters_apply_before_pagination(list_client):
    """Regression: filters ran after LIMIT/OFFSET, so page 1 came back empty.

    With six seeded jobs and `limit=2`, a single-page filter match was pushed to
    offset 4 and never shown on any page a caller would reach first. Each filter
    must also narrow the reported `count`, which it did not.
    """
    ids, count = _ids(list_client, limit=2, offset=0, track="ai_ml")
    assert ids == ["s:5"]
    assert count == 1

    ids, count = _ids(list_client, location="munich")
    assert ids == ["s:2", "s:4", "s:6"]
    assert count == 3

    ids, count = _ids(list_client, min_fit=0.5)
    assert ids == ["s:5", "s:2"]
    assert count == 2

    ids, count = _ids(list_client, analyzed=True)
    assert ids == ["s:5", "s:2", "s:4"]
    assert count == 3

    ids, count = _ids(list_client, analyzed=False)
    assert ids == ["s:1", "s:3", "s:6"]
    assert count == 3


def test_job_list_filters_compose(list_client):
    ids, count = _ids(list_client, location="munich", min_fit=0.6)
    assert ids == ["s:2"]
    assert count == 1

    ids, count = _ids(list_client, location="munich", analyzed=True)
    assert ids == ["s:2", "s:4"]
    assert count == 2


def test_job_list_no_match_reports_zero_not_everything(list_client):
    ids, count = _ids(list_client, location="no-such-city")
    assert ids == []
    assert count == 0

    ids, count = _ids(list_client, track="not-a-track")
    assert ids == []
    assert count == 0


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


def test_discover_closes_its_database_connection(tmp_path):
    """Regression: every discovery call leaked a SQLite handle and an fd.

    The handler opened its own connection and never closed it, on the success
    path and on the error path alike, so repeated calls accumulated open
    descriptors. Under `dry_run` each leaked handle was an in-memory database
    together with its schema and migrations.
    """
    from job_agent import db as job_db

    client, _db_path, monkeypatch = _discover_app(tmp_path, catalog_provider=True)
    # Track close() explicitly rather than relying on garbage collection: CPython
    # finalises an unreferenced sqlite3.Connection, so a "is it still usable?"
    # assertion passes even when the handler never called close(). That version of
    # this test could not fail.
    closed: list[int] = []
    opened: list[int] = []

    class TrackedConnection(sqlite3.Connection):
        def close(self) -> None:
            closed.append(id(self))
            super().close()

    def recording_connect(path):
        # Same setup as job_agent.db.connect, but with a connection subclass that
        # reports close() instead of relying on finalisation.
        from job_agent import migrations

        Path(path).parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(path, timeout=10.0, factory=TrackedConnection)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA busy_timeout = 10000")
        migrations.migrate(conn)
        opened.append(id(conn))
        return conn

    monkeypatch.setattr(job_db, "connect", recording_connect)
    try:
        with client:
            # The gateway connection is opened by the lifespan, not by the
            # handler, so everything opened after this point belongs to requests.
            mark = len(opened)
            ok = client.post("/api/job-agent/discover", json={"limit_total": 2})
            dry = client.post("/api/job-agent/discover", json={"dry_run": True})
            request_handles = opened[mark:]
        assert ok.status_code == 200
        assert dry.status_code == 200
        assert len(request_handles) == 2, "each discovery call opens one connection"
        for handle in request_handles:
            assert handle in closed, "discovery leaked a database connection"
    finally:
        monkeypatch.undo()


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


def test_upload_artifact_rejects_text_field_instead_of_file(client):
    """A `file` field sent as plain form text is not an upload.

    Regression test: the handler read `.filename` off whatever the form held.
    A plain string has no `.filename`, so the request died with an unhandled
    AttributeError (HTTP 500) instead of a client error the caller can act on.
    """
    with client:
        res = client.post(
            "/api/job-agent/jobs/remotive:1/artifacts",
            data={"file": "not-an-upload", "artifact_type": "cv"},
        )
    assert res.status_code == 400
    body = res.json()
    assert body["error"]["type"] == "invalid_request_error"
    # The message must name what actually arrived, so the caller can debug it.
    assert "file" in body["error"]["message"]


def test_upload_artifact_rejects_missing_file_field(client):
    """No `file` key at all is a client error, not a crash."""
    with client:
        res = client.post(
            "/api/job-agent/jobs/remotive:1/artifacts",
            data={"artifact_type": "cv"},
        )
    assert res.status_code == 400
    body = res.json()
    assert body["error"]["type"] == "invalid_request_error"


def test_upload_artifact_accepts_a_real_multipart_upload(client, tmp_path, monkeypatch):
    """A genuine multipart upload must be stored, not rejected.

    Regression test for the endpoint being 100% non-functional. This handler
    parses the body itself with `Request.form()` instead of declaring a
    `fastapi.UploadFile` parameter, so what arrives is a
    `starlette.datastructures.UploadFile`. `fastapi.UploadFile` is a *subclass*
    of it, so the `isinstance(uploaded, UploadFile)` guard was false for every
    valid upload and the endpoint answered 400 for all of them. The two
    negative tests above passed *because* of that bug, so only a happy-path
    test can catch it.
    """
    artifacts_root = tmp_path / "artifacts"
    monkeypatch.setenv("JOB_AGENT_ARTIFACTS", str(artifacts_root))
    pdf = b"%PDF-1.4\n1 0 obj<</Type/Catalog>>endobj\ntrailer<</Root 1 0 R>>\n%%EOF\n"

    with client:
        res = client.post(
            "/api/job-agent/jobs/remotive:1/artifacts",
            files={"file": ("cv.pdf", pdf, "application/pdf")},
            data={"artifact_type": "cv"},
        )
    assert res.status_code == 201, res.text
    body = res.json()
    assert body["filename"] == "cv.pdf"
    assert body["mime_type"] == "application/pdf"
    assert body["status"] == "uploaded"
    assert body["content_hash"]

    stored = artifacts_root / "remotive:1" / "cv" / "cv.pdf"
    assert stored.is_file()
    assert stored.read_bytes() == pdf


def test_upload_artifact_rejects_a_file_that_is_not_a_pdf(
    client, tmp_path, monkeypatch
):
    """Extension alone must not be enough: the PDF magic bytes are checked."""
    monkeypatch.setenv("JOB_AGENT_ARTIFACTS", str(tmp_path / "artifacts"))
    with client:
        res = client.post(
            "/api/job-agent/jobs/remotive:1/artifacts",
            files={"file": ("evil.pdf", b"#!/bin/sh\nrm -rf /\n", "application/pdf")},
            data={"artifact_type": "cv"},
        )
    assert res.status_code == 400
    assert "valid PDF" in res.json()["error"]["message"]


def test_upload_artifact_cannot_escape_the_artifact_root(client, tmp_path, monkeypatch):
    """A traversal filename must not be written outside the configured root."""
    artifacts_root = tmp_path / "artifacts"
    monkeypatch.setenv("JOB_AGENT_ARTIFACTS", str(artifacts_root))
    pdf = b"%PDF-1.4\n1 0 obj<</Type/Catalog>>endobj\ntrailer<</Root 1 0 R>>\n%%EOF\n"
    with client:
        res = client.post(
            "/api/job-agent/jobs/remotive:1/artifacts",
            files={"file": ("../../../../tmp/pwned.pdf", pdf, "application/pdf")},
            data={"artifact_type": "cv"},
        )
    # The upload itself may succeed -- the safe outcome is containment, not a
    # blanket rejection. `safe_filename()` reduces the name to its basename and
    # rewrites separators, so the file lands inside the root under a plain name.
    assert res.status_code == 201, res.text
    assert not Path("/tmp/pwned.pdf").exists()
    stored = artifacts_root / "remotive:1" / "cv" / "pwned.pdf"
    assert stored.is_file()
    assert stored.resolve().is_relative_to(artifacts_root.resolve())
