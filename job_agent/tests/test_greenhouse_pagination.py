"""Tests for Greenhouse pagination."""
from __future__ import annotations

import pytest
from unittest.mock import patch, MagicMock

from job_agent.sources import GreenhouseSource


def make_fake_response(jobs):
    return {"jobs": jobs}


def test_greenhouse_pagination_multiple_pages():
    src = GreenhouseSource("testboard")
    # Simulate two pages: first page 100 jobs, second page 1 job
    page_calls = []

    def fake_get_json(url):
        import urllib.parse as up
        qs = up.parse_qs(up.urlparse(url).query)
        page = int(qs.get("page", ["1"])[0])
        page_calls.append(page)
        if page == 1:
            return make_fake_response([{"id": i} for i in range(100)])
        elif page == 2:
            return make_fake_response([{"id": 100}])
        else:
            return make_fake_response([])

    with patch.object(src, "_get_json", side_effect=fake_get_json):
        jobs = src.fetch()
    assert len(jobs) == 101
    ids = {j.id for j in jobs}
    assert "gh:0" in ids and "gh:99" in ids and "gh:100" in ids
    assert page_calls == [1, 2]


def test_greenhouse_pagination_empty_first_page():
    src = GreenhouseSource("testboard")
    def fake_get_json(url):
        return make_fake_response([])
    with patch.object(src, "_get_json", side_effect=fake_get_json):
        jobs = src.fetch()
    assert jobs == []


def test_greenhouse_pagination_final_partial_page():
    src = GreenhouseSource("testboard")
    def fake_get_json(url):
        import urllib.parse as up
        qs = up.parse_qs(up.urlparse(url).query)
        page = int(qs.get("page", ["1"])[0])
        if page == 1:
            # exactly per_page
            return make_fake_response([{"id": i} for i in range(100)])
        else:
            return make_fake_response([])
    with patch.object(src, "_get_json", side_effect=fake_get_json):
        jobs = src.fetch()
    assert len(jobs) == 100


def test_greenhouse_pagination_duplicate_ids():
    src = GreenhouseSource("testboard")
    def fake_get_json(url):
        import urllib.parse as up
        qs = up.parse_qs(up.urlparse(url).query)
        page = int(qs.get("page", ["1"])[0])
        if page == 1:
            return make_fake_response([{"id": i} for i in range(100)])
        elif page == 2:
            # duplicate id 50
            return make_fake_response([{"id": 50}, {"id": 100}])
        else:
            return make_fake_response([])
    with patch.object(src, "_get_json", side_effect=fake_get_json):
        jobs = src.fetch()
    ids = [j.id for j in jobs]
    assert ids.count("gh:50") == 1
    assert len(jobs) == 101
