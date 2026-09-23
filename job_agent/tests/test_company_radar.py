"""Tests for Phase 3 company radar."""
from __future__ import annotations

import uuid
from unittest.mock import MagicMock, patch

import pytest

from job_agent import db
from job_agent.config import Config
from job_agent.company_radar import discover_radar, _load_radar, _find_source_for_provider


def test_radar_configuration_load():
    radar = _load_radar()
    assert len(radar) > 0
    names = [c.name for c in radar]
    assert "Helsinger" in names or "Databricks" in names


def test_disabled_company_not_queried():
    conn = db.connect(":memory:")
    cfg = Config()
    fake_companies = [
        MagicMock(name="DisabledCo", disabled=True, greenhouse="token1", ashby=None, lever=None, smartrecruiters=None),
        MagicMock(name="EnabledCo", disabled=False, greenhouse="token2", ashby=None, lever=None, smartrecruiters=None),
    ]
    with patch("job_agent.company_radar._load_radar", return_value=fake_companies):
        with patch("job_agent.company_radar._find_source_for_provider") as mock_find:
            mock_src = MagicMock()
            mock_src.fetch.return_value = []
            mock_src.kind.value = "ats_board"
            mock_find.return_value = mock_src
            run_id = uuid.uuid4().hex
            metrics = discover_radar(conn, cfg, run_id=run_id)
            assert metrics["radar_companies_planned"] == 1
            assert metrics["radar_companies_queried"] == 1


def test_provider_routing():
    cfg = Config()
    src = _find_source_for_provider(cfg, "greenhouse", "nonexistent-token-xyz")
    assert src is None


def test_unsupported_provider_isolated_error():
    conn = db.connect(":memory:")
    cfg = Config()
    fake_companies = [
        MagicMock(name="Co1", disabled=False, greenhouse=None, ashby=None, lever=None, smartrecruiters=None),
        MagicMock(name="Co2", disabled=False, greenhouse="valid", ashby=None, lever=None, smartrecruiters=None),
    ]
    class DummySource:
        kind = MagicMock()
        kind.value = "ats_board"
        name = "greenhouse"
        def fetch(self):
            return []

    def find_src(cfg, provider_type, token):
        if token == "valid":
            return DummySource()
        return None

    with patch("job_agent.company_radar._load_radar", return_value=fake_companies):
        with patch("job_agent.company_radar._find_source_for_provider", side_effect=find_src):
            run_id = uuid.uuid4().hex
            metrics = discover_radar(conn, cfg, run_id=run_id)
            assert metrics["radar_errors"] >= 1
            assert metrics["radar_companies_queried"] == 1
            assert metrics["radar_candidates"] == 0


def test_provider_only_isolation_metrics():
    conn = db.connect(":memory:")
    cfg = Config()
    fake_companies = [
        MagicMock(name="Co", disabled=False, greenhouse="dummy", ashby=None, lever=None, smartrecruiters=None)
    ]
    class DummySource:
        kind = MagicMock()
        kind.value = "ats_board"
        name = "greenhouse"
        def fetch(self):
            return []
    with patch("job_agent.company_radar._load_radar", return_value=fake_companies):
        with patch("job_agent.company_radar._find_source_for_provider", return_value=DummySource()):
            run_id = uuid.uuid4().hex
            metrics = discover_radar(conn, cfg, run_id=run_id)
            assert "radar_candidates" in metrics
            assert metrics["radar_candidates"] == 0
