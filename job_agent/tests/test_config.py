import pytest

from job_agent.config import (
    Config,
    JobsSettings,
    SalaryPolicy,
    SourcesSettings,
    default_config,
    load_config,
)

EXAMPLE_CONFIG = {
    "database_path": "/tmp/x.sqlite3",
    "jobs": {"salary": {"minimum_eur": 120000, "target_eur": 155000}},
    "llm": {"model": "qwen3:9b", "base_url": "http://x"},
    "sources": {"greenhouse": [{"token": "example"}]},
}


def test_defaults_satisfied():
    cfg = default_config()
    assert cfg.application.auto_submit is False
    assert cfg.projects.auto_publish is False
    # Shipped defaults must carry no personal career policy: no compensation
    # floor, no preferred city, no sector preferences, no network access.
    assert cfg.jobs.salary.minimum_eur == 0
    assert cfg.jobs.salary.target_eur == 0
    assert cfg.search.global_enabled is False
    assert cfg.career.location.preferred_city == ""
    assert cfg.search.industries_preferred == []
    assert cfg.llm.base_url == "http://127.0.0.1:11434"


def test_salary_floor_requires_both_or_neither():
    with pytest.raises(ValueError):
        Config(jobs={"salary": {"minimum_eur": 100000, "target_eur": 0}})
    with pytest.raises(ValueError):
        Config(jobs={"salary": {"minimum_eur": 0, "target_eur": 100000}})
    with pytest.raises(ValueError):
        Config(jobs={"salary": {"minimum_eur": 150000, "target_eur": 100000}})
    cfg = Config(jobs={"salary": {"minimum_eur": 100000, "target_eur": 150000}})
    assert cfg.jobs.salary.minimum_eur == 100000


def test_auto_submit_blocked():
    with pytest.raises(ValueError):
        Config(application={"auto_submit": True})


def test_auto_publish_blocked():
    with pytest.raises(ValueError):
        Config(projects={"auto_publish": True})


def test_negative_salary_blocked():
    with pytest.raises(ValueError):
        Config(jobs=JobsSettings(salary=SalaryPolicy(minimum_eur=-5)))


def test_load_config_from_file(tmp_path):
    p = tmp_path / "config.yaml"
    p.write_text("database_path: /x.db\njobs:\n  freshness_days: 14\n")
    cfg = load_config(str(p))
    assert cfg.database_path == "/x.db"
    assert cfg.jobs.freshness_days == 14
    assert cfg.jobs.salary.minimum_eur == 0  # no built-in compensation floor
    assert cfg.jobs.salary.target_eur == 0


def test_load_config_bad_structure(tmp_path):
    p = tmp_path / "config.yaml"
    p.write_text("- not-a-dict\n")
    with pytest.raises(ValueError):
        load_config(str(p))


def test_yaml_booleans_not_mistaken_for_countries(tmp_path):
    # YAML parses bare `NO`/`ON` as booleans; quoting is required.
    data = 'search:\n  countries: [DE, AT, CH, NL, DK, SE, "NO", ES, UK]\n'
    p = tmp_path / "config.yaml"
    p.write_text(data)
    cfg = load_config(str(p))
    assert cfg.search.countries == [
        "DE",
        "AT",
        "CH",
        "NL",
        "DK",
        "SE",
        "NO",
        "ES",
        "UK",
    ]
    assert all(isinstance(c, str) for c in cfg.search.countries)


def test_env_overrides():
    cfg = Config()
    out = cfg.with_env_overrides({"JOB_AGENT_DATABASE_PATH": "/env/db.sqlite3", "JOB_AGENT_LLM_MODEL": "abacus"})
    assert out.database_path == "/env/db.sqlite3"
    assert out.llm.model == "abacus"


def test_sources_settings_default():
    s = SourcesSettings()
    assert s.greenhouse == []
    assert s.direct_company_domains == []


def test_llm_timeout_defaults():
    cfg = default_config()
    assert cfg.llm.timeout_seconds == 300.0
    assert cfg.career.llm.timeout_seconds == 0.0  # reuse llm.timeout_seconds


def test_career_llm_timeout_override():
    from job_agent.config import CareerLlmSettings

    s = CareerLlmSettings(timeout_seconds=120.0)
    assert s.timeout_seconds == 120.0


def test_shipped_example_config_is_offline_and_commit_safe():
    """The committed example must not trigger network calls or leak real data.

    `config.example.yaml` is copied by users per job_agent/README.md, so an
    example that fires live discovery queries or carries real job-search
    preferences is both a privacy problem and a surprise on first run.
    """
    from pathlib import Path

    from job_agent.discovery import build_sources

    example = Path(__file__).resolve().parents[1] / "config.example.yaml"
    cfg = load_config(example)

    # No source is configured, and the CLI only runs global discovery when
    # search.global_enabled is true, so a copied example performs no requests.
    # planned_queries() itself still returns a plan built from the module's
    # documented default location anchors, so it is not the thing to assert.
    assert cfg.search.global_enabled is False
    assert build_sources(cfg) == []

    # No real job-search preferences may be committed in an example file.
    assert cfg.search.countries == []
    assert cfg.search.global_locations == []
    assert cfg.search.target_roles == []
    assert cfg.search.keywords_positive == []
