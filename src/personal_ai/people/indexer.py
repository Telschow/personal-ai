"""Bounded, deterministic indexing of source records into the people layer."""

from __future__ import annotations

from personal_ai.people.extract import extract_person_references
from personal_ai.people.models import PersonIndexReport
from personal_ai.people.store import PersonStore
from personal_ai.sources.models import SourceRecord

_PAGE = 200


class PersonIndexer:
    """Fuses person references from source records into a :class:`PersonStore`.

    Extraction is pure and deterministic, so indexing is idempotent: routing
    an already-indexed record again yields zero new evidence and zero new
    people.
    """

    def __init__(self, store: PersonStore) -> None:
        self._store = store

    @property
    def store(self) -> PersonStore:
        return self._store

    def index_records(
        self, records, *, limit: int | None = None, offset: int = 0
    ) -> PersonIndexReport:
        """Index a bounded window of source records and report aggregates.

        ``limit``/``offset`` bound the number of records indexed (an
        ``offset`` before a ``limit``), matching the CLI's bounded indexing
        without ever materializing the whole corpus.
        """
        window = list(records)
        if offset:
            window = window[offset:]
        if limit is not None:
            window = window[:limit]
        people_before = self._store.count()
        references_total = 0
        for record in window:
            for reference in extract_person_references(record):
                self._store.upsert_reference(reference)
                references_total += 1
        people_after = self._store.count()
        return PersonIndexReport(
            source_type=window[0].source_type if window else "",
            records=len(window),
            references=references_total,
            people_before=people_before,
            people_after=people_after,
            new_people=people_after - people_before,
        )

    def index_documents(
        self,
        document_store,
        *,
        source_type: str = "email",
        limit: int | None = None,
        offset: int = 0,
    ) -> PersonIndexReport:
        """Index already-ingested documents via their stored metadata.

        Routes documents that were already ingested (most useful for email,
        whose headers live in document metadata) through the same extraction
        as a fresh adapter run, so an operator never needs to re-export a
        source directory to populate the people layer. Because the pseudo
        record reuses the stored ``source``/``content_hash``/``source_type``,
        evidence document ids are identical to the ones an adapter route
        would produce.
        """
        before = self._store.count()
        references_total = 0
        records = 0
        fetch_offset = offset
        while True:
            documents = document_store.list_documents(
                source_type=source_type, limit=_PAGE, offset=fetch_offset
            )
            if not documents:
                break
            for document in documents:
                record = _document_as_record(document)
                for reference in extract_person_references(record):
                    self._store.upsert_reference(reference)
                    references_total += 1
                records += 1
            fetch_offset += len(documents)
            if limit is not None and records >= limit:
                break
        return PersonIndexReport(
            source_type=source_type,
            records=records,
            references=references_total,
            people_before=before,
            people_after=self._store.count(),
            new_people=self._store.count() - before,
        )


def _document_as_record(document) -> SourceRecord:
    """Rebuild a pseudo record from a stored document's metadata."""
    return SourceRecord(
        source_type=document.source_type,
        source_key=document.source,
        content_hash=document.content_hash,
        created_at=document.created_at,
        modified_at=document.modified_at,
        payload=None,
        metadata=dict(document.metadata),
    )
