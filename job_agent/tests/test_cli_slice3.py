"""CLI Slice 3 commands: documents ingest/list, evidence list, tailor.

All tests are hermetic: no network, no Ollama, in-memory or tmp DBs.
"""

import json

import job_agent.db as db
from job_agent.cli import run as cli_run
from job_agent.migrations import migrate
from job_agent.models import Job
from job_agent.normalizer import normalize_job

_EXAMPLE_CV = """\
Alice Example

Experience
Product Owner - Autonomous Driving at BMW Group, 2024-present
Led design of Automated Valet Parking feature

Education
MSc Automotive Engineering, TU Munich
"""


def _seed_db(tmp_path) -> str:
    path = str(tmp_path / "slice3.db")
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
    p.write_text(_EXAMPLE_CV)
    return str(p)


def test_ingest_documents_human_output(tmp_path, capsys):
    dbp = _seed_db(tmp_path)
    cv = _cv_path(tmp_path)
    rc = cli_run(["--database", dbp, "career", "documents", "ingest", cv])
    assert rc == 0
    out = capsys.readouterr().out
    assert "cv.txt" in out
    assert "new evidence:" in out


def test_ingest_documents_json_output(tmp_path, capsys):
    dbp = _seed_db(tmp_path)
    cv = _cv_path(tmp_path)
    rc = cli_run(["--database", dbp, "career", "documents", "ingest", cv, "--json"])
    assert rc == 0
    data = json.loads(capsys.readouterr().out)
    assert data["filename"] == "cv.txt"
    assert data["new_evidence_saved"] > 0
    assert data["status"] == "clean"


def test_ingest_idempotent(tmp_path, capsys):
    dbp = _seed_db(tmp_path)
    cv = _cv_path(tmp_path)
    cli_run(["--database", dbp, "career", "documents", "ingest", cv, "--json"])
    rc = cli_run(["--database", dbp, "career", "documents", "ingest", cv])
    assert rc == 0
    out = capsys.readouterr().out
    assert "already ingested" in out


def test_ingest_missing_file(tmp_path, capsys):
    dbp = _seed_db(tmp_path)
    rc = cli_run(["--database", dbp, "career", "documents", "ingest", "/nonexistent/cv.txt"])
    assert rc == 1
    assert "not found" in capsys.readouterr().err


def test_documents_list(tmp_path, capsys):
    dbp = _seed_db(tmp_path)
    cv = _cv_path(tmp_path)
    cli_run(["--database", dbp, "career", "documents", "ingest", cv, "--json"])
    rc = cli_run(["--database", dbp, "career", "documents", "list"])
    assert rc == 0
    assert "cv.txt" in capsys.readouterr().out


def test_documents_list_json(tmp_path, capsys):
    dbp = _seed_db(tmp_path)
    cv = _cv_path(tmp_path)
    cli_run(["--database", dbp, "career", "documents", "ingest", cv, "--json"])
    capsys.readouterr()
    rc = cli_run(["--database", dbp, "career", "documents", "list", "--json"])
    assert rc == 0
    data = json.loads(capsys.readouterr().out)
    assert len(data) == 1
    assert data[0]["filename"] == "cv.txt"


def test_evidence_list_human(tmp_path, capsys):
    dbp = _seed_db(tmp_path)
    cv = _cv_path(tmp_path)
    cli_run(["--database", dbp, "career", "documents", "ingest", cv, "--json"])
    rc = cli_run(["--database", dbp, "career", "evidence", "list"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "evidence item(s)" in out
    assert "documented" in out


def test_evidence_list_json(tmp_path, capsys):
    dbp = _seed_db(tmp_path)
    cv = _cv_path(tmp_path)
    cli_run(["--database", dbp, "career", "documents", "ingest", cv, "--json"])
    capsys.readouterr()
    rc = cli_run(["--database", dbp, "career", "evidence", "list", "--json"])
    assert rc == 0
    data = json.loads(capsys.readouterr().out)
    assert len(data) >= 2
    assert all({"evidence_id", "claim", "level"}.issubset(d.keys()) for d in data)


def test_evidence_conflicts_empty(tmp_path, capsys):
    dbp = _seed_db(tmp_path)
    cv = _cv_path(tmp_path)
    cli_run(["--database", dbp, "career", "documents", "ingest", cv, "--json"])
    rc = cli_run(["--database", dbp, "career", "evidence", "list", "--conflicts"])
    assert rc == 0
    assert "No unresolved conflicts" in capsys.readouterr().out


def test_tailor_human_output(tmp_path, capsys):
    dbp = _seed_db(tmp_path)
    cv = _cv_path(tmp_path)
    rc = cli_run(["--database", dbp, "tailor", "p:1", "--cv", cv, "--no-llm"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "PROPOSAL - NOT APPROVED" in out
    assert "deterministic" in out.lower()


def test_tailor_json_output(tmp_path, capsys):
    dbp = _seed_db(tmp_path)
    cv = _cv_path(tmp_path)
    rc = cli_run(["--database", dbp, "tailor", "p:1", "--cv", cv, "--no-llm", "--json"])
    assert rc == 0
    data = json.loads(capsys.readouterr().out)
    assert data["job_id"] == "p:1"
    assert data["source"] == "deterministic"
    assert data["status"] == "validated"
    assert data["note"] == "PROPOSAL - NOT APPROVED"
    assert len(data["bullets"]) >= 1
    for b in data["bullets"]:
        assert "evidence_id" in b


def test_tailor_not_found(tmp_path, capsys):
    dbp = _seed_db(tmp_path)
    cv = _cv_path(tmp_path)
    rc = cli_run(["--database", dbp, "tailor", "nonexistent", "--cv", cv, "--no-llm"])
    assert rc == 1
    assert "not found" in capsys.readouterr().err.lower()


def test_tailor_missing_cv(tmp_path, capsys):
    dbp = _seed_db(tmp_path)
    rc = cli_run(["--database", dbp, "tailor", "p:1", "--cv", "/missing/cv.txt", "--no-llm"])
    assert rc == 1
    assert "Error" in capsys.readouterr().err


def test_tailor_stores_artifact(tmp_path):
    dbp = _seed_db(tmp_path)
    cv = _cv_path(tmp_path)
    cli_run(["--database", dbp, "tailor", "p:1", "--cv", cv, "--no-llm"])
    conn = db.connect(dbp)
    stored = db.get_career_artifact(conn, "p:1")
    conn.close()
    assert stored is not None, "no career_artifacts row persisted"
    assert stored["status"] == "validated"
    assert len(json.loads(stored["bullets_json"])) >= 1


def test_tailor_idempotent(tmp_path, capsys):
    dbp = _seed_db(tmp_path)
    cv = _cv_path(tmp_path)
    cli_run(["--database", dbp, "tailor", "p:1", "--cv", cv, "--no-llm"])
    capsys.readouterr()
    rc = cli_run(["--database", dbp, "tailor", "p:1", "--cv", cv, "--no-llm", "--json"])
    assert rc == 0
    data = json.loads(capsys.readouterr().out)
    assert data["job_id"] == "p:1"
