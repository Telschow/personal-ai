"""Tests for bounded LLM-assisted memory candidate proposals.

The LLM is a proposal generator only. Coverage focuses on:

* the strict structured-output contract (valid/multiple/malformed batches,
  invalid items dropped, never repaired);
* the deterministic conversion invariants (evidence must exist and be
  user-authored, statements must reference the user, sensitive forms and
  negations/questions are dropped, application authority over kind/temporal);
* the guarantee that every write still travels through the deterministic
  policy-gated curator, including the mandatory regression: a policy-accepted
  candidate whose ``memory.write`` gate is denied never writes;
* bounded model input/output, bounded retries, count-only failures and
  aggregate-only (content-free) reporting;
* idempotent reruns that never duplicate memories or evidence.
"""

from __future__ import annotations

import json
import re
import sqlite3

import pytest

from personal_ai.agents.policy import ApprovalRequiredError
from personal_ai.documents.conversations import (
    Conversation,
    ConversationMessage,
)
from personal_ai.memory.models import MemoryKind, MemoryStatus, TemporalScope
from personal_ai.memory.proposals import (
    MAX_EVIDENCE_PER_PROPOSAL,
    MAX_PROPOSALS_PER_BATCH,
    MAX_RATIONALE_CHARS,
    LLMConversationMemoryIngestor,
    LLMConversationMemoryReport,
    LLMMemoryProposalExtractor,
    MalformedMemoryProposalError,
    MemoryProposal,
    MemoryProposalError,
    parse_memory_proposal_batch,
    to_memory_candidate,
)
from personal_ai.memory.service import MemoryService
from personal_ai.memory.store import MemoryStore
from personal_ai.ollama_client import ChatResponse, OllamaConnectionError
from personal_ai.storage import ConversationStore, connect_database


def _service() -> MemoryService:
    return MemoryService(MemoryStore(sqlite3.connect(":memory:")))


def _conv(
    conv_id: str = "conv-1",
    *,
    source_type: str = "chatgpt",
    title: str = "Test",
    created_at: str = "2026-01-01T00:00:00+00:00",
    modified_at: str = "2026-01-02T00:00:00+00:00",
) -> Conversation:
    return Conversation(
        id=conv_id,
        title=title,
        source_type=source_type,
        created_at=created_at,
        modified_at=modified_at,
        metadata={},
    )


def _msg(
    msg_id: str,
    content: str,
    *,
    role: str = "user",
    index: int = 0,
    timestamp: str | None = None,
    is_active_branch: bool = True,
    conv_id: str = "conv-1",
) -> ConversationMessage:
    return ConversationMessage(
        id=msg_id,
        conversation_id=conv_id,
        message_index=index,
        role=role,
        speaker=role.capitalize(),
        content_text=content,
        content_type="text",
        timestamp=timestamp,
        is_active_branch=is_active_branch,
        metadata={},
    )


def _prop(
    statement: str = "The user works at BCG",
    kind: str = "work",
    temporal: str = "current",
    confidence: float = 0.9,
    evidence: tuple[str, ...] = ("m1",),
    rationale: str = "",
) -> dict[str, object]:
    payload: dict[str, object] = {
        "statement": statement,
        "kind": kind,
        "temporal_scope": temporal,
        "confidence": confidence,
        "evidence_message_ids": list(evidence),
    }
    if rationale:
        payload["rationale"] = rationale
    return payload


def _batch_json(*proposals: dict[str, object]) -> str:
    return json.dumps({"proposals": list(proposals)})


class _FakeModelClient:
    """Scripted stand-in for the narrow ``chat`` model surface.

    Queued payloads are consumed in order; the last payload repeats once the
    queue is empty, so a persistent failure (an exception) keeps failing —
    matching how a real endpoint behaves across retries.
    """

    def __init__(self, *responses: str | Exception) -> None:
        self._responses = list(responses)
        self._last: str | Exception = _batch_json(_prop())
        self.calls: list[tuple[object, bool, object]] = []

    def chat(self, messages: object, *, think: bool, format: object) -> ChatResponse:
        self.calls.append((messages, think, format))
        if self._responses:
            self._last = self._responses.pop(0)
        if isinstance(self._last, Exception):
            raise self._last
        return ChatResponse(content=self._last, model="fake", done=True)


def _store(*conversations):
    connection = connect_database(":memory:")
    store = ConversationStore(connection)
    for conv, messages in conversations:
        store.save_conversation(conv)
        store.save_messages(messages)
    return connection, store


def _work_pair() -> tuple[Conversation, tuple[ConversationMessage, ...]]:
    return _conv(), (_msg("m1", "I work at BCG"),)


# ---- model-output parsing ----------------------------------------------------


def test_parse_valid_single_proposal() -> None:
    batch = parse_memory_proposal_batch(
        _batch_json(_prop(statement="I like coffee", kind="preference", confidence=0.8))
    )
    assert batch.dropped == 0
    (proposal,) = batch.proposals
    assert proposal.statement == "I like coffee"
    assert proposal.kind is MemoryKind.PREFERENCE
    assert proposal.temporal_scope is TemporalScope.CURRENT
    assert proposal.confidence == 0.8
    assert proposal.evidence_message_ids == ("m1",)


def test_parse_fenced_json_proposal() -> None:
    body = _batch_json(_prop())
    batch = parse_memory_proposal_batch(f"```json\n{body}\n```")
    assert len(batch.proposals) == 1


def test_parse_multiple_proposals() -> None:
    batch = parse_memory_proposal_batch(
        _batch_json(
            _prop(),
            _prop(statement="The user lives in Lisbon", kind="location_context"),
        )
    )
    assert len(batch.proposals) == 2
    assert batch.dropped == 0


def test_parse_non_json_raises() -> None:
    with pytest.raises(MalformedMemoryProposalError):
        parse_memory_proposal_batch("this is not json")


def test_parse_non_string_content_raises() -> None:
    with pytest.raises(MalformedMemoryProposalError):
        parse_memory_proposal_batch(123)  # type: ignore[arg-type]


def test_parse_wrong_container_raises() -> None:
    with pytest.raises(MalformedMemoryProposalError):
        parse_memory_proposal_batch(json.dumps({"proposals": {"statement": "x"}}))
    with pytest.raises(MalformedMemoryProposalError):
        parse_memory_proposal_batch(json.dumps([_prop()]))


def test_parse_missing_proposals_key_raises() -> None:
    with pytest.raises(MalformedMemoryProposalError):
        parse_memory_proposal_batch(json.dumps({"summary": "x"}))


def test_parse_empty_proposals_is_valid() -> None:
    batch = parse_memory_proposal_batch(json.dumps({"proposals": []}))
    assert batch.proposals == ()
    assert batch.dropped == 0


def test_parse_invalid_kind_is_dropped() -> None:
    batch = parse_memory_proposal_batch(_batch_json(_prop(kind="not_a_kind"), _prop()))
    assert len(batch.proposals) == 1
    assert batch.dropped == 1


def test_parse_invalid_temporal_is_dropped() -> None:
    assert (
        parse_memory_proposal_batch(_batch_json(_prop(temporal="someday"))).dropped == 1
    )


def test_parse_invalid_confidence_is_dropped() -> None:
    for bad in (1.5, -0.1, True, "0.8", float("nan"), float("inf")):
        batch = parse_memory_proposal_batch(_batch_json(_prop(confidence=bad)))
        assert batch.dropped == 1, bad
        assert batch.proposals == (), bad


def test_parse_oversized_statement_is_dropped() -> None:
    long_text = "word " * 300
    batch = parse_memory_proposal_batch(_batch_json(_prop(statement=long_text)))
    assert batch.dropped == 1
    assert batch.proposals == ()


def test_parse_empty_statement_is_dropped() -> None:
    batch = parse_memory_proposal_batch(_batch_json(_prop(statement="   ")))
    assert batch.dropped == 1


def test_parse_too_many_evidence_refs_is_dropped() -> None:
    evidence = tuple(f"m{i}" for i in range(MAX_EVIDENCE_PER_PROPOSAL + 1))
    batch = parse_memory_proposal_batch(_batch_json(_prop(evidence=evidence)))
    assert batch.dropped == 1


def test_parse_empty_evidence_is_dropped() -> None:
    assert parse_memory_proposal_batch(_batch_json(_prop(evidence=()))).dropped == 1


def test_parse_duplicate_evidence_id_is_dropped() -> None:
    batch = parse_memory_proposal_batch(_batch_json(_prop(evidence=("m1", "m1"))))
    assert batch.dropped == 1


def test_parse_unknown_field_is_dropped() -> None:
    bad = {**_prop(), "extra": "x"}
    assert parse_memory_proposal_batch(_batch_json(bad)).dropped == 1


def test_parse_missing_required_field_is_dropped() -> None:
    bad = {
        "kind": "work",
        "temporal_scope": "current",
        "confidence": 0.9,
        "evidence_message_ids": ["m1"],
    }
    assert parse_memory_proposal_batch(_batch_json(bad)).dropped == 1


def test_parse_wrong_value_types_are_dropped() -> None:
    for mutation in (
        {"statement": 5},
        {"kind": 5},
        {"temporal_scope": 5},
        {"evidence_message_ids": "m1"},
        {"evidence_message_ids": [1]},
        {"rationale": 5},
    ):
        bad = {**_prop(), **mutation}
        assert parse_memory_proposal_batch(_batch_json(bad)).dropped == 1, mutation


def test_parse_overlong_rationale_is_dropped() -> None:
    rationale = "x" * (MAX_RATIONALE_CHARS + 1)
    assert (
        parse_memory_proposal_batch(_batch_json(_prop(rationale=rationale))).dropped
        == 1
    )


def test_batch_over_max_proposals_raises() -> None:
    body = _batch_json(
        *(_prop(statement=f"Statement {i}") for i in range(MAX_PROPOSALS_PER_BATCH + 1))
    )
    with pytest.raises(MemoryProposalError):
        parse_memory_proposal_batch(body)


def test_proposal_is_never_repaired() -> None:
    batch = parse_memory_proposal_batch(
        _batch_json(_prop(kind="work", confidence=0.99), _prop(kind="bogus"))
    )
    assert len(batch.proposals) == 1
    assert batch.proposals[0].kind is MemoryKind.WORK


# ---- deterministic conversion invariants -------------------------------------


def _convert(proposal: MemoryProposal):
    return to_memory_candidate(proposal, _conv(), (_msg("m1", "I work at BCG"),))


def test_conversion_evidence_unknown_is_dropped() -> None:
    proposal = MemoryProposal(
        statement="The user works at BCG",
        kind="work",
        temporal_scope="current",
        confidence=0.9,
        evidence_message_ids=("nope",),
    )
    assert _convert(proposal) == (None, "evidence_unknown")


def test_conversion_evidence_not_user_is_dropped() -> None:
    proposal = MemoryProposal(
        statement="The user works at BCG",
        kind="work",
        temporal_scope="current",
        confidence=0.9,
        evidence_message_ids=("a1",),
    )
    candidate, reason = to_memory_candidate(
        proposal,
        _conv(),
        (_msg("a1", "The user works at BCG", role="assistant"),),
    )
    assert candidate is None
    assert reason == "evidence_not_user"


def test_conversion_evidence_not_claim_is_dropped() -> None:
    proposal = MemoryProposal(
        statement="The user likes bright blue",
        kind="preference",
        temporal_scope="current",
        confidence=0.9,
        evidence_message_ids=("m1",),
    )
    for request in (
        "Can you draft a report?",
        "Please summarize this",
        "I want you to review this",
    ):
        candidate, reason = to_memory_candidate(
            proposal,
            _conv(),
            (_msg("m1", request),),
        )
        assert candidate is None, request
        assert reason == "evidence_not_claim", request


def test_conversion_third_party_statement_is_dropped() -> None:
    proposal = MemoryProposal(
        statement="Alice works at BCG",
        kind="work",
        temporal_scope="current",
        confidence=0.9,
        evidence_message_ids=("m1",),
    )
    assert _convert(proposal) == (None, "not_user_statement")


def test_conversion_sensitive_form_is_dropped() -> None:
    for sensitive in (
        "The user earns 5000 euros a month",
        "The user's contact is me@example.com",
        "The user's site is https://example.com",
    ):
        proposal = MemoryProposal(
            statement=sensitive,
            kind="personal_fact",
            temporal_scope="current",
            confidence=0.9,
            evidence_message_ids=("m1",),
        )
        assert _convert(proposal) == (None, "sensitive_form"), sensitive


def test_conversion_question_statement_is_dropped() -> None:
    proposal = MemoryProposal(
        statement="The user works at BCG?",
        kind="work",
        temporal_scope="current",
        confidence=0.9,
        evidence_message_ids=("m1",),
    )
    assert _convert(proposal) == (None, "question")


def test_conversion_negation_is_dropped() -> None:
    for negated in (
        "The user does not work at BCG",
        "The user never runs",
        "The user no longer lives in Berlin",
    ):
        proposal = MemoryProposal(
            statement=negated,
            kind="fact",
            temporal_scope="current",
            confidence=0.9,
            evidence_message_ids=("m1",),
        )
        assert _convert(proposal) == (None, "negated"), negated


def test_conversion_goal_trigger_overrides_kind_and_temporal() -> None:
    proposal = MemoryProposal(
        statement="The user wants to learn Rust",
        kind="skill",
        temporal_scope="historical",
        confidence=0.9,
        evidence_message_ids=("m1",),
    )
    candidate, reason = _convert(proposal)
    assert reason is None
    assert candidate is not None
    assert candidate.statement == "The user wants to learn Rust"
    assert candidate.kind is MemoryKind.GOAL
    assert candidate.temporal_scope is TemporalScope.CURRENT


def test_conversion_recurring_trigger_overrides_temporal() -> None:
    proposal = MemoryProposal(
        statement="The user exercises every morning",
        kind="routine",
        temporal_scope="current",
        confidence=0.9,
        evidence_message_ids=("m1",),
    )
    candidate, reason = _convert(proposal)
    assert reason is None
    assert candidate is not None
    assert candidate.kind is MemoryKind.ROUTINE
    assert candidate.temporal_scope is TemporalScope.RECURRING
    assert candidate.recurrence == 2


def test_conversion_past_trigger_overrides_temporal() -> None:
    proposal = MemoryProposal(
        statement="The user used to work at McKinsey",
        kind="work",
        temporal_scope="current",
        confidence=0.9,
        evidence_message_ids=("m1",),
    )
    candidate, reason = _convert(proposal)
    assert reason is None
    assert candidate is not None
    assert candidate.temporal_scope is TemporalScope.HISTORICAL


def test_conversion_application_sets_all_non_proposed_scores() -> None:
    proposal = MemoryProposal(
        statement="The user is a resident of Lisbon",
        kind="identity",
        temporal_scope="current",
        confidence=0.6,
        evidence_message_ids=("m1",),
    )
    candidate, reason = _convert(proposal)
    assert reason is None
    assert candidate is not None
    assert candidate.durability == 0.9
    assert candidate.relevance == 0.85
    assert candidate.specificity == 0.7
    assert candidate.utility == 0.8
    assert candidate.confidence == 0.6
    assert candidate.assertion_status.value == "asserted"


def test_conversion_evidence_is_provenance_only() -> None:
    proposal = MemoryProposal(
        statement="I work at BCG",
        kind="work",
        temporal_scope="current",
        confidence=0.9,
        evidence_message_ids=("m1",),
    )
    candidate, reason = to_memory_candidate(
        proposal,
        _conv(),
        (_msg("m1", "I work at BCG", timestamp="2026-01-15T10:30:00+00:00"),),
    )
    assert reason is None
    assert candidate is not None
    (ref,) = candidate.evidence
    assert ref.source_type == "chatgpt"
    assert ref.source_id == "conv-1"
    assert ref.source_document_id == "m1"
    assert ref.source_timestamp == "2026-01-15T10:30:00+00:00"
    rendered = json.dumps(ref.to_dict())
    assert "BCG" not in rendered


# ---- extractor: bounded model interaction ------------------------------------


def test_extractor_skips_conversation_without_user_message() -> None:
    client = _FakeModelClient()
    extractor = LLMMemoryProposalExtractor(client)
    result = extractor.extract(_conv(), (_msg("a1", "Hello!", role="assistant"),))
    assert result.candidates == ()
    assert result.model_calls == 0
    assert result.failed is False
    assert client.calls == []


def test_extractor_bounded_window_truncates_transcript() -> None:
    client = _FakeModelClient()
    extractor = LLMMemoryProposalExtractor(client, max_messages=5)
    messages = tuple(
        _msg(f"m{i}", f"I work on project {i}", index=i) for i in range(10)
    )
    extractor.extract(_conv(), messages)
    (model_messages, _think, _fmt) = client.calls[0]
    user_content = model_messages[1].content  # type: ignore[union-attr]
    visible_ids = set(re.findall(r"\[(m\d+)\]", user_content))
    assert visible_ids <= {"m0", "m1", "m2", "m3", "m4"}
    assert len(visible_ids) == 5


def test_extractor_ollama_failure_retries_then_reports() -> None:
    client = _FakeModelClient(OllamaConnectionError("boom"))
    extractor = LLMMemoryProposalExtractor(client, max_retries=2)
    result = extractor.extract(_conv(), (_msg("m1", "I work at BCG"),))
    assert result.candidates == ()
    assert result.failed is True
    assert result.failure_reason == "ollama_error"
    assert result.model_calls == 3


def test_extractor_malformed_output_retries_then_reports() -> None:
    client = _FakeModelClient("definitely not json")
    extractor = LLMMemoryProposalExtractor(client, max_retries=1)
    result = extractor.extract(_conv(), (_msg("m1", "I work at BCG"),))
    assert result.failed is True
    assert result.failure_reason == "malformed_output"
    assert result.candidates == ()


def test_extractor_retry_then_success_recovers() -> None:
    client = _FakeModelClient(
        OllamaConnectionError("transient"),
        _batch_json(_prop()),
    )
    extractor = LLMMemoryProposalExtractor(client, max_retries=2)
    result = extractor.extract(_conv(), (_msg("m1", "I work at BCG"),))
    assert result.failed is False
    assert result.model_calls == 2
    (candidate,) = result.candidates
    assert candidate.statement == "The user works at BCG"


def test_extractor_empty_response_is_success_without_candidates() -> None:
    client = _FakeModelClient(json.dumps({"proposals": []}))
    extractor = LLMMemoryProposalExtractor(client)
    result = extractor.extract(_conv(), (_msg("m1", "I work at BCG"),))
    assert result.candidates == ()
    assert result.failed is False
    assert result.model_calls == 1


def test_extractor_caps_candidates_per_unit() -> None:
    body = _batch_json(
        *(  # 4 distinct facts
            _prop(statement=f"The user enjoys activity {i}", kind="interest")
            for i in range(4)
        )
    )
    client = _FakeModelClient(body)
    extractor = LLMMemoryProposalExtractor(client, max_candidates_per_unit=2)
    result = extractor.extract(_conv(), (_msg("m1", "I like several things"),))
    assert len(result.candidates) == 2
    assert result.proposals_parsed == 4


def test_extractor_dedupes_proposals_within_a_unit() -> None:
    body = _batch_json(_prop(), _prop())
    client = _FakeModelClient(body)
    extractor = LLMMemoryProposalExtractor(client)
    result = extractor.extract(_conv(), (_msg("m1", "I work at BCG"),))
    assert len(result.candidates) == 1
    assert result.proposals_parsed == 2


def test_extractor_counts_dropped_proposals() -> None:
    body = _batch_json(_prop(), _prop(kind="not_a_kind"))
    client = _FakeModelClient(body)
    extractor = LLMMemoryProposalExtractor(client)
    result = extractor.extract(_conv(), (_msg("m1", "I work at BCG"),))
    assert len(result.candidates) == 1
    assert result.proposals_parsed == 1
    assert result.proposals_dropped == 1


def test_extractor_model_calls_bounded_by_retry_limit() -> None:
    client = _FakeModelClient(OllamaConnectionError("boom"))
    extractor = LLMMemoryProposalExtractor(client, max_retries=0)
    result = extractor.extract(_conv(), (_msg("m1", "I work at BCG"),))
    assert result.model_calls == 1
    assert result.failed is True


# ---- ingestor: policy-gated orchestration ------------------------------------


def test_ingestor_rejects_unsupported_source() -> None:
    connection, store = _store(_work_pair())
    try:
        ingestor = LLMConversationMemoryIngestor(
            store, _service(), client=_FakeModelClient()
        )
        with pytest.raises(ValueError):
            ingestor.ingest("email")
    finally:
        connection.close()


def test_ingestor_requires_client_or_extractor() -> None:
    connection, store = _store(_work_pair())
    try:
        with pytest.raises(MemoryProposalError):
            LLMConversationMemoryIngestor(store, _service())
    finally:
        connection.close()


def test_ingestor_applies_accepted_candidate_and_is_idempotent() -> None:
    service = _service()
    connection, store = _store(_work_pair())
    try:
        client = _FakeModelClient(_batch_json(_prop()))
        ingestor = LLMConversationMemoryIngestor(store, service, client=client)

        first = ingestor.ingest("chatgpt")
        second = ingestor.ingest("chatgpt")

        assert first.conversations_scanned == 1
        assert first.messages_scanned == 1
        assert first.model_batches == 1
        assert first.failed_batches == 0
        assert first.proposals_parsed == 1
        assert first.tally.candidates == 1
        assert first.tally.accepted == 1
        assert first.tally.writes == 1
        assert first.tally.created == 1
        assert first.tally.updated == 0

        assert second.tally.writes == 1
        assert second.tally.created == 0
        assert second.tally.updated == 1

        assert service.counts()["active"] == 1
        memory = service.list()[0]
        assert memory.status is MemoryStatus.ACTIVE
        assert memory.content == "The user works at BCG"
        assert len(service.evidence_for(memory.memory_id)) == 1
    finally:
        connection.close()


def test_ingestor_denied_policy_engine_gate_never_writes() -> None:
    """Mandatory regression: policy ACCEPT + denied write gate => no write."""
    service = _service()
    connection, store = _store(_work_pair())
    try:
        client = _FakeModelClient(_batch_json(_prop()))
        ingestor = LLMConversationMemoryIngestor(
            store,
            service,
            client=client,
            auto_approver=lambda *args: False,
        )
        with pytest.raises(ApprovalRequiredError):
            ingestor.ingest("chatgpt")
        assert service.counts()["memories"] == 0
    finally:
        connection.close()


def test_ingestor_secret_never_reaches_write_path() -> None:
    service = _service()
    connection, store = _store(_work_pair())
    try:
        client = _FakeModelClient(
            _batch_json(_prop(statement="The user's password is hunter2", kind="fact"))
        )
        report = LLMConversationMemoryIngestor(store, service, client=client).ingest(
            "chatgpt"
        )
        assert report.tally.rejected == 1
        assert report.tally.writes == 0
        assert report.tally.require_approval == 0
        assert service.counts()["memories"] == 0
    finally:
        connection.close()


def test_ingestor_sensitive_requires_approval_without_writing() -> None:
    service = _service()
    connection, store = _store(_work_pair())
    try:
        client = _FakeModelClient(
            _batch_json(
                _prop(statement="The user's salary is 120k", kind="personal_fact")
            )
        )
        ingestor = LLMConversationMemoryIngestor(store, service, client=client)
        report = ingestor.ingest("chatgpt")
        assert report.tally.require_approval == 1
        assert report.tally.writes == 0
        assert service.counts()["memories"] == 0
    finally:
        connection.close()


def test_ingestor_sensitive_writes_with_interactive_approver() -> None:
    service = _service()
    connection, store = _store(_work_pair())
    try:
        client = _FakeModelClient(
            _batch_json(
                _prop(statement="The user's salary is 120k", kind="personal_fact")
            )
        )
        ingestor = LLMConversationMemoryIngestor(
            store,
            service,
            client=client,
            interactive_approver=lambda *args: True,
        )
        report = ingestor.ingest("chatgpt")
        assert report.tally.require_approval == 1
        assert report.tally.writes == 1
        assert service.counts()["active"] == 1
    finally:
        connection.close()


def test_ingestor_assistant_claim_never_becomes_candidate() -> None:
    service = _service()
    connection, store = _store(
        (_conv(), (_msg("m1", "I suggest the user works at BCG", role="assistant"),))
    )
    try:
        client = _FakeModelClient()
        report = LLMConversationMemoryIngestor(store, service, client=client).ingest(
            "chatgpt"
        )
        assert report.tally.candidates == 0
        assert report.model_batches == 0
        assert client.calls == []
        assert service.counts()["memories"] == 0
    finally:
        connection.close()


def test_ingestor_persistent_model_failure_aborts_batch_safely() -> None:
    service = _service()
    connection, store = _store(_work_pair())
    try:
        client = _FakeModelClient(
            OllamaConnectionError("down"), OllamaConnectionError("down")
        )
        ingestor = LLMConversationMemoryIngestor(store, service, client=client)
        report = ingestor.ingest("chatgpt")
        assert report.failed_batches == 1
        assert report.model_batches == 2
        assert report.errors == (("ollama_error", 1),)
        assert report.tally.candidates == 0
        assert report.tally.writes == 0
        assert service.counts()["memories"] == 0
    finally:
        connection.close()


def test_ingestor_partial_failure_does_not_corrupt_run() -> None:
    service = _service()
    good = _conv("conv-good")
    bad = _conv("conv-bad")
    connection, store = _store(
        (bad, (_msg("mb", "I work at BCG", conv_id="conv-bad"),)),
        (good, (_msg("mg", "I work at BCG", conv_id="conv-good"),)),
    )
    try:
        client = _FakeModelClient(
            OllamaConnectionError("down"),
            OllamaConnectionError("down"),
            _batch_json(_prop(evidence=("mg",))),
        )
        report = LLMConversationMemoryIngestor(store, service, client=client).ingest(
            "chatgpt"
        )
        assert report.failed_batches == 1
        assert report.model_batches == 3
        assert report.tally.writes == 1
        assert report.tally.created == 1
        assert service.counts()["active"] == 1
    finally:
        connection.close()


def test_ingestor_bounded_conversations_with_offset() -> None:
    conversations = [
        (
            _conv(f"conv-{index}"),
            (_msg(f"m-{index}", "I work at BCG", conv_id=f"conv-{index}"),),
        )
        for index in range(8)
    ]
    connection, store = _store(*conversations)
    try:
        service = _service()
        client = _FakeModelClient(
            _batch_json(_prop(evidence=("m-2",))),
            _batch_json(_prop(evidence=("m-3",))),
            _batch_json(_prop(evidence=("m-4",))),
        )
        ingestor = LLMConversationMemoryIngestor(
            store,
            service,
            client=client,
            max_conversations=3,
            offset=2,
        )
        report = ingestor.ingest("chatgpt")
        assert report.conversations_scanned == 3
        assert report.tally.created == 1
        assert report.tally.updated == 2
        active = [m.content for m in service.list()]
        assert active == ["The user works at BCG"]
        memory = service.list()[0]
        assert len(service.evidence_for(memory.memory_id)) == 3
    finally:
        connection.close()


def test_ingestor_report_is_aggregate_only_and_content_free() -> None:
    service = _service()
    connection, store = _store(_work_pair())
    try:
        report = LLMConversationMemoryIngestor(
            store, service, client=_FakeModelClient(_batch_json(_prop()))
        ).ingest("chatgpt")
    finally:
        connection.close()
    assert isinstance(report, LLMConversationMemoryReport)
    rendered = json.dumps(report.summary())
    assert "BCG" not in rendered
    assert "statement" not in rendered
    assert report.summary()["source_type"] == "chatgpt"
    assert report.summary()["conversations_scanned"] == 1
