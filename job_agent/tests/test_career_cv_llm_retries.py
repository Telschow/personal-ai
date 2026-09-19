"""Bounded LLM retries/backoff on transient failures (fail-closed unchanged)."""

import json

import httpx

from job_agent.career.cv_llm import OllamaCvClient


def _proposal_bytes() -> bytes:
    return json.dumps(
        {
            "message": {
                "content": json.dumps(
                    {
                        "headline": "h",
                        "summary": "s",
                        "bullets": [{"text": "Led X", "evidence_id": "e1"}],
                    }
                )
            }
        }
    ).encode()


def _make_client(handler, **kwargs) -> tuple[OllamaCvClient, list[int]]:
    statuses: list[int] = []

    def wrapped(request: httpx.Request) -> httpx.Response:
        statuses.append(1)
        return handler(request)

    opts = {"max_retries": 2, "backoff_seconds": 0.001, **kwargs}
    client = OllamaCvClient(
        "http://x",
        "qwen3.5:9b",
        client=httpx.Client(transport=httpx.MockTransport(wrapped)),
        **opts,
    )
    return client, statuses


def _ok_response() -> httpx.Response:
    return httpx.Response(200, content=_proposal_bytes())


def test_transient_then_success_retries_and_succeeds():
    counter = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        counter["n"] += 1
        if counter["n"] < 3:
            return httpx.Response(503, text="service unavailable")
        return _ok_response()

    client, calls = _make_client(handler)
    proposal = client.generate_proposal(job={}, evidence_items=[], evidence_ids=["e1"])
    assert proposal is not None
    assert proposal.bullets[0].evidence_id == "e1"
    assert len(calls) == 3  # two failed + one success


def test_persistent_transient_fails_closed_after_bounded_retries():
    calls_expected = 3  # max_retries=2 -> 3 attempts

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="boom")

    client, calls = _make_client(handler)
    assert client.generate_proposal(job={}, evidence_items=[], evidence_ids=[]) is None
    assert len(calls) == calls_expected


def test_client_error_not_retried():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, text="bad request")

    client, calls = _make_client(handler)
    assert client.generate_proposal(job={}, evidence_items=[], evidence_ids=[]) is None
    assert len(calls) == 1  # 4xx is not transient, no retry


def test_network_error_retried_then_fails_closed():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    client, calls = _make_client(handler)
    assert client.generate_proposal(job={}, evidence_items=[], evidence_ids=[]) is None
    assert len(calls) == 3


def test_retries_disabled_with_max_retries_zero():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="down")

    client, calls = _make_client(handler, max_retries=0)
    assert client.generate_proposal(job={}, evidence_items=[], evidence_ids=[]) is None
    assert len(calls) == 1


def test_malformed_success_is_not_retried_and_fails_closed():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"not json at all")

    client, calls = _make_client(handler)
    assert client.generate_proposal(job={}, evidence_items=[], evidence_ids=[]) is None
    assert len(calls) == 1  # a 200 with malformed body is a definitive result, no retry
