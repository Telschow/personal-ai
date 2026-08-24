"""Integration tests for FTS5 keyword search over stored chunks."""

from personal_ai.documents import DocumentChunk
from personal_ai.storage import ChunkSearchResult, ChunkStore, connect_database


def make_chunk(**overrides: object) -> DocumentChunk:
    values: dict[str, object] = {
        "id": "chunk-1",
        "document_id": "doc-1",
        "text": "first searchable passage",
        "metadata": {"chunk_index": 0},
    }
    values.update(overrides)
    return DocumentChunk(**values)  # type: ignore[arg-type]


def make_store() -> ChunkStore:
    return ChunkStore(connect_database(":memory:"))


def test_store_creates_the_derived_fts_schema() -> None:
    connection = connect_database(":memory:")

    with ChunkStore(connection):
        tables = {
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE name LIKE 'document_chunks%'"
            ).fetchall()
        }

    assert "document_chunks" in tables
    assert "document_chunks_fts" in tables


def test_inserted_chunk_becomes_searchable() -> None:
    with make_store() as store:
        store.add(make_chunk(text="quarterly budget review notes"))

        hits = store.search("budget")

    assert len(hits) == 1
    assert hits[0].chunk_id == "chunk-1"


def test_search_returns_authoritative_metadata_and_text() -> None:
    chunk = make_chunk(
        text="quarterly budget review notes",
        document_id="doc-77",
        metadata={"chunk_index": 4},
    )

    with make_store() as store:
        store.add(chunk)

        hit = store.search("budget")[0]

    assert isinstance(hit, ChunkSearchResult)
    assert hit.chunk_id == "chunk-1"
    assert hit.document_id == "doc-77"
    assert hit.chunk_index == 4
    assert hit.text == "quarterly budget review notes"
    assert isinstance(hit.rank, float)


def test_search_exposes_null_index_for_unindexed_chunks() -> None:
    unindexed = make_chunk(text="unnumbered passage", metadata={})

    with make_store() as store:
        store.add(unindexed)

        assert store.search("passage")[0].chunk_index is None


def test_multiple_matching_chunks_are_all_returned() -> None:
    chunks = (
        make_chunk(id="chunk-a", text="kite design notes"),
        make_chunk(id="chunk-b", text="kite safety checklist"),
    )

    with make_store() as store:
        store.add_many(chunks)

        hits = store.search("kite")

    assert [hit.chunk_id for hit in hits] == ["chunk-a", "chunk-b"]


def test_multi_term_query_requires_all_terms() -> None:
    matching = make_chunk(id="chunk-both", text="kite safety checklist")
    partial = make_chunk(
        id="chunk-partial", document_id="doc-2", text="kite design notes"
    )

    with make_store() as store:
        store.add_many((matching, partial))

        hits = store.search("kite safety")

    assert [hit.chunk_id for hit in hits] == ["chunk-both"]


def test_no_match_returns_empty_tuple() -> None:
    with make_store() as store:
        store.add(make_chunk())

        assert store.search("zeppelin") == ()


def test_empty_and_whitespace_queries_return_no_results() -> None:
    with make_store() as store:
        store.add(make_chunk())

        assert store.search("") == ()
        assert store.search("   \t\n ") == ()


def test_ranking_orders_stronger_matches_first() -> None:
    frequent = make_chunk(id="chunk-frequent", text="flute flute flute practice")
    single = make_chunk(id="chunk-single", text="one flute mention")

    with make_store() as store:
        store.add_many((single, frequent))

        hits = store.search("flute")

    assert [hit.chunk_id for hit in hits] == ["chunk-frequent", "chunk-single"]
    assert hits[0].rank < hits[1].rank


def test_equal_ranks_order_deterministically_by_chunk_id() -> None:
    twin_first = make_chunk(id="chunk-z", text="identical body")
    twin_second = make_chunk(id="chunk-a", text="identical body")

    with make_store() as store:
        store.add_many((twin_first, twin_second))

        hits = store.search("body")
        again = store.search("body")

    assert [hit.chunk_id for hit in hits] == ["chunk-a", "chunk-z"]
    assert hits == again


def test_search_respects_explicit_limit() -> None:
    chunks = tuple(make_chunk(id=f"chunk-{n}", text=f"kite note {n}") for n in range(5))

    with make_store() as store:
        store.add_many(chunks)

        assert len(store.search("kite", limit=3)) == 3
        assert store.search("kite", limit=0) == ()


def test_negative_limit_is_rejected() -> None:
    with make_store() as store:
        try:
            store.search("kite", limit=-1)
        except ValueError:
            pass
        else:
            msg = "Negative limit must be rejected"
            raise AssertionError(msg)


def test_operator_words_are_literal_terms_not_boolean_syntax() -> None:
    flute = make_chunk(id="chunk-flute", text="flute practice log")
    trumpet = make_chunk(id="chunk-trumpet", text="trumpet practice log")

    with make_store() as store:
        store.add_many((flute, trumpet))

        # Raw FTS5 syntax would return both chunks via boolean OR; literal
        # keyword treatment requires one chunk containing every term.
        assert store.search("flute OR trumpet") == ()
        assert [hit.chunk_id for hit in store.search("flute")] == ["chunk-flute"]


def test_punctuation_heavy_query_stays_a_literal_keyword_query() -> None:
    chunk = make_chunk(id="chunk-cpp", text="don't panic about the C++ quotes")

    with make_store() as store:
        store.add(chunk)

        hits = store.search('don\'t (panic): "C++" ...')

    assert [hit.chunk_id for hit in hits] == ["chunk-cpp"]


def test_updating_chunk_text_changes_searchability() -> None:
    revised = make_chunk(text="revised retirement plan figures")

    with make_store() as store:
        store.add(make_chunk(text="original draft figures"))

        store.add(revised)

        assert store.search("original") == ()
        hits = store.search("retirement")

    assert [hit.chunk_id for hit in hits] == ["chunk-1"]
    assert hits[0].text == "revised retirement plan figures"


def test_repeated_insertion_does_not_duplicate_hits() -> None:
    chunk = make_chunk()

    with make_store() as store:
        assert store.add(chunk) is True
        assert store.add(chunk) is False

        hits = store.search("searchable")

    assert len(hits) == 1
    assert hits[0].chunk_id == "chunk-1"


def test_replaying_add_many_keeps_one_hit_per_chunk() -> None:
    chunks = (
        make_chunk(id="chunk-a", text="sailing trip log"),
        make_chunk(id="chunk-b", text="sailing gear inventory"),
    )

    with make_store() as store:
        store.add_many(chunks)
        store.add_many(chunks)

        hits = store.search("sailing")

    assert sorted(hit.chunk_id for hit in hits) == ["chunk-a", "chunk-b"]


def test_deleting_chunk_removes_it_from_search() -> None:
    survivor = make_chunk(
        id="chunk-survivor", document_id="doc-2", text="kept sailing log"
    )
    removed = make_chunk(id="chunk-removed", text="removed sailing log")

    with make_store() as store:
        assert store.add_many((survivor, removed)) == 2
        assert store.delete_for_document("doc-1") == 1

        hits = store.search("sailing")

    assert [hit.chunk_id for hit in hits] == ["chunk-survivor"]


def test_deleting_document_removes_all_of_its_hits() -> None:
    other_document = make_chunk(
        id="chunk-other", document_id="doc-2", text="unrelated hiking log"
    )
    gone_first = make_chunk(id="chunk-gone-a", text="hiking route plan")
    gone_second = make_chunk(id="chunk-gone-b", text="hiking packing list")

    with make_store() as store:
        store.add_many((other_document, gone_first, gone_second))

        store.delete_for_document("doc-1")

        hits = store.search("hiking")

    assert [hit.chunk_id for hit in hits] == ["chunk-other"]


def test_rebuild_restores_the_index_from_authoritative_chunks(tmp_path) -> None:
    database = tmp_path / "personal.db"
    chunk = make_chunk(text="restorable pottery kiln notes")

    writer = ChunkStore(connect_database(database))
    writer.add(chunk)
    writer.close()

    # Corrupt the derived state directly: the index is disposable.
    corrupting = connect_database(database)
    corrupting.execute("DELETE FROM document_chunks_fts")
    corrupting.commit()
    corrupting.close()

    store = ChunkStore(connect_database(database))
    try:
        assert store.search("pottery") == ()

        assert store.rebuild_search_index() == 1

        hits = store.search("pottery")
    finally:
        store.close()

    assert [hit.chunk_id for hit in hits] == ["chunk-1"]


def test_rebuilt_index_matches_incrementally_maintained_results(tmp_path) -> None:
    database = tmp_path / "personal.db"
    chunks = (
        make_chunk(id="chunk-a", text="guitar chord chart"),
        make_chunk(
            id="chunk-b",
            document_id="doc-2",
            text="guitar practice schedule",
            metadata={"chunk_index": 1},
        ),
    )

    incremental = ChunkStore(connect_database(database))
    incremental.add_many(chunks)
    incremental.close()

    rebuilt = ChunkStore(connect_database(database))
    rebuilt.rebuild_search_index()
    hits = rebuilt.search("guitar")
    rebuilt.close()

    assert [(hit.chunk_id, hit.document_id, hit.text) for hit in hits] == [
        ("chunk-a", "doc-1", "guitar chord chart"),
        ("chunk-b", "doc-2", "guitar practice schedule"),
    ]


def test_search_works_on_legacy_database_without_fts_table() -> None:
    connection = connect_database(":memory:")
    legacy = make_chunk(text="pre-existing handwritten archive entry")

    # Simulate a database written before the derived index existed by
    # creating only the authoritative schema through raw DDL.
    connection.execute(
        """
        CREATE TABLE document_chunks (
            chunk_id TEXT PRIMARY KEY,
            document_id TEXT NOT NULL,
            chunk_index INTEGER,
            page_number INTEGER,
            text TEXT NOT NULL,
            metadata TEXT NOT NULL
        )
        """
    )
    connection.execute(
        "INSERT INTO document_chunks VALUES (?, ?, ?, ?, ?, ?)",
        (
            legacy.id,
            legacy.document_id,
            0,
            None,
            legacy.text,
            '{"chunk_index": 0}',
        ),
    )
    connection.commit()

    store = ChunkStore(connection)
    try:
        assert store.search("handwritten") == ()
        assert store.rebuild_search_index() == 1
        assert [hit.chunk_id for hit in store.search("archive")] == ["chunk-1"]
    finally:
        store.close()


def test_add_many_failure_rolls_back_both_tables() -> None:
    unserializable = make_chunk(metadata={"blob": object()})

    with make_store() as store:
        store.add(make_chunk(id="chunk-keep", text="stable anchor phrase"))
        try:
            store.add_many(
                (
                    make_chunk(id="chunk-new", text="doomed transaction phrase"),
                    unserializable,
                )
            )
        except TypeError:
            pass

        assert store.search("doomed") == ()
        assert [hit.chunk_id for hit in store.search("anchor")] == ["chunk-keep"]
