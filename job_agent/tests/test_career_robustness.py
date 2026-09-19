"""Operational robustness for Slice 3.5 surfaces (hermetic).

Covers:
- concurrent append-only artifact version assignment (no collision under
  racing writers, no UNIQUE violation), including cross-connection races,
- config gates: ``career.llm.enabled``, ``--no-llm``, and the
  ``career.llm.semantic`` toggle, verified end-to-end through the CLI,
- provider-error fail-closed behaviour (LLM + semantic degrade to the
  deterministic result, never crash, never persist a fake artifact).

No network, no Ollama; everything is mocked or config-driven.
"""

from __future__ import annotations

import json
import threading

import pytest

import job_agent.db as db
from job_agent.career.artifacts import ArtifactStatus, CVArtifact
from job_agent.cli import run as cli_run
from job_agent.models import Job
from job_agent.normalizer import normalize_job


def _artifact(job_id: str, headline: str = "Headline") -> CVArtifact:
    return CVArtifact(
        artifact_id="tailor",
        job_id=job_id,
        status=ArtifactStatus.VALIDATED,
        headline=headline,
        summary="Summary",
        bullets=[],
        evidence_ids=[],
    )


def _job_conn(path: str, job_id: str = "p:1"):
    conn = db.connect(path)
    job = normalize_job(
        Job(
            id=job_id,
            title="AI Product Manager",
            company="ACME",
            location="Munich",
            url="https://example.test/jobs/1",
            source="test",
            description="autonomous driving product owner",
        ),
        source_type="board",
    )
    db.upsert_job(conn, job)
    conn.commit()
    return conn


def _db_path(tmp_path) -> str:
    return str(tmp_path / "robust.db")


def _cv_path(tmp_path) -> str:
    p = tmp_path / "cv.txt"
    p.write_text(
        "Daniel Telschow\n\nExperience\nProduct Owner - Autonomous Driving at BMW Group, 2024-present\n"
        "Led design of Automated Valet Parking feature\n"
    )
    return str(p)


# ---------------------------------------------------------------------------
# Concurrent version assignment
# ---------------------------------------------------------------------------


class TestConcurrentArtifactVersions:
    def test_two_connections_produce_sequential_versions(self, tmp_path) -> None:
        path = _db_path(tmp_path)
        conn_a = _job_conn(path)
        conn_b = db.connect(path)
        try:
            va = db.save_career_artifact(conn_a, _artifact("p:1"), source="deterministic")
            conn_a.commit()
            vb = db.save_career_artifact(conn_b, _artifact("p:1"), source="deterministic")
            conn_b.commit()
        finally:
            conn_a.close()
            conn_b.close()
        assert sorted([va, vb]) == [1, 2]
        art_a = db.get_career_artifact(db.connect(path), "p:1", version=1)
        art_b = db.get_career_artifact(db.connect(path), "p:1", version=2)
        assert art_a and art_b
        assert art_a["version"] == 1
        assert art_b["version"] == 2

    def test_racing_writers_never_violate_unique_index(self, tmp_path) -> None:
        path = _db_path(tmp_path)
        conn = _job_conn(path)
        conn.close()
        results: list[int] = []
        errors: list[Exception] = []
        lock = threading.Lock()

        def writer() -> None:
            try:
                c = db.connect(path)
                try:
                    v = db.save_career_artifact(c, _artifact("p:1"), source="deterministic")
                    c.commit()
                finally:
                    c.close()
                with lock:
                    results.append(v)
            except Exception as exc:  # noqa: BLE001
                with lock:
                    errors.append(exc)

        threads = [threading.Thread(target=writer) for _ in range(6)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert not errors, f"unexpected errors: {errors}"
        assert sorted(results) == [1, 2, 3, 4, 5, 6]
        check = db.connect(path)
        rows = db.list_career_artifacts(check, "p:1")
        check.close()
        assert [r["version"] for r in rows] == [1, 2, 3, 4, 5, 6]

    def test_pinned_version_collision_raises_not_retried(self, tmp_path) -> None:
        import sqlite3

        path = _db_path(tmp_path)
        conn = _job_conn(path)
        try:
            db.save_career_artifact(conn, _artifact("p:1"), source="deterministic")
            conn.commit()
            with pytest.raises(sqlite3.IntegrityError):
                db.save_career_artifact(conn, _artifact("p:1"), version=1, source="deterministic")
                conn.commit()
        finally:
            conn.close()


# ---------------------------------------------------------------------------
# Config gates (end-to-end via CLI)
# ---------------------------------------------------------------------------


class TestLlmAndSemanticGates:
    def _write_config(self, tmp_path, *, llm_enabled: bool, semantic: bool) -> str:
        cfg_path = tmp_path / "config.yaml"
        cfg_path.write_text(
            "career:\n"
            "  llm:\n"
            f"    enabled: {str(llm_enabled).lower()}\n"
            f"    semantic: {str(semantic).lower()}\n"
            "    base_url: 'http://127.0.0.1:9'\n"  # unreachable => provider error path
            "    model: 'qwen3.5:9b'\n"
        )
        return str(cfg_path)

    def test_llm_disabled_by_config_falls_back_to_deterministic(self, tmp_path, capsys) -> None:
        path = _db_path(tmp_path)
        conn = _job_conn(path)
        conn.close()
        cfg = self._write_config(tmp_path, llm_enabled=False, semantic=True)
        rc = cli_run(["--config", cfg, "--database", path, "tailor", "p:1", "--cv", _cv_path(tmp_path), "--json"])
        assert rc == 0
        out = json.loads(capsys.readouterr().out)
        assert out["source"] == "deterministic"
        assert out["status"] == "validated"
        assert out["semantic"] == {"applied": False, "skipped": "disabled"}  # no LLM => no semantic client

    def test_no_llm_flag_defeats_config_enabled(self, tmp_path, capsys) -> None:
        path = _db_path(tmp_path)
        conn = _job_conn(path)
        conn.close()
        cfg = self._write_config(tmp_path, llm_enabled=True, semantic=True)
        rc = cli_run(
            ["--config", cfg, "--database", path, "tailor", "p:1", "--cv", _cv_path(tmp_path), "--no-llm", "--json"]
        )
        assert rc == 0
        out = json.loads(capsys.readouterr().out)
        assert out["source"] == "deterministic"
        assert out["semantic"] == {"applied": False, "skipped": "disabled"}  # --no-llm disables the semantic client

    def test_llm_enabled_provider_error_fails_closed(self, tmp_path, capsys) -> None:
        path = _db_path(tmp_path)
        conn = _job_conn(path)
        conn.close()
        cfg = self._write_config(tmp_path, llm_enabled=True, semantic=True)
        # unreachable base_url => both the LLM proposal and the semantic layer
        # return nothing; the run must degrade to deterministic and never fake.
        rc = cli_run(["--config", cfg, "--database", path, "tailor", "p:1", "--cv", _cv_path(tmp_path), "--json"])
        assert rc == 0
        out = json.loads(capsys.readouterr().out)
        assert out["source"] == "deterministic"
        assert out["status"] == "validated"
        assert out["semantic"] is not None
        assert out["semantic"]["applied"] is False
        assert out["semantic"]["skipped"] in {"provider_error", "malformed"}
        # the persisted artifact is the deterministic one (source deterministic)
        stored = db.get_career_artifact(db.connect(path), "p:1")
        assert stored is not None
        assert stored["source"] == "deterministic"
        assert stored["llm_used"] == 0

    def test_semantic_toggle_off_still_runs_llm_proposal(self, tmp_path, capsys) -> None:
        path = _db_path(tmp_path)
        conn = _job_conn(path)
        conn.close()
        cfg = self._write_config(tmp_path, llm_enabled=True, semantic=False)
        rc = cli_run(["--config", cfg, "--database", path, "tailor", "p:1", "--cv", _cv_path(tmp_path), "--json"])
        assert rc == 0
        out = json.loads(capsys.readouterr().out)
        # semantic toggle off => no semantic build attempt; LLM proposal still runs
        assert out["semantic"] == {"applied": False, "skipped": "disabled"}
