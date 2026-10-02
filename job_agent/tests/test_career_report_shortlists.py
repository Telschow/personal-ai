"""Career report shortlists must follow configuration, not a built-in city."""

import job_agent.career.report_generator as rg
from job_agent.config import default_config


def _jobs():
    return [
        rg.Job(
            id="a",
            title="AI Product Manager",
            company="Example Corp",
            location="Berlin, Germany",
            url="https://example.test/a",
            source="test",
            role_classification_confidence=0.9,
            location_score=0.5,
        ),
        rg.Job(
            id="b",
            title="Product Owner",
            company="Example Motors",
            location="Munich, Germany",
            url="https://example.test/b",
            source="test",
            role_classification_confidence=0.8,
            location_score=0.9,
        ),
    ]


def _report(monkeypatch, preferred_city):
    cfg = default_config()
    cfg.career.location.preferred_city = preferred_city
    monkeypatch.setattr(rg, "load_config", lambda *a, **k: cfg)
    return rg.CareerSearchReport.__new__(rg.CareerSearchReport)


def test_no_preferred_city_yields_no_local_shortlist(monkeypatch):
    report = _report(monkeypatch, "")
    sl = report._shortlists_by_category(_jobs())
    assert sl["preferred_city"] == []


def test_shortlist_follows_configured_city(monkeypatch):
    report = _report(monkeypatch, "Munich")
    sl = report._shortlists_by_category(_jobs())
    assert [j["company"] for j in sl["preferred_city"]] == ["Example Motors"]


def test_shortlist_follows_configured_city_alias(monkeypatch):
    report = _report(monkeypatch, "München")
    sl = report._shortlists_by_category(_jobs())
    assert [j["company"] for j in sl["preferred_city"]] == ["Example Motors"]


def test_other_categories_unaffected_by_city(monkeypatch):
    with_city = _report(monkeypatch, "Munich")._shortlists_by_category(_jobs())
    without_city = _report(monkeypatch, "")._shortlists_by_category(_jobs())
    assert with_city["ai_autonomous"] == without_city["ai_autonomous"]
    assert with_city["career_acceleration"] == without_city["career_acceleration"]


def test_unreadable_config_does_not_raise(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("no config")

    monkeypatch.setattr(rg, "load_config", boom)
    report = rg.CareerSearchReport.__new__(rg.CareerSearchReport)
    assert report._shortlists_by_category(_jobs())["preferred_city"] == []
