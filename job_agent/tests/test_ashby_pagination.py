"""Tests for Ashby pagination."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from job_agent.sources import AshbySource


def make_fake_response(jobs):
    return {"jobs": jobs}


def test_ashby_empty():
    src = AshbySource("board")

    def fake_get_json(url):
        return make_fake_response([])

    with patch.object(src, "_get_json", side_effect=fake_get_json):
        jobs = src.fetch()
    assert jobs == []


def test_ashby_one_page():
    src = AshbySource("board")

    def fake_get_json(url):
        return make_fake_response([{"title": "A", "jobUrl": "u1"}, {"title": "B", "jobUrl": "u2"}])

    with patch.object(src, "_get_json", side_effect=fake_get_json):
        jobs = src.fetch()
    assert len(jobs) == 2
    assert {j.id for j in jobs} == {"ashby:u1", "ashby:u2"}


def test_ashby_multi_page():
    src = AshbySource("board")
    calls = []

    def fake_get_json(url):
        import urllib.parse as up

        qs = up.parse_qs(up.urlparse(url).query)
        offset = int(qs.get("offset", ["0"])[0])
        calls.append(offset)
        if offset == 0:
            return make_fake_response([{"jobUrl": f"u{i}"} for i in range(100)])
        elif offset == 100:
            return make_fake_response([{"jobUrl": f"u{i}"} for i in range(100, 150)])
        else:
            return make_fake_response([])

    with patch.object(src, "_get_json", side_effect=fake_get_json):
        jobs = src.fetch()
    assert len(jobs) == 150
    assert calls == [0, 100]


def test_ashby_final_partial():
    src = AshbySource("board")

    def fake_get_json(url):
        import urllib.parse as up

        qs = up.parse_qs(up.urlparse(url).query)
        offset = int(qs.get("offset", ["0"])[0])
        if offset == 0:
            return make_fake_response([{"jobUrl": f"u{i}"} for i in range(100)])
        elif offset == 100:
            return make_fake_response([{"jobUrl": f"u{100 + i}"} for i in range(37)])
        else:
            return make_fake_response([])

    with patch.object(src, "_get_json", side_effect=fake_get_json):
        jobs = src.fetch()
    assert len(jobs) == 137


def test_ashby_duplicate_ids():
    src = AshbySource("board")

    def fake_get_json(url):
        import urllib.parse as up

        qs = up.parse_qs(up.urlparse(url).query)
        offset = int(qs.get("offset", ["0"])[0])
        if offset == 0:
            # first page full limit
            return make_fake_response([{"jobUrl": f"u{i}"} for i in range(100)])
        elif offset == 100:
            # duplicate u50 and new u100
            return make_fake_response([{"jobUrl": "u50"}, {"jobUrl": "u100"}])
        else:
            return make_fake_response([])

    with patch.object(src, "_get_json", side_effect=fake_get_json):
        jobs = src.fetch()
    ids = [j.id for j in jobs]
    assert ids.count("ashby:u50") == 1
    assert len(jobs) == 101


def test_ashby_page_failure():
    src = AshbySource("board")

    def fake_get_json(url):
        import urllib.parse as up

        qs = up.parse_qs(up.urlparse(url).query)
        offset = int(qs.get("offset", ["0"])[0])
        if offset == 0:
            return make_fake_response([{"jobUrl": "u1"} for _ in range(100)])
        else:
            raise RuntimeError("timeout")

    with patch.object(src, "_get_json", side_effect=fake_get_json), pytest.raises(RuntimeError):
        src.fetch()
