"""CLI `fit` command smoke tests (hermetic; no network, no Ollama)."""

import json

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
            id="fit:1",
            title="Senior Product Manager AI",
            company="Nimbus Motors",
            location="Munich",
            url="https://example.test/jobs/1",
            source="test",
            description="Drive autonomous driving AI products, stakeholder management, machine learning",
        ),
        source_type="board",
    )
    db.upsert_job(conn, job)
    conn.commit()
    conn.close()
    return path


def test_fit_human_output(tmp_path, capsys):
    path = _seed(tmp_path)
    rc = cli_run(["--database", path, "fit", "fit:1"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "current_fit:" in out
    assert "career_upside:" in out
    assert "evidence_coverage:" in out
    assert "Nimbus Motors" in out


def test_fit_json_output(tmp_path, capsys):
    path = _seed(tmp_path)
    rc = cli_run(["--database", path, "fit", "fit:1", "--json"])
    assert rc == 0
    out = capsys.readouterr().out
    data = json.loads(out)
    assert data["job_id"] == "fit:1"
    assert data["job_title"] == "Senior Product Manager AI"
    assert 0.0 <= data["current_fit"]["score"] <= 1.0
    assert "evidence_ids_used" in data
    assert isinstance(data["knowledge_sources"], list)


def test_fit_persists_result(tmp_path):
    path = _seed(tmp_path)
    rc = cli_run(["--database", path, "fit", "fit:1", "--json"])
    assert rc == 0
    conn = db.connect(path)
    row = db.get_career_fit(conn, "fit:1")
    conn.close()
    assert row is not None
    assert row["job_id"] == "fit:1"
    assert 0.0 <= float(row["current_fit"]) <= 1.0
    assert float(row["evidence_coverage"]) >= 0.0


def test_fit_not_found(tmp_path, capsys):
    path = _seed(tmp_path)
    rc = cli_run(["--database", path, "fit", "nonexistent-id"])
    assert rc == 1
    err = capsys.readouterr().err
    assert "not found" in err.lower()


def test_fit_idempotent(tmp_path):
    path = _seed(tmp_path)
    assert cli_run(["--database", path, "fit", "fit:1", "--json"]) == 0
    conn1 = db.connect(path)
    row1 = db.get_career_fit(conn1, "fit:1")
    conn1.close()

    assert cli_run(["--database", path, "fit", "fit:1", "--json"]) == 0
    conn2 = db.connect(path)
    row2 = db.get_career_fit(conn2, "fit:1")
    conn2.close()

    assert row1["current_fit"] == row2["current_fit"]
    assert row1["evidence_coverage"] == row2["evidence_coverage"]
    assert row1["knowledge_sources_json"] == row2["knowledge_sources_json"]


def test_fit_no_llm_flag_accepted(tmp_path, capsys):
    path = _seed(tmp_path)
    rc = cli_run(["--database", path, "fit", "fit:1", "--no-llm", "--json"])
    assert rc == 0
    data = json.loads(capsys.readouterr().out)
    assert data["narrative"] is None


def test_fit_json_structure_and_ranges(tmp_path, capsys):
    path = _seed(tmp_path)
    rc = cli_run(["--database", path, "fit", "fit:1", "--json"])
    assert rc == 0
    data = json.loads(capsys.readouterr().out)
    assert isinstance(data["strengths"], list)
    assert isinstance(data["gaps"], list)
    assert isinstance(data["transferable_skills"], list)
    assert isinstance(data["positioning"], list)
    assert isinstance(data["risks"], list)
    assert isinstance(data["evidence_ids_used"], list)
    assert isinstance(data["knowledge_sources"], list)
    assert isinstance(data["current_fit"], dict)
    assert "score" in data["current_fit"]
    assert isinstance(data["current_fit"]["breakdown"], dict)
    assert len(data["positioning"]) >= 1
