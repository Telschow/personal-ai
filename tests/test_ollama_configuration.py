"""Tests for Ollama endpoint configuration (Docker-ready base URL)."""

from pathlib import Path
from typing import Self

import pytest

from personal_ai.cli import build_agent
from personal_ai.config import (
    DEFAULT_OLLAMA_BASE_URL,
    OLLAMA_BASE_URL_ENV,
    OllamaSettings,
    load_ollama_settings,
)


class TestOllamaSettings:
    def test_defaults_to_local_ollama_daemon(self) -> None:
        assert load_ollama_settings({}).base_url == "http://localhost:11434"
        assert DEFAULT_OLLAMA_BASE_URL == "http://localhost:11434"

    def test_configured_endpoint_is_read_correctly(self) -> None:
        settings = load_ollama_settings({OLLAMA_BASE_URL_ENV: "http://ollama:11434"})
        assert settings.base_url == "http://ollama:11434"

    def test_blank_configuration_is_treated_as_unset(self) -> None:
        assert load_ollama_settings({OLLAMA_BASE_URL_ENV: "   "}).base_url == (
            DEFAULT_OLLAMA_BASE_URL
        )

    def test_real_environment_is_used_by_default(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(OLLAMA_BASE_URL_ENV, "http://ollama:11434")
        assert load_ollama_settings().base_url == "http://ollama:11434"
        monkeypatch.delenv(OLLAMA_BASE_URL_ENV)
        assert load_ollama_settings().base_url == DEFAULT_OLLAMA_BASE_URL

    def test_settings_value_object_remains_a_plain_dataclass(self) -> None:
        assert OllamaSettings(base_url="http://x").base_url == "http://x"


class TestBuildAgentOllamaBaseUrl:
    def test_build_agent_honors_ollama_base_url_env(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        received: list[str] = []

        class _StubClient:
            def __init__(self, model: str, base_url: str) -> None:
                self.model = model
                self.base_url = base_url

            def __enter__(self) -> Self:
                received.append(self.base_url)
                return self

            def __exit__(self, *exc: object) -> None:
                pass

        monkeypatch.setattr("personal_ai.cli.OllamaClient", _StubClient)
        monkeypatch.setenv(OLLAMA_BASE_URL_ENV, "http://ollama:11434")
        built = build_agent(tmp_path)
        assert received == ["http://ollama:11434"]
        assert built.client.base_url == "http://ollama:11434"

    def test_build_agent_keeps_local_default_without_env(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        received: list[str] = []

        class _StubClient:
            def __init__(self, model: str, base_url: str) -> None:
                self.base_url = base_url

            def __enter__(self) -> Self:
                received.append(self.base_url)
                return self

            def __exit__(self, *exc: object) -> None:
                pass

        monkeypatch.setattr("personal_ai.cli.OllamaClient", _StubClient)
        monkeypatch.delenv(OLLAMA_BASE_URL_ENV, raising=False)
        built = build_agent(tmp_path)
        assert received == [DEFAULT_OLLAMA_BASE_URL]
        assert built.client.base_url == DEFAULT_OLLAMA_BASE_URL
