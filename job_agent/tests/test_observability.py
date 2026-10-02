"""Discovery observability tests — hermetic, aggregate-only."""

from __future__ import annotations

from job_agent import db
from job_agent.models import Job
from job_agent.observability import discovery_diagnostics


def _job(jid: str, title: str = "Product Manager AI", location: str = "Munich") -> Job:
    return Job(
        id=jid,
        title=title,
        company="X",
        url=f"https://x.example/{jid}",
        source="test",
        location=location,
    )


def test_diagnostics_empty_db(tmp_path):
    conn = db.connect(str(tmp_path / "d.db"))
    diag = discovery_diagnostics(conn)
    assert diag.jobs_total == 0
    assert diag.jobs_sampled == 0
    assert diag.jobs_by_career_track == {}
    conn.close()


def test_diagnostics_aggregates_tracks_and_locations(tmp_path):
    conn = db.connect(str(tmp_path / "d.db"))
    db.upsert_job(conn, _job("1", title="Senior Manager Autonomous Driving", location="Munich"))
    db.upsert_job(conn, _job("2", title="Senior Manager Autonomous Driving", location="Berlin"))
    db.upsert_job(conn, _job("3", title="Robotics Engineer Mission Planning", location="Remote (Europe)"))
    db.upsert_job(conn, _job("4", title="Barista", location=""))
    diag = discovery_diagnostics(conn, sample_limit=100)
    assert diag.jobs_total == 4
    assert diag.jobs_sampled == 4
    assert diag.jobs_by_career_track.get("autonomous_driving") == 2
    assert diag.jobs_by_career_track.get("robotics") == 1
    assert diag.jobs_by_career_track.get("unknown") == 1
    assert diag.jobs_by_location_tier.get("A_city_core") == 1
    assert diag.jobs_by_location_tier.get("D_country") == 1
    assert diag.jobs_by_location_tier.get("F_remote") == 1
    assert diag.jobs_by_location_tier.get("unknown") == 1
    conn.close()


def test_diagnostics_sampling_bound(tmp_path):
    conn = db.connect(str(tmp_path / "d.db"))
    for i in range(5):
        db.upsert_job(conn, _job(str(i), location="Munich"))
    diag = discovery_diagnostics(conn, sample_limit=2)
    assert diag.jobs_sampled == 2
    assert diag.jobs_total == 5
    conn.close()


def test_diagnostics_provenance_rollup(tmp_path):
    conn = db.connect(str(tmp_path / "d.db"))
    db.upsert_job(conn, _job("1", location="Munich"))
    db.upsert_job(conn, _job("2", location="Munich"))
    db.record_job_source(conn, "1", source_id="gh", discovery_method="ats_api", canonical_url="u1")
    db.record_job_source(conn, "2", source_id="gh", discovery_method="ats_api", canonical_url="u2")
    db.record_job_source(conn, "1", source_id="li", discovery_method="search_engine", canonical_url="u3")
    diag = discovery_diagnostics(conn)
    assert diag.jobs_by_canonical_source == {"gh": 2, "li": 1}
    assert diag.records_by_discovery_method == {"ats_api": 2, "search_engine": 1}
    conn.close()


def test_diagnostics_is_content_free(tmp_path):
    conn = db.connect(str(tmp_path / "d.db"))
    db.upsert_job(conn, _job("1", title="Product Manager AI", location="Munich"))
    diag = discovery_diagnostics(conn)
    payload = diag.to_dict()
    assert "description" not in payload
    assert "url" not in payload
    assert any(isinstance(v, dict) for v in payload.values() if isinstance(v, dict))
