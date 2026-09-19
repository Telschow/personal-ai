"""Unit tests for native discovery providers (M3).

Hermetic: ``http`` is injected per provider; no network, no Ollama, no real
catalog or database.
"""

from __future__ import annotations

import json

from job_agent import providers
from job_agent.providers import (
    MalformedProviderPayload,
    ProviderError,
    ProviderHealth,
    ProviderStatus,
    RemoteOkProvider,
    RemotiveProvider,
    provider_health_for,
)


def _http_ok(payload: object, status: int = 200) -> object:
    return lambda url: (status, json.dumps(payload).encode("utf-8"), {})


def _http_text(text: str, status: int = 200) -> object:
    return lambda url: (status, text.encode("utf-8"), {})


REMOTEOK_PAYLOAD = [
    {"last_updated": "2026-01-01T00:00:00Z", "legal": ["x"]},
    {
        "id": 101,
        "slug": "senior-ml-engineer",
        "position": "Senior ML Engineer",
        "company": "AcmeAI",
        "location": "🌍",
        "tags": ["python", "ml"],
        "salary_min": "$60k",
        "salary_max": "$90k",
        "url": "https://remoteok.com/l/senior-ml-engineer",
        "apply_url": "https://acme.apply.dev/123",
        "date": "2026-01-15",
        "epoch": 1768492800,
    },
    {
        "id": 102,
        "slug": "ai-product-manager",
        "position": "AI Product Manager",
        "company": "BetaLabs",
        "tags": ["product"],
        "salary_min": "70k",
        "salary_max": "80k",
    },
]


REMOTIVE_PAYLOAD = {
    "jobs": [
        {
            "id": 501,
            "url": "https://remotive.com/remote-jobs/software/501",
            "title": "Machine Learning Engineer (Remote)",
            "company_name": "Gamma Systems",
            "category": "software",
            "job_type": "full_time",
            "publication_date": "2026-01-20T10:00:00Z",
            "candidate_required_location": "Europe",
            "salary": "70k - 90k",
            "tags": ["python", "aws"],
            "description": "<p>Build <strong>ML</strong> systems.</p>",
        }
    ]
}


def test_remoteok_parse_metadata_index_skipped_and_fields_mapped() -> None:
    http = _http_ok(REMOTEOK_PAYLOAD)
    result = RemoteOkProvider(http=http).fetch(source_id="remote_remoteok")
    assert result.status == ProviderStatus.OK
    assert result.requests == 1
    assert result.hits == 2
    assert len(result.jobs) == 2
    first = result.jobs[0]
    assert first.id == "remoteok:101"
    assert first.title == "Senior ML Engineer"
    assert first.company == "AcmeAI"
    assert first.source == "remoteok"
    assert first.apply_url == "https://acme.apply.dev/123"
    assert first.location == "Worldwide"  # emoji location -> neutral default
    assert "python" in first.description
    assert first.salary_min == 60_000
    assert first.salary_max == 90_000
    assert first.salary_currency == "USD"


def test_remoteok_zero_yield_and_failed_status() -> None:
    zero = RemoteOkProvider(http=_http_ok([])).fetch(source_id="remote_remoteok")
    assert zero.status == ProviderStatus.ZERO_YIELD
    assert zero.jobs == ()

    failed = RemoteOkProvider(http=_http_ok({"oops": True}, status=500)).fetch(source_id="remote_remoteok")
    assert failed.status == ProviderStatus.FAILED
    assert failed.jobs == ()
    assert failed.errors


def test_remoteok_duplicate_source_ids_deduped_within_feed() -> None:
    payload = [dict(REMOTEOK_PAYLOAD[1]), dict(REMOTEOK_PAYLOAD[1])]
    result = RemoteOkProvider(http=_http_ok(payload)).fetch(source_id="remote_remoteok")
    assert len(result.jobs) == 1
    assert result.duplicates == 0  # within-feed dedup, counted in hits only


def test_remoteok_search_filter_keeps_only_matching_entries() -> None:
    result = RemoteOkProvider(http=_http_ok(REMOTEOK_PAYLOAD)).fetch(
        source_id="remote_remoteok", search="product manager"
    )
    assert len(result.jobs) == 1
    assert result.jobs[0].title == "AI Product Manager"


def test_remoteok_malformed_payload_fails_closed() -> None:
    result = RemoteOkProvider(http=_http_text("not json")).fetch(source_id="remote_remoteok")
    assert result.status == ProviderStatus.FAILED
    assert result.jobs == ()
    assert result.errors


def test_remotive_parse_full_fields_and_html_strip() -> None:
    result = RemotiveProvider(http=_http_ok(REMOTIVE_PAYLOAD)).fetch(source_id="remote_remotive")
    assert result.status == ProviderStatus.OK
    assert len(result.jobs) == 1
    job = result.jobs[0]
    assert job.id == "remotive:501"
    assert job.source == "remotive"
    assert job.location == "Europe"
    assert job.salary_min == 70_000
    assert job.salary_max == 90_000
    assert job.employment_type == "full_time"
    assert job.description.startswith("Build ML systems.")
    assert "python" in job.description


def test_remotive_search_parameter_appended_to_url() -> None:
    seen_urls: list[str] = []

    def http(url: str):
        seen_urls.append(url)
        return (200, json.dumps(REMOTIVE_PAYLOAD).encode("utf-8"), {})

    RemotiveProvider(http=http).fetch(source_id="remote_remotive", search="ml engineer")
    assert len(seen_urls) == 1
    assert "search=ml%20engineer" in seen_urls[0]


def test_remotive_missing_optional_fields_neutral() -> None:
    payload = {"jobs": [{"id": 1, "title": "Only Title", "url": "https://x"}]}
    result = RemotiveProvider(http=_http_ok(payload)).fetch(source_id="remote_remotive")
    assert result.status == ProviderStatus.OK
    job = result.jobs[0]
    assert job.company == "Remotive"
    assert job.location == "Worldwide"
    assert job.salary_min is None
    assert job.description == ""


def test_remotive_missing_jobs_key_malformed() -> None:
    result = RemotiveProvider(http=_http_ok({"other": []})).fetch(source_id="remote_remotive")
    assert result.status == ProviderStatus.FAILED
    assert result.jobs == ()
    assert result.errors

    array_payload = "[]"
    result2 = RemotiveProvider(http=_http_text(array_payload)).fetch(source_id="remote_remotive")
    assert result2.status == ProviderStatus.FAILED


def test_malformed_provider_payload_is_failure_never_raises() -> None:
    assert issubclass(MalformedProviderPayload, ProviderError)


def test_provider_for_resolves_registry_and_fallback() -> None:
    from job_agent.catalog import SourceCatalogEntry

    remoteok = SourceCatalogEntry(source_id="remote_remoteok", name="RemoteOK", provider="remoteok")
    remotive = SourceCatalogEntry(source_id="remote_remotive", name="Remotive", provider="remotive")
    none_provider = SourceCatalogEntry(source_id="x", name="X", provider="")
    unknown = SourceCatalogEntry(source_id="y", name="Y", provider="does_not_exist")

    assert isinstance(providers.provider_for(remoteok), RemoteOkProvider)
    assert isinstance(providers.provider_for(remotive), RemotiveProvider)
    assert providers.provider_for(none_provider) is None
    assert providers.provider_for(unknown) is None  # unknown name -> search fallback
    assert providers.provider_for(None) is None


def test_provider_run_stats_to_dict_content_free() -> None:
    result = RemoteOkProvider(http=_http_ok(REMOTEOK_PAYLOAD)).fetch(source_id="remote_remoteok")
    stats = result.to_run_stats().to_dict()
    assert stats["source_id"] == "remote_remoteok"
    assert stats["provider"] == "remoteok"
    assert stats["status"] == "ok"
    assert stats["candidate_jobs"] == 2
    # content-free: no titles/urls/descriptions anywhere in the dict
    text = json.dumps(stats)
    assert "Senior ML Engineer" not in text
    assert "acme.apply" not in text


def test_provider_health_classification_diagnostic_only() -> None:
    from job_agent.catalog import SourceCatalogEntry

    entry = SourceCatalogEntry(source_id="s", name="S", provider="remoteok")
    disabled = SourceCatalogEntry(source_id="d", name="D", provider="")
    assert provider_health_for(None, None) == ProviderHealth.DISABLED
    assert provider_health_for(disabled, None) == ProviderHealth.DISABLED
    assert provider_health_for(entry, None) == ProviderHealth.NOT_TESTED
    assert provider_health_for(entry, {"status": "ok", "hits": 5}) == ProviderHealth.HEALTHY
    assert provider_health_for(entry, {"status": "ok", "hits": 0}) == ProviderHealth.ZERO_YIELD
    assert provider_health_for(entry, {"status": "failed"}) == ProviderHealth.FAILED


def test_parse_salary_text_variants() -> None:
    assert providers._parse_salary_text(None) == (None, None)
    assert providers._parse_salary_text(60) == (60.0, None)
    assert providers._parse_salary_text("$60k") == (60_000.0, None)
    assert providers._parse_salary_text("€65.000") == (65_000.0, None)
    assert providers._parse_salary_text("70k - 80k") == (70_000.0, 80_000.0)
    minv, maxv = providers._parse_salary_text("$100k-$120k")
    assert (minv, maxv) == (100_000.0, 120_000.0)
    assert providers._parse_salary_text("Unpaid") == (None, None)
    assert providers._parse_salary_text("") == (None, None)
