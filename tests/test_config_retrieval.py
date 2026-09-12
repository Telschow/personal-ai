"""Tests for retrieval-backend configuration (Phase 51)."""

import pytest

from personal_ai.config import (
    RETRIEVAL_MODE_ENV,
    RetrievalMode,
    RetrievalSettings,
    load_retrieval_settings,
)

SEMANTIC = "semantic-test-model"


class TestRetrievalSettings:
    def test_absent_environment_defaults_to_keyword(self) -> None:
        assert load_retrieval_settings({}).mode is RetrievalMode.KEYWORD

    def test_blank_configuration_is_treated_as_default_keyword(self) -> None:
        assert (
            load_retrieval_settings({RETRIEVAL_MODE_ENV: "   "}).mode
            is RetrievalMode.KEYWORD
        )

    @pytest.mark.parametrize(
        "raw, expected",
        [
            ("keyword", RetrievalMode.KEYWORD),
            ("semantic", RetrievalMode.SEMANTIC),
            ("hybrid", RetrievalMode.HYBRID),
            ("KEYWORD", RetrievalMode.KEYWORD),
            ("Semantic", RetrievalMode.SEMANTIC),
        ],
    )
    def test_valid_values_are_read_case_insensitively(
        self, raw: str, expected: RetrievalMode
    ) -> None:
        assert load_retrieval_settings({RETRIEVAL_MODE_ENV: raw}).mode is expected

    def test_unknown_value_raises_and_lists_valid_modes(self) -> None:
        with pytest.raises(ValueError) as exc_info:
            load_retrieval_settings({RETRIEVAL_MODE_ENV: "vector"})
        message = str(exc_info.value)
        assert RETRIEVAL_MODE_ENV in message
        assert "keyword" in message
        assert "semantic" in message
        assert "hybrid" in message

    def test_embedding_model_env_never_changes_mode(self) -> None:
        settings = load_retrieval_settings({"PERSONAL_AI_EMBEDDING_MODEL": SEMANTIC})
        assert settings.mode is RetrievalMode.KEYWORD

    def test_real_environment_is_used_by_default(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(RETRIEVAL_MODE_ENV, "hybrid")
        assert load_retrieval_settings().mode is RetrievalMode.HYBRID
        monkeypatch.delenv(RETRIEVAL_MODE_ENV)
        assert load_retrieval_settings().mode is RetrievalMode.KEYWORD

    def test_settings_value_object_defaults_to_keyword(self) -> None:
        assert RetrievalSettings().mode is RetrievalMode.KEYWORD
