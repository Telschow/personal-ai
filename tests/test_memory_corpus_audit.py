"""Hermetic tests for the bounded, aggregate-only corpus audit.

The audit reads raw conversation export directories through the existing
loaders/extractor/policy and returns counts and category tallies only. These
tests verify: deterministic bounded sampling, aggregate-only output (privacy
sentinels never surface), extraction/policy/provenance accounting, the Phase
21A ES verb-gap measurement, scratch-DB two-pass idempotency, and hard no-write
behavior (no DB, never touches production).
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from personal_ai.memory.corpus_audit import (
    MAX_CONVERSATIONS_PER_SOURCE,
    run_corpus_audit,
)
from personal_ai.storage.documents import connect_database


def _write_gemini_conversation(
    directory: Path,
    filename: str,
    *,
    conv_id: str = "abc123",
    title: str = "Test",
    messages: list[dict[str, str]] | None = None,
) -> None:
    if messages is None:
        messages = [
            {"role": "user", "content": "Hello world"},
            {"role": "assistant", "content": "Hi!"},
        ]
    data = {
        "id": conv_id,
        "title": title,
        "messages": messages,
        "messageCount": len(messages),
        "createdAt": "2026-01-01T00:00:00.000Z",
        "lastMessageAt": "2026-01-01T00:00:00.000Z",
    }
    (directory / filename).write_text(json.dumps(data))


def _write_chatgpt_shard(
    directory: Path,
    filename: str,
    conversations: list[dict[str, object]],
) -> None:
    (directory / filename).write_text(json.dumps(conversations))


def _chatgpt_conversation(
    conv_id: str,
    title: str,
    user_parts: list[str] | None = None,
) -> dict[str, object]:
    if user_parts is None:
        user_parts = ["I like hiking"]
    return {
        "id": conv_id,
        "title": title,
        "create_time": 1767236400.0,
        "update_time": 1767237000.0,
        "current_node": "node-assistant",
        "mapping": {
            "node-root": {"message": None, "parent": None},
            "node-user": {
                "message": {
                    "id": f"message-{conv_id}-1",
                    "author": {"role": "user"},
                    "create_time": 1767236400.0,
                    "content": {
                        "content_type": "text",
                        "parts": user_parts,
                    },
                },
                "parent": "node-root",
            },
            "node-assistant": {
                "message": {
                    "id": f"message-{conv_id}-2",
                    "author": {"role": "assistant"},
                    "create_time": 1767237000.0,
                    "content": {"content_type": "text", "parts": ["Fine!"]},
                },
                "parent": "node-user",
            },
        },
    }


class TestValidation:
    def test_rejects_bad_source(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError):
            run_corpus_audit("email", tmp_path)

    def test_rejects_missing_directory(self, tmp_path: Path) -> None:
        with pytest.raises(NotADirectoryError):
            run_corpus_audit("gemini", tmp_path / "nope")

    def test_rejects_zero_limit(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError):
            run_corpus_audit("gemini", tmp_path, limit=0)

    def test_rejects_limit_above_cap(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError):
            run_corpus_audit("gemini", tmp_path, limit=MAX_CONVERSATIONS_PER_SOURCE + 1)

    def test_rejects_zero_max_messages(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError):
            run_corpus_audit("gemini", tmp_path, max_messages=0)

    def test_empty_export_is_valid_noop(self, tmp_path: Path) -> None:
        report = run_corpus_audit("gemini", tmp_path)
        assert report.conversations_sampled == 0
        assert report.user_messages == 0
        assert report.extraction.candidates == 0
        assert report.model_calls == 0


class TestGeminiSamplingAndAccounting:
    def setup_method(self) -> None:
        self.prep = tempfile.mkdtemp(prefix="corpus-audit-")

    def teardown_method(self) -> None:
        import shutil

        shutil.rmtree(self.prep, ignore_errors=True)

    def _multi_export(self, tmp_path: Path, count: int) -> Path:
        export = tmp_path / "gemini"
        export.mkdir()
        for i in range(count):
            _write_gemini_conversation(
                export,
                f"2026010{i + 1:02d}_Chat_{i:03d}.json",
                conv_id=f"conv-{i}",
                messages=[
                    {"role": "user", "content": "I like hiking"},
                    {"role": "assistant", "content": "Nice!"},
                ],
            )
        return export

    def test_bounded_sample_limit(self, tmp_path: Path) -> None:
        export = self._multi_export(tmp_path, 30)
        report = run_corpus_audit("gemini", export, limit=5)
        assert report.conversations_available == 30
        assert report.conversations_sampled == 5
        assert report.messages_sampled == 10

    def test_limit_at_max_samples_full_cap(self, tmp_path: Path) -> None:
        export = self._multi_export(tmp_path, 40)
        report = run_corpus_audit("gemini", export, limit=MAX_CONVERSATIONS_PER_SOURCE)
        assert report.conversations_sampled == MAX_CONVERSATIONS_PER_SOURCE

    def test_deterministic_sampling(self, tmp_path: Path) -> None:
        export = self._multi_export(tmp_path, 10)
        first = run_corpus_audit("gemini", export, limit=3)
        second = run_corpus_audit("gemini", export, limit=3)
        assert first.summary() == second.summary()

    def test_messages_capped_per_conversation(self, tmp_path: Path) -> None:
        export = tmp_path / "gemini"
        export.mkdir()
        long_messages = [{"role": "user", "content": "I like hiking"}] * 20
        _write_gemini_conversation(
            export, "20260101_Chat_000.json", messages=long_messages
        )
        report = run_corpus_audit("gemini", export, max_messages=3)
        assert report.conversations_sampled == 1
        assert report.messages_sampled == 3

    def test_language_distribution(self, tmp_path: Path) -> None:
        export = tmp_path / "gemini"
        export.mkdir()
        _write_gemini_conversation(
            export,
            "20260101_En_000.json",
            messages=[{"role": "user", "content": "I like hiking"}],
        )
        _write_gemini_conversation(
            export,
            "20260102_De_001.json",
            messages=[{"role": "user", "content": "Ich mag Wandern."}],
        )
        _write_gemini_conversation(
            export,
            "20260103_Es_002.json",
            messages=[{"role": "user", "content": "A mí me gusta el fútbol."}],
        )
        report = run_corpus_audit("gemini", export, limit=3)
        langs = report.languages.languages
        assert langs.get("en", 0) >= 1
        assert langs.get("de", 0) >= 1
        assert langs.get("es", 0) >= 1

    def test_candidates_and_extraction_accounting(self, tmp_path: Path) -> None:
        export = tmp_path / "gemini"
        export.mkdir()
        _write_gemini_conversation(
            export,
            "20260101_En_000.json",
            messages=[
                {"role": "user", "content": "I like hiking in the alps."},
                {"role": "assistant", "content": "Me too."},
                {"role": "user", "content": "Ich möchte nach Spanien ziehen."},
            ],
        )
        report = run_corpus_audit("gemini", export, limit=1)
        assert report.extraction.candidates >= 2
        assert report.extraction.by_kind.get("interest", 0) >= 1
        assert report.extraction.by_kind.get("goal", 0) >= 1
        assert report.extraction.by_language.get("en", 0) >= 1
        assert report.extraction.by_language.get("de", 0) >= 1
        assert report.skip_reasons.get("non_user_role", 0) >= 1
        assert report.model_calls == 0
        assert report.llm_proposal_layer == "not_exercised"

    def test_policy_security_accounting(self, tmp_path: Path) -> None:
        export = tmp_path / "gemini"
        export.mkdir()
        _write_gemini_conversation(
            export,
            "20260101_En_000.json",
            messages=[{"role": "user", "content": "I live in Berlin and I like jazz."}],
        )
        report = run_corpus_audit("gemini", export, limit=1)
        assert report.security.decisions.get("accept", 0) >= 1
        assert report.security.sensitivity.get("ordinary", 0) >= 1
        assert report.security.secret_rejected == 0
        assert "policy_decisions" in report.summary()

    def test_secret_content_never_surfaces_as_candidate(self, tmp_path: Path) -> None:
        export = tmp_path / "gemini"
        export.mkdir()
        _write_gemini_conversation(
            export,
            "20260101_En_000.json",
            messages=[
                {
                    "role": "user",
                    "content": "My access token is sk_live_abcdefghijklmnopq123456",
                },
                {"role": "user", "content": "I like hiking."},
            ],
        )
        report = run_corpus_audit("gemini", export, limit=1)
        # The secret-form message produces no candidate; the safe one survives.
        assert report.extraction.candidates == 1
        # Skip reasons attest the secret was filtered at the extractor boundary.
        assert report.skip_reasons.get("sensitive_form", 0) >= 1
        assert report.security.secret_rejected == 0

    def test_unicode_metrics(self, tmp_path: Path) -> None:
        export = tmp_path / "gemini"
        export.mkdir()
        _write_gemini_conversation(
            export,
            "20260101_De_000.json",
            messages=[{"role": "user", "content": "Ich wohne in München und Berlin."}],
        )
        report = run_corpus_audit("gemini", export, limit=1)
        assert report.unicode.non_ascii_messages >= 1
        assert report.unicode.messages_with_umlauts_or_accents >= 1
        assert report.unicode.tokens_total >= 1
        assert report.unicode.unicode_tokens >= 1
        assert report.unicode.zero_token_messages == 0

    def test_phase21a_es_verb_measurement(self, tmp_path: Path) -> None:
        export = tmp_path / "gemini"
        export.mkdir()
        _write_gemini_conversation(
            export,
            "20260101_Es_000.json",
            messages=[{"role": "user", "content": "Hago ejercicio en el gimnasio."}],
        )
        report = run_corpus_audit("gemini", export, limit=1)
        # Today these ES first-person verbs are not in the deterministic trigger
        # set, so the sentence is detected UNKNOWN (the Phase 21A gap).
        assert report.phase21a.verb_sentences >= 1
        assert report.phase21a.detected_es == 0
        assert report.phase21a.language_unknown >= 1

    def test_provenance_all_valid(self, tmp_path: Path) -> None:
        export = tmp_path / "gemini"
        export.mkdir()
        _write_gemini_conversation(
            export,
            "20260101_En_000.json",
            messages=[{"role": "user", "content": "I like hiking."}],
        )
        report = run_corpus_audit("gemini", export, limit=1)
        assert report.provenance.evidence_valid >= 1
        assert report.provenance.evidence_invalid == 0

    def test_summary_never_contains_content_or_identifiers(
        self, tmp_path: Path
    ) -> None:
        export = tmp_path / "gemini"
        export.mkdir()
        _write_gemini_conversation(
            export,
            "20260101_En_000.json",
            messages=[
                {
                    "role": "user",
                    "content": "My private secret is that I love para-gliding.",
                }
            ],
        )
        report = run_corpus_audit("gemini", export, limit=1)
        dumped = json.dumps(report.summary())
        for sentinel in (
            "private secret",
            "para-gliding",
            "conv-",
            "message-",
            "20260101",
        ):
            assert sentinel not in dumped


class TestChatgptSampling:
    def test_bounded_sample_and_accounting(self, tmp_path: Path) -> None:
        export = tmp_path / "chatgpt"
        export.mkdir()
        conversations = [_chatgpt_conversation(f"c{i}", f"Title {i}") for i in range(8)]
        _write_chatgpt_shard(export, "conversations-000.json", conversations)
        report = run_corpus_audit("chatgpt", export, limit=3)
        assert report.conversations_available == 8
        assert report.conversations_sampled == 3
        assert report.messages_sampled == 6
        assert report.extraction.candidates >= 3
        assert report.languages.languages.get("en", 0) >= 1
        assert report.provenance.evidence_valid >= 1

    def test_multiple_shards_sorted(self, tmp_path: Path) -> None:
        export = tmp_path / "chatgpt"
        export.mkdir()
        _write_chatgpt_shard(
            export,
            "conversations-000.json",
            [_chatgpt_conversation("a", "First")],
        )
        _write_chatgpt_shard(
            export,
            "conversations-001.json",
            [_chatgpt_conversation("b", "Second")],
        )
        report = run_corpus_audit("chatgpt", export, limit=2)
        assert report.conversations_available == 2
        assert report.conversations_sampled == 2


class TestIdempotencyScratchDb:
    def test_two_passes_produce_zero_growth(self, tmp_path: Path) -> None:
        export = tmp_path / "gemini"
        export.mkdir()
        _write_gemini_conversation(
            export,
            "20260101_En_000.json",
            messages=[
                {
                    "role": "user",
                    "content": "I like hiking and I want to move to Spain.",
                }
            ],
        )
        scratch = tmp_path / "scratch.db"
        report = run_corpus_audit("gemini", export, limit=1, scratch_database=scratch)
        assert report.idempotency is not None
        assert report.idempotency.conversations_seeded == 1
        assert report.idempotency.messages_seeded == 1
        assert report.idempotency.memories_pass1 == report.idempotency.memories_pass2
        assert report.idempotency.evidence_pass1 == report.idempotency.evidence_pass2
        assert report.idempotency.idempotent
        assert report.idempotency.memories_growth == 0

    def test_scratch_db_never_leaves_tables_behind_in_production(
        self, tmp_path: Path
    ) -> None:
        export = tmp_path / "gemini"
        export.mkdir()
        _write_gemini_conversation(export, "20260101_En_000.json")
        # The scratch DB must be a usable sqlite file, not the production DB.
        scratch = tmp_path / "scratch.db"
        run_corpus_audit("gemini", export, limit=1, scratch_database=scratch)
        connection = connect_database(scratch)
        try:
            tables = {
                row[0]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
        finally:
            connection.close()
        assert "conversations" in tables
        assert "conversation_messages" in tables

    def test_no_scratch_db_means_no_idempotency_metadata(self, tmp_path: Path) -> None:
        export = tmp_path / "gemini"
        export.mkdir()
        _write_gemini_conversation(export, "20260101_En_000.json")
        report = run_corpus_audit("gemini", export, limit=1, scratch_database=None)
        assert report.idempotency is None
        assert report.summary()["idempotency"] is None

    def test_clean_corpus_survives_rerun(self, tmp_path: Path) -> None:
        export = tmp_path / "gemini"
        export.mkdir()
        _write_gemini_conversation(
            export,
            "20260101_En_000.json",
            messages=[{"role": "user", "content": "I like hiking."}],
        )
        scratch = tmp_path / "scratch.db"
        first = run_corpus_audit("gemini", export, limit=1, scratch_database=scratch)
        second = run_corpus_audit("gemini", export, limit=1, scratch_database=scratch)
        assert first.summary() == second.summary()


class TestReportIsAggregate:
    def test_summary_is_count_only_and_json_serializable(self, tmp_path: Path) -> None:
        export = tmp_path / "gemini"
        export.mkdir()
        _write_gemini_conversation(
            export,
            "20260101_En_000.json",
            messages=[{"role": "user", "content": "I like hiking."}],
        )
        report = run_corpus_audit("gemini", export, limit=1)
        summary = report.summary()
        json.dumps(summary)  # must be fully serializable
        assert isinstance(summary, dict)
        assert summary["model_calls"] == 0


class TestNoFilesystemSideEffects:
    def test_audit_without_scratch_writes_nothing(self, tmp_path: Path) -> None:
        export = tmp_path / "gemini"
        export.mkdir()
        _write_gemini_conversation(export, "20260101_En_000.json")
        before = set(tmp_path.iterdir())
        run_corpus_audit("gemini", export, limit=1, scratch_database=None)
        after = set(tmp_path.iterdir())
        assert before == after
