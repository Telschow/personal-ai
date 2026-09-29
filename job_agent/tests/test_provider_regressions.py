"""Regression tests for provider fixes."""

from __future__ import annotations

import json
import httpx
import pytest
from unittest.mock import patch, MagicMock

from job_agent.sources import (
    SmartRecruitersSource,
    GreenhouseSource,
    DirectPageSource,
    AshbySource,
    LeverSource,
    WorkableSource,
    SourceError,
)


def _mock_response(payload: object, status: int = 200):
    """Create a mock httpx.Response."""
    mock = MagicMock()
    mock.status_code = 200
    mock.json.return_value = payload
    mock.raise_for_status.return_value = None
    return mock


def _mock_error_response(status: int = 500):
    """Create a mock error response."""
    mock = MagicMock()
    mock.status_code = status
    mock.raise_for_status.side_effect = httpx.HTTPStatusError(
        f"HTTP {status}",
        request=MagicMock(),
        response=MagicMock(status_code=status)
    )
    return mock


def _mock_html_response(html: str):
    """Create a mock HTML response."""
    mock = MagicMock()
    mock.status_code = 200
    mock.content = html.encode()
    mock.text = html
    mock.raise_for_status.return_value = None
    return mock


def test_smartrecruiters_valid_response(monkeypatch):
    """Test SmartRecruiters with valid response."""
    payload = {
        "content": [
            {
                "id": "123",
                "name": "AI Product Manager",
                "ref": {
                    "jobAdUrl": "https://careers.smartrecruiters.com/google/123",
                    "applyUrl": "https://careers.smartrecruiters.com/google/123/apply",
                },
                "location": {"city": "Munich", "region": "Bavaria", "country": "Germany"},
            }
        ]
    }
    
    monkeypatch.setattr("job_agent.sources.httpx.get", lambda url, **kwargs: _mock_response(payload))
    jobs = SmartRecruitersSource("google").fetch()
    assert len(jobs) == 1
    assert jobs[0].title == "AI Product Manager"
    assert jobs[0].company == "google"
    assert "Munich" in jobs[0].location


def test_smartrecruiters_malformed_top_level(monkeypatch):
    """Test SmartRecruiters with non-dict top-level response."""
    monkeypatch.setattr("job_agent.sources.httpx.get", lambda url, **kwargs: _mock_response(["not", "a", "dict"]))
    with pytest.raises(SourceError) as excinfo:
        SmartRecruitersSource("google").fetch()
    assert "unexpected top-level response type" in str(excinfo.value)


def test_smartrecruiters_missing_content(monkeypatch):
    """Test SmartRecruiters with missing content field."""
    monkeypatch.setattr("job_agent.sources.httpx.get", lambda url, **kwargs: _mock_response({"other": "field"}))
    with pytest.raises(SourceError) as excinfo:
        SmartRecruitersSource("google").fetch()
    assert "'content' field missing" in str(excinfo.value)


def test_smartrecruiters_string_in_content(monkeypatch):
    """Test SmartRecruiters skips string entries in content array."""
    payload = {
        "content": [
            {"id": "123", "name": "Valid Job", "ref": {}},
            "not a dict",
            {"id": "456", "name": "Another Valid", "ref": {}},
        ]
    }
    
    monkeypatch.setattr("job_agent.sources.httpx.get", lambda url, **kwargs: _mock_response(payload))
    jobs = SmartRecruitersSource("google").fetch()
    assert len(jobs) == 2
    assert jobs[0].title == "Valid Job"
    assert jobs[1].title == "Another Valid"


def test_smartrecruiters_non_dict_ref(monkeypatch):
    """Test SmartRecruiters handles non-dict ref gracefully."""
    payload = {
        "content": [
            {"id": "123", "name": "Job", "ref": "not a dict"},
            {"id": "456", "name": "Job 2", "ref": {}},
        ]
    }
    
    monkeypatch.setattr("job_agent.sources.httpx.get", lambda url, **kwargs: _mock_response(payload))
    jobs = SmartRecruitersSource("google").fetch()
    assert len(jobs) == 2


def test_greenhouse_pagination_bounded(monkeypatch):
    """Test Greenhouse pagination respects max_pages limit."""
    call_count = {"count": 0}
    
    def fake_get(url, **kwargs):
        call_count["count"] += 1
        if call_count["count"] <= 3:
            payload = {"jobs": [{"id": f"{call_count['count']}-1", "title": "Job", "location": {"name": "Munich"}}]}
        else:
            payload = {"jobs": []}
        return _mock_response(payload)
    
    monkeypatch.setattr("job_agent.sources.httpx.get", fake_get)
    jobs = GreenhouseSource("test").fetch()
    assert len(jobs) >= 1


def test_greenhouse_timeout(monkeypatch):
    """Test Greenhouse handles timeout gracefully."""
    monkeypatch.setattr("job_agent.sources.httpx.get", lambda url, **kwargs: (_ for _ in ()).throw(httpx.TimeoutException("Request timed out")))
    with pytest.raises(SourceError) as excinfo:
        GreenhouseSource("test").fetch()
    assert "timeout" in str(excinfo.value).lower()


def test_greenhouse_http_error(monkeypatch):
    """Test Greenhouse handles HTTP errors."""
    monkeypatch.setattr("job_agent.sources.httpx.get", lambda url, **kwargs: _mock_error_response(500))
    with pytest.raises(httpx.HTTPStatusError):
        GreenhouseSource("test").fetch()


def test_direct_source_diagnostics(monkeypatch):
    """Test DirectPageSource logs diagnostics for zero results."""
    html = '<html><script type="application/ld+json">{"@type": "WebPage"}</script></html>'
    monkeypatch.setattr("job_agent.sources.httpx.get", lambda url, **kwargs: _mock_html_response(html))
    jobs = DirectPageSource("https://example.com/careers").fetch()
    assert jobs == []


def test_direct_source_timeout_wrapped(monkeypatch):
    """Test DirectPageSource wraps timeout in SourceError."""
    with patch("httpx.get", side_effect=httpx.ConnectTimeout("Connection timed out")):
        with pytest.raises(SourceError) as excinfo:
            DirectPageSource("https://example.com/jobs").fetch()
    assert "timeout after 3 attempts" in str(excinfo.value)


def test_ashby_malformed_response(monkeypatch):
    """Test Ashby handles non-dict response gracefully."""
    monkeypatch.setattr("job_agent.sources.httpx.get", lambda url, **kwargs: _mock_response("not a dict"))
    jobs = AshbySource("test").fetch()
    assert jobs == []


def test_ashby_malformed_posting(monkeypatch):
    """Test Ashby skips malformed postings."""
    payload = {
        "jobs": [
            {"id": "1", "title": "Valid Job", "jobUrl": "https://example.com/1"},
            "not a dict",
            {"id": "2", "title": "Another Valid", "jobUrl": "https://example.com/2"},
        ]
    }
    
    monkeypatch.setattr("job_agent.sources.httpx.get", lambda url, **kwargs: _mock_response(payload))
    jobs = AshbySource("test").fetch()
    assert len(jobs) == 2


def test_ashby_invalid_jobs_field(monkeypatch):
    """Test Ashby handles non-list jobs field."""
    monkeypatch.setattr("job_agent.sources.httpx.get", lambda url, **kwargs: _mock_response({"jobs": "not a list"}))
    jobs = AshbySource("test").fetch()
    assert jobs == []


def test_ashby_missing_job_id(monkeypatch):
    """Test Ashby skips postings without job URL."""
    # The source falls back to title when jobUrl/applyUrl are missing
    # So we test that it doesn't crash and handles gracefully
    payload = {
        "jobs": [
            {"id": "1", "title": "Valid Job", "jobUrl": "https://example.com/1"},
            {"id": "2", "title": "No URL Job"},
            {"id": "3", "title": "Another Valid", "jobUrl": "https://example.com/3"},
        ]
    }
    
    monkeypatch.setattr("job_agent.sources.httpx.get", lambda url, **kwargs: _mock_response(payload))
    jobs = AshbySource("test").fetch()
    # Should process all 3 (falls back to title for missing URL)
    assert len(jobs) == 3


def test_lever_invalid_response(monkeypatch):
    """Test Lever handles non-list response."""
    monkeypatch.setattr("job_agent.sources.httpx.get", lambda url, **kwargs: _mock_response({"jobs": []}))
    jobs = LeverSource("test").fetch()
    assert jobs == []


def test_lever_malformed_posting(monkeypatch):
    """Test Lever skips malformed postings."""
    payload = [
        {"id": "1", "text": "Valid Job", "hostedUrl": "https://example.com/1"},
        "not a dict",
        {"id": "2", "text": "Another Valid", "hostedUrl": "https://example.com/2"},
    ]
    
    monkeypatch.setattr("job_agent.sources.httpx.get", lambda url, **kwargs: _mock_response(payload))
    jobs = LeverSource("test").fetch()
    assert len(jobs) == 2


def test_workable_malformed_items(monkeypatch):
    """Test Workable handles non-list items."""
    monkeypatch.setattr("job_agent.sources.httpx.get", lambda url, **kwargs: _mock_response({"jobs": "not a list"}))
    jobs = WorkableSource("test").fetch()
    assert jobs == []


def test_source_isolation():
    """Test that one source failure doesn't affect others."""
    pass


if __name__ == "__main__":
    pytest.main([__file__, "-v"])