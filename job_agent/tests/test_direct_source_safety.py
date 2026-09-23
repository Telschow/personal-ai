"""Tests for direct source safety."""
from __future__ import annotations

import pytest
import httpx
from unittest.mock import patch, MagicMock

from job_agent.sources import DirectPageSource


def test_direct_source_normal_response():
    src = DirectPageSource("https://example.com/jobs")
    def fake_get(url, **kwargs):
        resp = MagicMock()
        resp.status_code = 200
        resp.content = b"<html><script type=\"application/ld+json\">{\"@type\": \"JobPosting\", \"title\": \"Engineer\"}</script></html>"
        resp.text = resp.content.decode()
        resp.raise_for_status.return_value = None
        return resp
    with patch("httpx.get", side_effect=fake_get):
        jobs = src.fetch()
    assert len(jobs) == 1
    assert jobs[0].title == "Engineer"


def test_direct_source_zero_length_response():
    src = DirectPageSource("https://example.com/jobs")
    def fake_get(url, **kwargs):
        resp = MagicMock()
        resp.status_code = 200
        resp.content = b""
        resp.text = ""
        resp.raise_for_status.return_value = None
        return resp
    with patch("httpx.get", side_effect=fake_get):
        jobs = src.fetch()
    assert jobs == []


def test_direct_source_oversized_response():
    src = DirectPageSource("https://example.com/jobs")
    def fake_get(url, **kwargs):
        resp = MagicMock()
        resp.status_code = 200
        resp.content = b"x" * (5_000_000 + 1)
        resp.text = resp.content.decode()
        resp.raise_for_status.return_value = None
        return resp
    with patch("httpx.get", side_effect=fake_get):
        with pytest.raises(RuntimeError) as excinfo:
            src.fetch()
    assert "page too large" in str(excinfo.value)


def test_direct_source_connect_timeout():
    src = DirectPageSource("https://example.com/jobs")
    def fake_get(url, **kwargs):
        raise httpx.ConnectTimeout("Connection timed out")
    with patch("httpx.get", side_effect=fake_get):
        with pytest.raises(httpx.ConnectTimeout):
            src.fetch()


def test_direct_source_read_timeout():
    src = DirectPageSource("https://example.com/jobs")
    def fake_get(url, **kwargs):
        raise httpx.ReadTimeout("Read timed out")
    with patch("httpx.get", side_effect=fake_get):
        with pytest.raises(httpx.ReadTimeout):
            src.fetch()


def test_direct_source_transient_http_error():
    src = DirectPageSource("https://example.com/jobs")
    call_count = 0
    def fake_get(url, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count < 2:
            raise httpx.HTTPStatusError("Service Unavailable", request=MagicMock(), response=MagicMock(status_code=503))
        resp = MagicMock()
        resp.status_code = 200
        resp.content = b"<html><script type=\"application/ld+json\">{\"@type\": \"JobPosting\", \"title\": \"Engineer\"}</script></html>"
        resp.text = resp.content.decode()
        resp.raise_for_status.return_value = None
        return resp
    with patch("httpx.get", side_effect=fake_get):
        jobs = src.fetch()
    assert len(jobs) == 1
    assert call_count == 2


def test_direct_source_permanent_http_error():
    src = DirectPageSource("https://example.com/jobs")
    def fake_get(url, **kwargs):
        raise httpx.HTTPStatusError("Not Found", request=MagicMock(), response=MagicMock(status_code=404))
    with patch("httpx.get", side_effect=fake_get):
        with pytest.raises(httpx.HTTPStatusError):
            src.fetch()


def test_direct_source_malformed_content():
    src = DirectPageSource("https://example.com/jobs")
    def fake_get(url, **kwargs):
        resp = MagicMock()
        resp.status_code = 200
        resp.content = b"<html><script type=\"application/ld+json\">invalid json</script></html>"
        resp.text = resp.content.decode()
        resp.raise_for_status.return_value = None
        return resp
    with patch("httpx.get", side_effect=fake_get):
        jobs = src.fetch()
    assert jobs == []
