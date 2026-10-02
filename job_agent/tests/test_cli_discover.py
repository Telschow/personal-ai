"""CLI discover-command tests — hermetic; plan mode is fully offline."""

from __future__ import annotations

import json
import textwrap
from pathlib import Path

import yaml

from job_agent import cli
from job_agent.config import default_config

MINI = textwrap.dedent(
    """
    provenance:
      url: https://github.com/emredurukn/awesome-job-boards
      retrieved: "2026-09-18"
    sources:
      - source_id: ats_board_a
        name: Board A
        source_type: ats
        enabled: true
        priority: 90
        query_host: "boards-a.example"
      - source_id: blocked
        name: Blocked
        source_type: search_engine
        enabled: false
        priority: 95
        reason: "bot-hostile"
    """
)


def _write_config(tmp_path) -> tuple[str, str]:
    cat = tmp_path / "catalog.yaml"
    cat.write_text(MINI, encoding="utf-8")
    cfg = default_config()
    cfg.sources.catalog_path = str(cat)
    cfg.profile_path = str(tmp_path / "profile.yaml")
    cfg.database_path = str(tmp_path / "jobs.sqlite3")
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(yaml.safe_dump(cfg.model_dump(mode="json"), sort_keys=False), encoding="utf-8")
    return str(cfg_path), str(cat)


def _write_config_with_provider(tmp_path) -> tuple[str, str]:
    cat = tmp_path / "catalog.yaml"
    cat.write_text(
        textwrap.dedent(
            """
            provenance:
              url: https://github.com/emredurukn/awesome-job-boards
              retrieved: "2026-09-18"
            sources:
              - source_id: ats_board_a
                name: Board A
                source_type: ats
                enabled: true
                priority: 90
                query_host: "boards-a.example"
              - source_id: remote_remotive
                name: Remotive
                source_type: search_engine
                enabled: true
                priority: 88
                url: "remotive.example"
                query_host: "remotive.example"
                provider: remotive
              - source_id: blocked
                name: Blocked
                source_type: search_engine
                enabled: false
                priority: 95
                reason: "bot-hostile"
            """
        ),
        encoding="utf-8",
    )
    cfg = default_config()
    cfg.sources.catalog_path = str(cat)
    cfg.profile_path = str(tmp_path / "profile.yaml")
    cfg.database_path = str(tmp_path / "jobs.sqlite3")
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(yaml.safe_dump(cfg.model_dump(mode="json"), sort_keys=False), encoding="utf-8")
    return str(cfg_path), str(cat)


def test_parser_has_discover_command():
    parser = cli.build_parser()
    args = parser.parse_args(["discover", "plan"])
    assert args.mode == "plan"
    assert hasattr(args, "func")


def test_plan_mode_offline_yaml(tmp_path, capsys):
    cfg_path, _ = _write_config(tmp_path)
    rc = cli.run(["--config", cfg_path, "discover", "plan", "--json", "--max-queries", "12"])
    assert rc == 0
    out = capsys.readouterr().out
    payload = json.loads(out)
    assert payload["query_count"] == 12
    # A default config carries no city preference, so the plan starts at the
    # national scope term.
    assert payload["locations"][0] == "Germany"
    assert payload["queries"]
    assert payload["queries_by_source"]


def test_plan_mode_budget_overrides(tmp_path, capsys):
    cfg_path, _ = _write_config(tmp_path)
    rc = cli.run(
        ["--config", cfg_path, "discover", "plan", "--json", "--max-queries", "18", "--max-queries-per-track", "6"]
    )
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["budgets"]["max_queries_total"] == 18
    assert payload["query_count"] == 18


def test_plan_mode_caps_sources(tmp_path, capsys):
    cfg_path, _ = _write_config(tmp_path)
    rc = cli.run(
        [
            "--config",
            cfg_path,
            "discover",
            "plan",
            "--json",
            "--max-queries",
            "40",
            "--max-queries-per-track",
            "40",
            "--max-sources-per-track",
            "1",
        ]
    )
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    sources = set(payload["queries_by_source"])
    assert sources == {"ats_board_a"}


def test_invalid_mode_rejected(tmp_path):
    cfg_path, _ = _write_config(tmp_path)
    try:
        rc = cli.run(["--config", cfg_path, "discover", "explode"])
    except SystemExit as exc:
        rc = int(exc.code or 1)
    assert rc != 0


def test_run_mode_records_provider_and_discovery_runs(tmp_path, capsys, monkeypatch):
    """Non-dry-run discover persists content-free provider/discovery telemetry."""
    cfg_path, _ = _write_config_with_provider(tmp_path)
    import json as _json

    def fake_http(url):
        return (
            200,
            _json.dumps({"jobs": [{"id": 1, "title": "AI Engineer", "url": "https://x/1"}]}).encode(),
            {},
        )

    monkeypatch.setattr("job_agent.providers._default_http", fake_http)

    from pathlib import Path

    from job_agent import db

    rc = cli.run(["--config", cfg_path, "discover", "run", "--max-queries", "8", "--json"])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["pacing"]["providers"]
    assert payload["pacing"]["providers"][0]["source_id"] == "remote_remotive"

    conn = db.connect(str(Path(tmp_path) / "jobs.sqlite3"))
    runs = db.latest_provider_runs(conn, limit=5)
    assert len(runs) == 1
    assert runs[0]["provider"] == "remotive"
    discovery = db.latest_discovery_run(conn)
    assert discovery is not None
    assert discovery["jobs_from_providers"] >= 0  # content-free aggregate
    conn.close()


def test_run_mode_dry_run_persists_nothing(tmp_path, monkeypatch):
    cfg_path, _ = _write_config_with_provider(tmp_path)
    import json as _json

    monkeypatch.setattr("job_agent.providers._default_http", lambda url: (200, _json.dumps({"jobs": []}).encode(), {}))
    rc = cli.run(["--config", cfg_path, "discover", "run", "--dry-run", "--max-queries", "8"])
    assert rc == 0
    # Dry-run must not create a database file (in-memory DB only).
    assert not (Path(tmp_path) / "jobs.sqlite3").exists()
