"""Pipeline tests against temporary SQLite — hermetic, no network."""

from __future__ import annotations

from job_agent import db
from job_agent.config import default_config
from job_agent.models import Job
from job_agent.pipeline import run_lifecycle, run_sources
from job_agent.scoring import ScoringPolicy

PROFILE = {"skills": ["Product Management", "AI", "Product Strategy", "Leadership"], "leadership_required": True}

POLICY = ScoringPolicy(
    salary_min=120000,
    salary_target=150000,
    similarity_weight=0.25,
    ai_weight=0.20,
    compensation_weight=0.15,
    location_weight=0.10,
    leadership_weight=0.20,
    purpose_weight=0.05,
    wlb_weight=0.05,
    industries=("AI", "Defence"),
    negative_keywords=("junior",),
    target_roles=("Product Manager", "Technical Program Manager"),
)


class FakeSource:
    name = "fake"
    kind = type("K", (), {"value": "api"})()

    def __init__(self, jobs: list[Job]):
        self.jobs = jobs

    def fetch(self) -> list[Job]:
        return self.jobs


class BrokenSource:
    name = "broken"
    kind = type("K", (), {"value": "api"})()

    def fetch(self):
        raise RuntimeError("boom")


def _job(jid: str, title: str = "Product Manager AI", company: str = "X", salary_eur: float | None = None) -> Job:
    return Job(
        id=jid,
        title=title,
        company=company,
        url=f"https://x.example/{jid}",
        source="fake",
        location="Munich",
        salary_min_eur=salary_eur,
        salary_max_eur=salary_eur,
    )


def test_run_sources_persists_and_ranks(tmp_path):
    conn = db.connect(str(tmp_path / "t.db"))
    src = FakeSource([_job("1", salary_eur=160000), _job("2", title="Junior Tester")])
    result = run_sources(conn, [src], PROFILE, POLICY)
    assert result.total_fetched == 2 or len(result.jobs_seen) == 2

    jobs = db.get_jobs(conn)
    assert len(jobs) == 2

    matched = db.list_matched_jobs(conn)
    assert len(matched) == 1  # junior rejected
    assert matched[0][0].id == "1"

    runs = conn.execute("SELECT * FROM scan_runs").fetchall()
    assert len(runs) == 1
    assert runs[0]["status"] == "completed"
    conn.close()


def test_broken_source_isolated(tmp_path):
    conn = db.connect(str(tmp_path / "t.db"))
    result = run_sources(conn, [BrokenSource()], PROFILE, POLICY)
    assert result.errors
    runs = conn.execute("SELECT * FROM scan_runs").fetchall()
    assert runs and runs[0]["status"] == "failed"
    conn.close()


def test_duplicates_counted_and_dedup(tmp_path):
    conn = db.connect(str(tmp_path / "t.db"))
    a = FakeSource([_job("a", company="Acme GmbH", salary_eur=150000)])
    b = FakeSource([_job("b", company="Acme", salary_eur=150000)])  # same canonical key
    run_sources(conn, [a], PROFILE, POLICY)
    r2 = run_sources(conn, [b], PROFILE, POLICY)
    assert r2.total_duplicates >= 1
    db.deduplicate(conn)
    conn.commit()
    states = {r["id"]: r["status"] for r in conn.execute("SELECT id, status FROM jobs").fetchall()}
    assert states["a"] == "active"
    assert states.get("b") == "duplicate"
    conn.close()


def test_lifecycle_after_scan(tmp_path):
    from job_agent.models import Score

    conn = db.connect(str(tmp_path / "t.db"))
    db.evaluate_and_store(
        conn, _job("gone", salary_eur=150000), Score(total=90, decision="strong", reasons=["x"], gaps=[]), "v1", "p1"
    )
    cfg = default_config()
    cfg.jobs.freshness_days = 30
    cfg.jobs.close_after_missing_scans = 2
    run_lifecycle(conn, cfg, seen_ids=set())
    run_lifecycle(conn, cfg, seen_ids=set())  # second miss → closed
    conn.commit()
    row = db.get_job(conn, "gone")
    assert row and row.status == "closed"
    conn.close()
