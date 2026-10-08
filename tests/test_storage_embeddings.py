"""Integration tests for the SQLite-backed embedding store."""

import sqlite3
from pathlib import Path

import pytest

import personal_ai.storage.embeddings as embeddings_module
from personal_ai.documents import Embedding, MalformedEmbeddingError, parse_embedding
from personal_ai.storage import EmbeddingStore, connect_database


def make_embedding(**overrides: object) -> Embedding:
    values: dict[str, object] = {
        "model": "nomic-embed-text",
        "vector": (0.25, -0.5, 1.0),
    }
    values.update(overrides)
    return Embedding(**values)  # type: ignore[arg-type]


def make_provider_embedding() -> Embedding:
    """Build an embedding through the real production validation path."""
    return parse_embedding("nomic-embed-text", [[0.1, -0.2, 0.3]])


def make_store(path: Path | str = ":memory:") -> EmbeddingStore:
    return EmbeddingStore(connect_database(path))


def test_add_then_get_round_trips_real_domain_embedding() -> None:
    embedding = make_provider_embedding()

    with make_store() as store:
        assert store.add(embedding, "chunk-1") is True
        assert store.get("chunk-1") == embedding


def test_complete_fields_survive_the_round_trip() -> None:
    embedding = make_embedding(model="mxbai-embed-large", vector=(1.5, -2.5))

    with make_store() as store:
        store.add(embedding, "chunk-1")

        stored = store.get("chunk-1")

    assert stored is not None
    assert stored.model == "mxbai-embed-large"
    assert stored.vector == (1.5, -2.5)
    assert stored.dimensions == 2 == len(stored.vector)


def test_get_unknown_chunk_id_returns_none() -> None:
    with make_store() as store:
        assert store.get("missing") is None


def test_add_returns_true_on_insert_and_false_on_resave() -> None:
    embedding = make_embedding()

    with make_store() as store:
        assert store.add(embedding, "chunk-1") is True
        assert store.add(embedding, "chunk-1") is False


def test_resaving_updates_the_stored_vector() -> None:
    original = make_embedding()
    updated = make_embedding(vector=(9.0, 8.0, 7.0))

    with make_store() as store:
        assert store.add(original, "chunk-1") is True
        assert store.add(updated, "chunk-1") is False

        assert store.get("chunk-1") == updated


def test_model_is_preserved_and_can_change_on_update() -> None:
    first = make_embedding(model="nomic-embed-text")
    second = make_embedding(model="mxbai-embed-large", vector=(4.0, 5.0, 6.0))

    with make_store() as store:
        store.add(first, "chunk-1")
        store.add(second, "chunk-1")

        stored = store.get("chunk-1")

    assert stored == second
    assert stored is not None and stored.model == "mxbai-embed-large"


def test_distinct_chunks_keep_distinct_embeddings() -> None:
    first = make_embedding()
    second = make_embedding(vector=(3.0, 2.0, 1.0))

    with make_store() as store:
        assert store.add(first, "chunk-a") is True
        assert store.add(second, "chunk-b") is True

        assert store.get("chunk-a") == first
        assert store.get("chunk-b") == second


@pytest.mark.parametrize("vector", [(), ("not a number",), (True, 0.5)])
def test_invalid_vectors_are_rejected_before_storage(
    vector: tuple[object, ...],
) -> None:
    embedding = make_embedding(vector=vector)  # type: ignore[arg-type]

    with make_store() as store:
        with pytest.raises(MalformedEmbeddingError):
            store.add(embedding, "chunk-1")

        assert store.get("chunk-1") is None


def test_nan_and_infinity_are_rejected() -> None:
    with make_store() as store:
        with pytest.raises(MalformedEmbeddingError):
            store.add(make_embedding(vector=(float("nan"),)), "chunk-nan")

        with pytest.raises(MalformedEmbeddingError):
            store.add(make_embedding(vector=(float("inf"),)), "chunk-inf")


def test_delete_removes_row_and_reports_whether_it_existed() -> None:
    with make_store() as store:
        store.add(make_embedding(), "chunk-1")

        assert store.delete("chunk-1") is True
        assert store.delete("chunk-1") is False
        assert store.get("chunk-1") is None


def test_embeddings_persist_across_connections(tmp_path: Path) -> None:
    database = tmp_path / "personal.db"

    with make_store(database) as store:
        store.add(make_embedding(), "chunk-1")

    with make_store(database) as store:
        assert store.get("chunk-1") == make_embedding()


def test_context_manager_closes_connection() -> None:
    connection = connect_database(":memory:")

    with EmbeddingStore(connection) as store:
        store.add(make_embedding(), "chunk-1")

    with pytest.raises(sqlite3.ProgrammingError):
        store.get("chunk-1")


def test_list_for_chunks_returns_stored_embeddings_in_request_order() -> None:
    with make_store() as store:
        first = make_embedding(vector=(1.0, 2.0, 3.0))
        second = make_embedding(vector=(4.0, 5.0, 6.0))
        third = make_embedding(model="mxbai-embed-large", vector=(7.0, 8.0))
        store.add(first, "chunk-a")
        store.add(second, "chunk-b")
        store.add(third, "chunk-c")

        result = store.list_for_chunks(["chunk-c", "chunk-a", "chunk-missing"])

    assert list(result) == ["chunk-c", "chunk-a"]
    assert result["chunk-a"] == first
    assert result["chunk-c"] == third
    assert "chunk-missing" not in result


def test_list_for_chunks_empty_input_returns_empty_dict() -> None:
    with make_store() as store:
        store.add(make_embedding(), "chunk-1")

        assert store.list_for_chunks([]) == {}


def test_list_for_chunks_accepts_a_generator_of_ids() -> None:
    with make_store() as store:
        store.add(make_embedding(), "chunk-a")
        store.add(make_embedding(), "chunk-b")

        result = store.list_for_chunks(f"chunk-{index}" for index in ("a", "b"))

    assert set(result) == {"chunk-a", "chunk-b"}


def test_list_for_chunks_raises_on_corrupt_stored_json() -> None:
    connection = connect_database(":memory:")
    store = EmbeddingStore(connection)
    write_raw_embedding_row(
        connection, "chunk-1", vector_json="{not json", dimensions=2
    )

    with pytest.raises(ValueError, match="invalid JSON"):
        store.list_for_chunks(["chunk-1"])


def write_raw_embedding_row(
    connection: sqlite3.Connection,
    chunk_id: str,
    *,
    vector_json: str,
    dimensions: int,
) -> None:
    connection.execute(
        "INSERT OR REPLACE INTO chunk_embeddings "
        "(chunk_id, model, vector, dimensions) VALUES (?, ?, ?, ?)",
        (chunk_id, "nomic-embed-text", vector_json, dimensions),
    )
    connection.commit()


def test_corrupt_stored_json_raises_an_explicit_error() -> None:
    connection = connect_database(":memory:")
    store = EmbeddingStore(connection)
    write_raw_embedding_row(
        connection, "chunk-1", vector_json="{not json", dimensions=2
    )

    with pytest.raises(ValueError, match="invalid JSON"):
        store.get("chunk-1")


def test_dimensions_mismatch_raises_an_explicit_error() -> None:
    connection = connect_database(":memory:")
    store = EmbeddingStore(connection)
    write_raw_embedding_row(
        connection, "chunk-1", vector_json="[0.5, 0.25]", dimensions=7
    )

    with pytest.raises(ValueError, match="dimensions"):
        store.get("chunk-1")


def test_error_messages_do_not_contain_vector_values() -> None:
    connection = connect_database(":memory:")
    store = EmbeddingStore(connection)
    write_raw_embedding_row(
        connection,
        "chunk-1",
        vector_json='[0.123456789, "s3cret-component"]',
        dimensions=2,
    )

    with pytest.raises(ValueError) as exc_info:
        store.get("chunk-1")

    message = str(exc_info.value)
    assert "s3cret-component" not in message
    assert "0.123456789" not in message


def test_write_rejection_messages_do_not_contain_vector_values() -> None:
    embedding = make_embedding(vector=(0.123456789, object()))

    with make_store() as store, pytest.raises(MalformedEmbeddingError) as exc_info:
        store.add(embedding, "chunk-1")

    assert "0.123456789" not in str(exc_info.value)


def test_storage_module_has_no_infrastructure_imports() -> None:
    source = Path(embeddings_module.__file__).read_text(encoding="utf-8").lower()

    assert "ollama" not in source
    assert "httpx" not in source


def test_count_tracks_rows_per_model() -> None:
    with make_store() as store:
        assert store.count("nomic-embed-text") == 0
        store.add(make_embedding(model="nomic-embed-text"), "chunk-a")
        store.add(make_embedding(model="nomic-embed-text"), "chunk-b")
        store.add(make_embedding(model="mxbai-embed-large"), "chunk-c")

        assert store.count("nomic-embed-text") == 2
        assert store.count("mxbai-embed-large") == 1
        assert store.count("another-model") == 0


def test_count_follows_update_across_models() -> None:
    with make_store() as store:
        store.add(make_embedding(model="old-model"), "chunk-1")
        assert store.count("old-model") == 1

        store.add(make_embedding(model="new-model"), "chunk-1")
        assert store.count("old-model") == 0
        assert store.count("new-model") == 1


def test_add_many_inserts_and_updates_in_one_transaction() -> None:
    with make_store() as store:
        store.add(make_embedding(), "chunk-a")
        items = [
            (make_embedding(vector=(1.0, 1.0, 1.0)), "chunk-a"),
            (make_embedding(vector=(2.0, 2.0, 2.0)), "chunk-b"),
            (make_embedding(vector=(3.0, 3.0, 3.0)), "chunk-c"),
        ]

        inserted = store.add_many(items)

        assert inserted == 2
        assert store.get("chunk-b") == make_embedding(vector=(2.0, 2.0, 2.0))
        assert store.get("chunk-c") == make_embedding(vector=(3.0, 3.0, 3.0))


def test_add_many_rejects_invalid_embedding_whole_batch() -> None:
    with make_store() as store:
        items = [
            (make_embedding(), "chunk-a"),
            (make_embedding(vector=(True,)), "chunk-b"),
        ]

        with pytest.raises(MalformedEmbeddingError):
            store.add_many(items)

        assert store.get("chunk-a") is None
        assert store.get("chunk-b") is None


def test_add_many_empty_batch_is_a_noop() -> None:
    with make_store() as store:
        assert store.add_many([]) == 0
        assert store.count("nomic-embed-text") == 0
