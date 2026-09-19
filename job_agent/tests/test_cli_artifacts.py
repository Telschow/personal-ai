"""CLI career artifacts list/show/diff (append-only version tape)."""

import json

import job_agent.db as db
from job_agent.cli import run as cli_run
from job_agent.migrations import migrate
from job_agent.models import Job
from job_agent.normalizer import normalize_job

_CV = """\
Daniel Telschow

Experience
Product Owner - Autonomous Driving at BMW Group, 2024-present
Led design of Automated Valet Parking feature

Education
MSc Automotive Engineering, TU Munich
"""


def _seed_db(tmp_path) -> str:
    path = str(tmp_path / "artifacts.db")
    conn = db.connect(path)
    migrate(conn)
    job = normalize_job(
        Job(
            id="p:1",
            title="AI Product Manager",
            company="ACME",
            location="Munich",
            url="https://example.test/jobs/1",
            source="test",
            description="autonomous driving product owner stakeholder management",
        ),
        source_type="board",
    )
    db.upsert_job(conn, job)
    conn.commit()
    conn.close()
    return path


def _cv_path(tmp_path) -> str:
    p = tmp_path / "cv.txt"
    p.write_text(_CV)
    return str(p)


def _tailor(dbp: str, cv: str, capsys) -> None:
    rc = cli_run(["--database", dbp, "tailor", "p:1", "--cv", cv, "--no-llm"])
    capsys.readouterr()  # discard tailor stdout so it never pollutes later assertions
    assert rc == 0


def test_artifacts_list_human(tmp_path, capsys):
    dbp = _seed_db(tmp_path)
    cv = _cv_path(tmp_path)
    _tailor(dbp, cv, capsys)
    _tailor(dbp, cv, capsys)  # second version
    rc = cli_run(["--database", dbp, "career", "artifacts", "list", "p:1"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "v1" in out
    assert "v2" in out
    assert "deterministic" in out


def test_artifacts_list_json(tmp_path, capsys):
    dbp = _seed_db(tmp_path)
    cv = _cv_path(tmp_path)
    _tailor(dbp, cv, capsys)
    rc = cli_run(["--database", dbp, "career", "artifacts", "list", "p:1", "--json"])
    assert rc == 0
    rows = json.loads(capsys.readouterr().out)
    assert len(rows) == 1
    assert rows[0]["job_id"] == "p:1"
    assert rows[0]["version"] == 1


def test_artifacts_list_empty(tmp_path, capsys):
    dbp = _seed_db(tmp_path)
    rc = cli_run(["--database", dbp, "career", "artifacts", "list", "p:1"])
    assert rc == 0
    assert "No proposal artifacts" in capsys.readouterr().out


def test_artifacts_show_latest(tmp_path, capsys):
    dbp = _seed_db(tmp_path)
    cv = _cv_path(tmp_path)
    _tailor(dbp, cv, capsys)
    _tailor(dbp, cv, capsys)
    rc = cli_run(["--database", dbp, "career", "artifacts", "show", "p:1"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "v2" in out
    assert "headline" in out
    assert "created" in out


def test_artifacts_show_specific_version(tmp_path, capsys):
    dbp = _seed_db(tmp_path)
    cv = _cv_path(tmp_path)
    _tailor(dbp, cv, capsys)
    _tailor(dbp, cv, capsys)
    rc = cli_run(["--database", dbp, "career", "artifacts", "show", "p:1", "--version", "1"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "v1" in out
    assert "status:" in out


def test_artifacts_show_json(tmp_path, capsys):
    dbp = _seed_db(tmp_path)
    cv = _cv_path(tmp_path)
    _tailor(dbp, cv, capsys)
    rc = cli_run(["--database", dbp, "career", "artifacts", "show", "p:1", "--json"])
    assert rc == 0
    data = json.loads(capsys.readouterr().out)
    assert data["version"] == 1
    assert data["job_id"] == "p:1"
    assert "bullets" in data


def test_artifacts_show_missing(tmp_path, capsys):
    dbp = _seed_db(tmp_path)
    rc = cli_run(["--database", dbp, "career", "artifacts", "show", "p:1"])
    assert rc == 1
    assert "No artifact" in capsys.readouterr().err


def test_artifacts_diff_default_latest_two(tmp_path, capsys):
    dbp = _seed_db(tmp_path)
    cv = _cv_path(tmp_path)
    _tailor(dbp, cv, capsys)
    _tailor(dbp, cv, capsys)
    rc = cli_run(["--database", dbp, "career", "artifacts", "diff", "p:1"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "Diff v1 -> v2" in out


def test_artifacts_diff_explicit_versions(tmp_path, capsys):
    dbp = _seed_db(tmp_path)
    cv = _cv_path(tmp_path)
    _tailor(dbp, cv, capsys)
    _tailor(dbp, cv, capsys)
    rc = cli_run(["--database", dbp, "career", "artifacts", "diff", "p:1", "--from-version", "1", "--to-version", "2"])
    assert rc == 0
    assert "Diff v1 -> v2" in capsys.readouterr().out


def test_artifacts_diff_json(tmp_path, capsys):
    dbp = _seed_db(tmp_path)
    cv = _cv_path(tmp_path)
    _tailor(dbp, cv, capsys)
    _tailor(dbp, cv, capsys)
    rc = cli_run(["--database", dbp, "career", "artifacts", "diff", "p:1", "--json"])
    assert rc == 0
    data = json.loads(capsys.readouterr().out)
    assert set(data.keys()) == {"from", "to"}
    assert data["from"]["version"] == 1
    assert data["to"]["version"] == 2


def test_artifacts_diff_too_few_versions(tmp_path, capsys):
    dbp = _seed_db(tmp_path)
    cv = _cv_path(tmp_path)
    _tailor(dbp, cv, capsys)
    rc = cli_run(["--database", dbp, "career", "artifacts", "diff", "p:1"])
    assert rc == 1
    assert "two artifact versions" in capsys.readouterr().err


def test_artifacts_diff_partial_versions_rejected(tmp_path, capsys):
    dbp = _seed_db(tmp_path)
    cv = _cv_path(tmp_path)
    _tailor(dbp, cv, capsys)
    rc = cli_run(["--database", dbp, "career", "artifacts", "diff", "p:1", "--from-version", "1"])
    assert rc == 1
    assert "both --from-version and --to-version" in capsys.readouterr().err
