"""Discovery provenance + canonical-url identity tests — hermetic SQLite."""

from __future__ import annotations

import pytest

from job_agent import db
from job_agent.models import Job


def _job(
    jid: str,
    *,
    title: str = "Product Manager AI",
    company: str = "X",
    canonical_key: str | None = None,
    canonical_url: str | None = None,
    url: str | None = None,
) -> Job:
    return Job(
        id=jid,
        title=title,
        company=company,
        url=url or f"https://x.example/{jid}",
        source="test",
        location="Munich",
        canonical_key=canonical_key,
        canonical_url=canonical_url,
    )


def test_migration_v6_columns_exist(tmp_path):
    conn = db.connect(str(tmp_path / "m.db"))
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(jobs)").fetchall()}
    assert {"canonical_url", "source_count"}.issubset(cols)
    tables = {r["name"] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    assert "job_sources" in tables
    conn.close()


def test_upsert_persists_canonical_url_and_source_count(tmp_path):
    conn = db.connect(str(tmp_path / "d.db"))
    j = _job("j1", canonical_key="k", canonical_url="https://job.example/x")
    db.upsert_job(conn, j)
    rows = db.get_jobs(conn)
    assert rows[0].canonical_url == "https://job.example/x"
    assert conn.execute("SELECT source_count FROM jobs WHERE id='j1'").fetchone()["source_count"] == 0
    conn.close()


def test_record_job_source_idempotent(tmp_path):
    conn = db.connect(str(tmp_path / "d.db"))
    j = _job("j1", canonical_key="k", canonical_url="https://job.example/x")
    db.upsert_job(conn, j)
    db.record_job_source(
        conn,
        "j1",
        source_id="ats_greenhouse",
        discovery_method="ats_api",
        query="qm",
        canonical_url="https://job.example/x",
    )
    db.record_job_source(
        conn,
        "j1",
        source_id="ats_greenhouse",
        discovery_method="ats_api",
        query="qm",
        canonical_url="https://job.example/x",
    )
    sources = db.job_sources_for(conn, "j1")
    assert len(sources) == 1
    assert conn.execute("SELECT source_count FROM jobs WHERE id='j1'").fetchone()["source_count"] == 1
    conn.close()


def test_record_job_source_multiple_sources(tmp_path):
    conn = db.connect(str(tmp_path / "d.db"))
    j = _job("j1", canonical_key="k", canonical_url="https://job.example/x")
    db.upsert_job(conn, j)
    db.record_job_source(conn, "j1", source_id="greenhouse", canonical_url="https://job.example/x")
    db.record_job_source(conn, "j1", source_id="linkedin", canonical_url="https://job.example/x?ref=li")
    assert len(db.job_sources_for(conn, "j1")) == 2
    assert conn.execute("SELECT source_count FROM jobs WHERE id='j1'").fetchone()["source_count"] == 2
    conn.close()


def test_source_job_counts_aggregate(tmp_path):
    conn = db.connect(str(tmp_path / "d.db"))
    for jid, key in (("a", "ka"), ("b", "kb"), ("c", "kc")):
        db.upsert_job(conn, _job(jid, canonical_key=key, url=f"https://e/{jid}"))
    db.record_job_source(conn, "a", source_id="gh", canonical_url="u1")
    db.record_job_source(conn, "b", source_id="gh", canonical_url="u2")
    db.record_job_source(conn, "c", source_id="li", canonical_url="u3")
    counts = db.source_job_counts(conn)
    by_source = {r["source_id"]: r["jobs_count"] for r in counts}
    assert by_source == {"gh": 2, "li": 1}
    conn.close()


def test_discovery_method_counts(tmp_path):
    conn = db.connect(str(tmp_path / "d.db"))
    db.upsert_job(conn, _job("a", canonical_key="ka"))
    db.upsert_job(conn, _job("b", canonical_key="kb", url="https://e/b"))
    db.record_job_source(conn, "a", source_id="gh", discovery_method="ats_api", canonical_url="u1")
    db.record_job_source(conn, "b", source_id="gh", discovery_method="ats_api", canonical_url="u2")
    db.record_job_source(conn, "a", source_id="li", discovery_method="search_engine", canonical_url="u3")
    assert db.discovery_method_counts(conn) == {"ats_api": 2, "search_engine": 1}
    conn.close()


def test_canonical_url_stats(tmp_path):
    conn = db.connect(str(tmp_path / "d.db"))
    db.upsert_job(conn, _job("a", canonical_key="ka", canonical_url="u1"))
    db.upsert_job(conn, _job("b", canonical_key="kb"))
    stats = db.canonical_url_stats(conn)
    assert stats["total_jobs"] == 2
    assert stats["with_canonical_url"] == 1
    conn.close()


def test_find_duplicate_of_matches_canonical_url(tmp_path):
    conn = db.connect(str(tmp_path / "d.db"))
    existing = _job("existing", canonical_key="k1", canonical_url="https://job.example/posting", url="https://li/1")
    db.upsert_job(conn, existing)
    incoming = _job(
        "incoming",
        canonical_key="k2",  # different identity key
        canonical_url="https://job.example/posting",
        url="https://gh/2",
    )
    dup = db.find_duplicate_of(conn, incoming)
    assert dup is not None and dup.id == "existing"
    assert db.find_duplicate_of(conn, _job("z", canonical_key="k3", canonical_url="https://other.example/p")) is None
    conn.close()


def test_find_duplicate_of_ignores_closed(tmp_path):
    conn = db.connect(str(tmp_path / "d.db"))
    db.upsert_job(conn, _job("existing", canonical_key="k1", canonical_url="https://job.example/posting"))
    conn.execute("UPDATE jobs SET status='closed' WHERE id='existing'")
    assert (
        db.find_duplicate_of(conn, _job("n", canonical_key="k2", canonical_url="https://job.example/posting")) is None
    )
    conn.close()


def test_jobs_for_analysis_deterministic_order(tmp_path):
    conn = db.connect(str(tmp_path / "d.db"))
    for jid in ("b", "a", "c"):
        db.upsert_job(conn, _job(jid, canonical_key=jid, url=f"https://e/{jid}"))
    snap = db.jobs_for_analysis(conn, limit=10)
    assert [j.id for j in snap] == ["a", "b", "c"]
    assert db.jobs_for_analysis(conn, limit=2) == snap[:2]
    conn.close()


def test_jobs_for_analysis_rejects_negative_limit(tmp_path):
    conn = db.connect(str(tmp_path / "d.db"))
    with pytest.raises(ValueError):
        db.jobs_for_analysis(conn, limit=-1)
    conn.close()
