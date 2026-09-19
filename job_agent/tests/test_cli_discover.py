"""CLI discover-command tests — hermetic; plan mode is fully offline."""

from __future__ import annotations

import json
import textwrap

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
    assert payload["locations"][0] == "Munich"
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
