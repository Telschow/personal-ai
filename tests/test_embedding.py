"""Unit tests for the provider-independent embedding contract."""

from pathlib import Path

import pytest

import personal_ai.documents.embedding as embedding_module
from personal_ai.documents import (
    Embedding,
    EmbeddingProvider,
    MalformedEmbeddingError,
    parse_embedding,
)


class FakeEmbeddingProvider:
    """Deterministic in-memory provider satisfying the contract."""

    def __init__(
        self,
        vector: tuple[float, ...] = (0.25, -0.5, 1.0),
        model: str = "fake-model",
    ) -> None:
        self._vector = vector
        self.model = model
        self.calls: list[str] = []

    def embed(self, text: str) -> Embedding:
        self.calls.append(text)
        return Embedding(model=self.model, vector=self._vector)


def test_fake_provider_satisfies_the_embedding_protocol() -> None:
    assert isinstance(FakeEmbeddingProvider(), EmbeddingProvider)


def test_embed_returns_typed_deterministic_result() -> None:
    provider = FakeEmbeddingProvider()

    first = provider.embed("some text")
    second = provider.embed("some text")

    assert isinstance(first, Embedding)
    assert first == second
    assert first.model == "fake-model"
    assert first.vector == (0.25, -0.5, 1.0)
    assert provider.calls == ["some text", "some text"]


def test_dimensions_agree_with_vector_length() -> None:
    embedding = FakeEmbeddingProvider(vector=(0.5, 0.5, 0.5, 0.5)).embed("text")

    assert embedding.dimensions == 4
    assert embedding.dimensions == len(embedding.vector)


def test_parse_embedding_builds_typed_result() -> None:
    embedding = parse_embedding("nomic-embed-text", [[1.5, -2.5]])

    assert embedding == Embedding(model="nomic-embed-text", vector=(1.5, -2.5))
    assert embedding.dimensions == 2


@pytest.mark.parametrize(
    "vectors",
    [
        "not a list",
        [],
        [[0.1], [0.2]],
        [{}],
        ["0.1"],
        [None],
        [[True, 0.5]],
        [["x", 0.5]],
        [[]],
        [[float("nan")]],
        [[float("inf"), 0.5]],
        [[0.5, float("-inf")]],
    ],
)
def test_malformed_vectors_fail_explicitly(vectors: object) -> None:
    with pytest.raises(MalformedEmbeddingError):
        parse_embedding("test-model", vectors)


def test_error_messages_do_not_contain_component_values() -> None:
    with pytest.raises(MalformedEmbeddingError) as exc_info:
        parse_embedding("test-model", [[0.123456789, "s3cret-value"]])

    assert "s3cret-value" not in str(exc_info.value)
    assert "0.123456789" not in str(exc_info.value)


def test_domain_module_has_no_infrastructure_imports() -> None:
    source = Path(embedding_module.__file__).read_text(encoding="utf-8")

    assert "ollama" not in source.lower()
    assert "httpx" not in source


def test_documents_package_exports_stay_infrastructure_free() -> None:
    package_init = Path(embedding_module.__file__).with_name("__init__.py")
    source = package_init.read_text(encoding="utf-8").lower()

    assert "ollama" not in source
    assert "httpx" not in source
