"""Tests for embedding model configuration and backend readiness."""

import json
from collections.abc import Callable

import httpx
import pytest

from personal_ai.config import (
    EMBEDDING_MODEL_ENV,
    EmbeddingSettings,
    load_embedding_settings,
)
from personal_ai.ollama_client import OllamaConnectionError, OllamaHTTPStatusError
from personal_ai.ollama_embeddings import (
    PROBE_TEXT,
    EmbeddingModelNotConfiguredError,
    OllamaEmbedder,
    create_embedder,
    probe_embedding_backend,
)

CHAT_MODEL = "qwen3.5:9b"


class TestEmbeddingSettings:
    def test_configured_model_is_read_correctly(self) -> None:
        settings = load_embedding_settings({EMBEDDING_MODEL_ENV: "qwen3-embedding:4b"})
        assert settings.model == "qwen3-embedding:4b"

    def test_missing_configuration_stays_valid_but_unset(self) -> None:
        assert load_embedding_settings({}).model is None

    def test_blank_configuration_is_treated_as_unset(self) -> None:
        assert load_embedding_settings({EMBEDDING_MODEL_ENV: "   "}).model is None

    def test_chat_model_environment_does_not_leak_into_embeddings(self) -> None:
        environ = {
            "PERSONAL_AI_CHAT_MODEL": CHAT_MODEL,
            "OLLAMA_MODEL": CHAT_MODEL,
            "MODEL": CHAT_MODEL,
        }
        assert load_embedding_settings(environ).model is None

    def test_real_environment_is_used_by_default(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(EMBEDDING_MODEL_ENV, "nomic-embed-text")
        assert load_embedding_settings().model == "nomic-embed-text"
        monkeypatch.delenv(EMBEDDING_MODEL_ENV)
        assert load_embedding_settings().model is None

    def test_settings_value_object_remains_a_plain_dataclass(self) -> None:
        assert EmbeddingSettings(model=None).model is None


class TestEmbedderConstruction:
    def test_embedder_receives_the_configured_model(self) -> None:
        requests: list[httpx.Request] = []

        def record(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            return httpx.Response(200, json={"model": "m", "embeddings": [[0.1]]})

        settings = EmbeddingSettings(model="qwen3-embedding")
        embedder = create_embedder(settings, transport=httpx.MockTransport(record))

        assert isinstance(embedder, OllamaEmbedder)
        assert embedder.model == "qwen3-embedding"
        embedder.embed("hello")
        sent = json.loads(requests[0].content)
        assert sent["model"] == "qwen3-embedding"

    def test_missing_configuration_fails_clearly_without_defaults(self) -> None:
        with pytest.raises(EmbeddingModelNotConfiguredError) as exc_info:
            create_embedder(EmbeddingSettings(model=None))

        message = str(exc_info.value)
        assert EMBEDDING_MODEL_ENV in message
        assert CHAT_MODEL not in message
        assert "nomic" not in message.lower()

    def test_configuration_error_is_outside_the_ollama_taxonomy(self) -> None:
        assert not issubclass(EmbeddingModelNotConfiguredError, OllamaConnectionError)
        assert not issubclass(EmbeddingModelNotConfiguredError, OllamaHTTPStatusError)


class TestIngestionStaysEmbeddingFree:
    @pytest.mark.parametrize(
        "module_name",
        ["personal_ai.ingestion", "personal_ai.orchestration"],
    )
    def test_normal_ingestion_never_initializes_embedders(
        self, module_name: str
    ) -> None:
        import importlib
        from pathlib import Path

        module = importlib.import_module(module_name)
        source = Path(module.__file__).read_text(encoding="utf-8")
        for forbidden in ("OllamaClient", "OllamaEmbedder", "create_embedder", "httpx"):
            assert forbidden not in source, forbidden

    def test_document_domain_contract_remains_infrastructure_free(self) -> None:
        from pathlib import Path

        import personal_ai.documents.embedding as contract_module

        source = Path(contract_module.__file__).read_text(encoding="utf-8")
        for forbidden in ("httpx", "ollama", "OllamaClient"):
            assert forbidden not in source


class TestBackendProbe:
    def probe(self, handler: Callable[[httpx.Request], httpx.Response]) -> object:
        return probe_embedding_backend(
            "qwen3-embedding", transport=httpx.MockTransport(handler)
        )

    def test_ready_backend_reports_full_contract_satisfaction(self) -> None:
        report = self.probe(
            lambda request: httpx.Response(
                200, json={"model": "qwen3-embedding", "embeddings": [[0.1, 0.2, 0.3]]}
            )
        )

        assert report.reachable and report.supported and report.model_ready
        assert report.dimensions == 3
        assert report.detail == "ready"

    def test_unreachable_endpoint_reported_without_exception(self) -> None:
        def refused(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("connection refused")

        report = self.probe(refused)

        assert not report.reachable
        assert report.detail == "endpoint unreachable"

    def test_server_without_embeddings_support_is_classified(self) -> None:
        body = {"error": "This server does not support embeddings."}
        report = self.probe(lambda request: httpx.Response(400, json=body))

        assert report.reachable
        assert not report.supported
        assert report.model_ready is False
        assert "does not support embeddings" in report.detail

    def test_missing_model_reported_for_404(self) -> None:
        report = self.probe(
            lambda request: httpx.Response(
                404, json={"error": 'model "qwen3-embedding" not found'}
            )
        )

        assert report.reachable
        assert not report.supported
        assert "not available" in report.detail

    def test_malformed_success_response_violates_contract(self) -> None:
        report = self.probe(
            lambda request: httpx.Response(200, json={"unexpected": True})
        )

        assert report.reachable
        assert not report.model_ready

    def test_non_numeric_vector_components_violate_contract(self) -> None:
        report = self.probe(
            lambda request: httpx.Response(
                200, json={"model": "m", "embeddings": [["bad"]]}
            )
        )

        assert report.reachable
        assert not report.model_ready

    def test_probe_text_and_errors_never_appear_in_reports(self) -> None:
        reports = [
            self.probe(
                lambda request: httpx.Response(
                    200, json={"model": "m", "embeddings": [[0.1]]}
                )
            ),
            self.probe(lambda request: httpx.Response(500, text="boom")),
        ]
        for report in reports:
            assert PROBE_TEXT not in report.detail
