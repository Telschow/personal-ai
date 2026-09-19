"""CV proposal LLM layer: fail-closed, evidence-allow-listed, no writes."""

import json

import pytest

from job_agent.career.cv_llm import (
    SCHEMA_VERSION,
    CvLlmError,
    OllamaCvClient,
    parse_tailoring,
)

ALLOW = {"e1", "e2", "e3"}


def test_parse_valid_proposal():
    raw = json.dumps(
        {
            "headline": "Product leadership in automotive AI",
            "summary": "Product Owner with autonomous driving delivery.",
            "bullets": [
                {"text": "Led Automated Valet Parking design", "evidence_id": "e1"},
                {"text": "Product Owner Autonomous Driving", "evidence_id": "e2"},
            ],
        }
    )
    proposal = parse_tailoring(raw, ALLOW)
    assert proposal.headline
    assert len(proposal.bullets) == 2


def test_parse_drops_non_allowlisted_evidence():
    raw = json.dumps(
        {
            "headline": "h",
            "summary": "s",
            "bullets": [
                {"text": "backed line", "evidence_id": "e1"},
                {"text": "unbacked line", "evidence_id": "fabricated"},
            ],
        }
    )
    proposal = parse_tailoring(raw, ALLOW)
    assert [b.evidence_id for b in proposal.bullets] == ["e1"]


def test_parse_raises_when_no_bullets_survive():
    raw = json.dumps(
        {
            "headline": "h",
            "summary": "s",
            "bullets": [{"text": "only unbacked", "evidence_id": "zzz"}],
        }
    )
    with pytest.raises(CvLlmError):
        parse_tailoring(raw, ALLOW)


def test_parse_malformed_json_raises():
    with pytest.raises(CvLlmError):
        parse_tailoring("this is not json {", ALLOW)


def test_parse_non_object_raises():
    with pytest.raises(CvLlmError):
        parse_tailoring('["not","an","object"]', ALLOW)


def test_parse_missing_required_field_raises():
    raw = json.dumps({"headline": "h", "bullets": [{"text": "t", "evidence_id": "e1"}]})
    with pytest.raises(CvLlmError):
        parse_tailoring(raw, ALLOW)


def test_parse_caps_lengths():
    raw = json.dumps(
        {
            "headline": "x" * 500,
            "summary": "y" * 900,
            "bullets": [{"text": "b" * 2000, "evidence_id": e} for e in ["e1", "e2", "e3"]],
        }
    )
    proposal = parse_tailoring(raw, ALLOW)
    assert len(proposal.headline) <= 120
    assert len(proposal.summary) <= 400
    assert len(proposal.bullets) <= 7


def test_schema_version():
    assert SCHEMA_VERSION == "cv-proposal-v1"


def test_ollama_client_uses_format_and_fails_closed():
    import httpx

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert body["format"] is not None
        assert "bullets" in body["format"]["properties"]
        assert body["think"] is False  # reasoning off: content must be non-empty
        return httpx.Response(
            200,
            json={
                "message": {
                    "content": json.dumps(
                        {
                            "headline": "h",
                            "summary": "s",
                            "bullets": [{"text": "Led X", "evidence_id": "e1"}],
                        }
                    )
                }
            },
        )

    client = OllamaCvClient("http://x", "qwen3.5:9b", client=httpx.Client(transport=httpx.MockTransport(handler)))
    job = {"title": "AI PM", "company": "ACME", "description": "autonomous driving"}
    items = [{"evidence_id": "e1", "claim": "Led X"}]
    proposal = client.generate_proposal(job=job, evidence_items=items, evidence_ids=["e1"])
    assert proposal is not None
    assert proposal.bullets[0].evidence_id == "e1"


def test_call_log_records_tailor_success_and_retries():
    import httpx

    counter = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        counter["n"] += 1
        if counter["n"] < 3:
            return httpx.Response(503, text="down")
        return httpx.Response(
            200,
            json={
                "message": {
                    "content": json.dumps(
                        {
                            "headline": "h",
                            "summary": "s",
                            "bullets": [{"text": "Led X", "evidence_id": "e1"}],
                        }
                    )
                }
            },
        )

    from job_agent.career.llm_log import LlmCallLog, LlmOutcome

    call_log = LlmCallLog()
    client = OllamaCvClient(
        "http://x",
        "qwen3.5:9b",
        max_retries=2,
        backoff_seconds=0.001,
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        call_log=call_log,
    )
    proposal = client.generate_proposal(job={}, evidence_items=[], evidence_ids=["e1"])
    assert proposal is not None
    assert counter["n"] == 3  # two retries, one success
    rec = call_log.records[0]
    assert rec.call_type == "tailor"
    assert rec.outcome == LlmOutcome.SUCCESS
    assert rec.retry_count == 2
    assert rec.duration_seconds >= 0.0
    assert rec.prompt_chars > 0
    assert call_log.outcome_counts() == {"success": 1}


def test_call_log_records_tailor_http_error_fail_closed():
    import httpx

    from job_agent.career.llm_log import LlmCallLog, LlmOutcome

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="boom")

    call_log = LlmCallLog()
    client = OllamaCvClient(
        "http://x",
        "qwen3.5:9b",
        max_retries=2,
        backoff_seconds=0.001,
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        call_log=call_log,
    )
    assert client.generate_proposal(job={}, evidence_items=[], evidence_ids=[]) is None
    rec = call_log.records[0]
    assert rec.outcome == LlmOutcome.HTTP_ERROR
    assert rec.retry_count == 2  # 3 attempts total -> 2 retries
    assert call_log.outcome_counts() == {"http_error": 1}


def test_ollama_client_returns_none_on_http_error():
    import httpx

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="boom")

    client = OllamaCvClient("http://x", "qwen3.5:9b", client=httpx.Client(transport=httpx.MockTransport(handler)))
    assert client.generate_proposal(job={}, evidence_items=[], evidence_ids=[]) is None
