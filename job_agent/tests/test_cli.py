"""CLI smoke tests (hermetic; no network, no Ollama).

These exercise the argparse wiring and the sqlite-backed read/write commands
without ever issuing an HTTP request.
"""

import job_agent.db as db
from job_agent.cli import run as cli_run
from job_agent.migrations import migrate
from job_agent.models import Job
from job_agent.normalizer import normalize_job


def _seed(tmp_path, name="seed.db") -> str:
    path = str(tmp_path / name)
    conn = db.connect(path)
    migrate(conn)
    job = normalize_job(
        Job(
            id="smoke:1",
            title="Senior Product Manager AI",
            company="BMW Group",
            location="Munich",
            url="https://example.test/jobs/1",
            source="test",
            description="Drive autonomous driving AI products",
        ),
        source_type="board",
    )
    db.upsert_job(conn, job)
    conn.commit()
    conn.close()
    return path


def test_scan_dry_run_offline_with_unknown_source_filter(capsys):
    # Dry-run with a filter matching nothing must not touch the network.
    rc = cli_run(["scan", "--dry-run", "--no-global-search", "--source", "definitely-not-configured"])
    out = capsys.readouterr().out
    assert rc == 1
    assert "No configured sources found" in out


def test_db_path_override_wired(tmp_path, capsys):
    path = _seed(tmp_path)
    rc = cli_run(["--database", path, "jobs", "--json"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "smoke:1" in out
    assert "Senior Product Manager AI" in out


def test_decision_flow_changes_status(tmp_path, capsys):
    path = _seed(tmp_path)
    rc = cli_run(["--database", path, "decision", "smoke:1", "approve", "--note", "manual"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "Recorded decision" in out
    conn = db.connect(path)
    rows = conn.execute("SELECT job_id, action FROM job_decisions WHERE job_id='smoke:1'").fetchall()
    conn.close()
    assert [tuple(r) for r in rows] == [("smoke:1", "approve")]


def test_jobs_empty_db(tmp_path, capsys):
    conn = db.connect(str(tmp_path / "empty.db"))
    migrate(conn)
    conn.close()
    rc = cli_run(["--database", str(tmp_path / "empty.db"), "jobs"])
    assert rc == 0
    assert "No jobs found" in capsys.readouterr().out
