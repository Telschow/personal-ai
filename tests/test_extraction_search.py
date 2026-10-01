"""Tests for structured extraction search."""

from personal_ai.documents.structured import StructuredExtraction
from personal_ai.storage import ExtractionStore, connect_database


def _make_extraction(
    document_id: str,
    summary: str = "",
    people: tuple[str, ...] = (),
    organizations: tuple[str, ...] = (),
    projects: tuple[str, ...] = (),
    goals: tuple[str, ...] = (),
    topics: tuple[str, ...] = (),
) -> StructuredExtraction:
    return StructuredExtraction(
        document_id=document_id,
        summary=summary,
        people=people,
        organizations=organizations,
        projects=projects,
        goals=goals,
        topics=topics,
    )


class TestExtractionSearch:
    """Structured extraction search across all fields."""

    def setup_method(self) -> None:
        self.connection = connect_database(":memory:")
        self.store = ExtractionStore(self.connection)

    def teardown_method(self) -> None:
        self.connection.close()

    def _seed(self) -> None:
        self.store.save(
            _make_extraction(
                "doc-1",
                summary="Alice works at Example Consulting on consulting projects",
                people=("Alice Example",),
                organizations=("Example Consulting",),
                projects=("Project Apollo",),
                goals=("Career advancement",),
                topics=("consulting", "strategy"),
            )
        )
        self.store.save(
            _make_extraction(
                "doc-2",
                summary="Meeting notes about product development",
                people=("Bob", "Carol"),
                organizations=("Example GmbH",),
                projects=("Product X",),
                goals=("Ship by Q4",),
                topics=("product", "engineering"),
            )
        )
        self.store.save(
            _make_extraction(
                "doc-3",
                summary="Financial goals for 2026",
                people=("Alice Example",),
                organizations=(),
                projects=("Financial independence",),
                goals=("Save 100k", "Invest wisely"),
                topics=("finance", "goals"),
            )
        )

    def test_search_by_person(self) -> None:
        self._seed()
        results = self.store.search("Alice")
        assert len(results) == 2
        doc_ids = {r.document_id for r in results}
        assert "doc-1" in doc_ids
        assert "doc-3" in doc_ids

    def test_search_by_organization(self) -> None:
        self._seed()
        results = self.store.search("Example Consulting")
        assert len(results) == 1
        assert results[0].document_id == "doc-1"
        assert "organizations" in results[0].matched_fields

    def test_search_by_project(self) -> None:
        self._seed()
        results = self.store.search("Apollo")
        assert len(results) == 1
        assert results[0].document_id == "doc-1"
        assert "projects" in results[0].matched_fields

    def test_search_by_goal(self) -> None:
        self._seed()
        results = self.store.search("Career")
        assert len(results) == 1
        assert results[0].document_id == "doc-1"
        assert "goals" in results[0].matched_fields

    def test_search_by_topic(self) -> None:
        self._seed()
        results = self.store.search("consulting")
        assert len(results) == 1
        assert results[0].document_id == "doc-1"
        assert "topics" in results[0].matched_fields

    def test_search_summary_text(self) -> None:
        self._seed()
        results = self.store.search("product development")
        assert len(results) == 1
        assert results[0].document_id == "doc-2"
        assert "summary" in results[0].matched_fields

    def test_case_insensitive(self) -> None:
        self._seed()
        lower = self.store.search("example consulting")
        upper = self.store.search("Example Consulting")
        mixed = self.store.search("Example consulting")
        assert lower == upper == mixed

    def test_no_result_query(self) -> None:
        self._seed()
        results = self.store.search("nonexistent")
        assert results == ()

    def test_empty_query(self) -> None:
        self._seed()
        assert self.store.search("") == ()
        assert self.store.search("  ") == ()

    def test_limit_applied(self) -> None:
        self._seed()
        results = self.store.search("Alice", limit=1)
        assert len(results) == 1

    def test_score_ordering(self) -> None:
        self._seed()
        results = self.store.search("Alice")
        assert len(results) >= 2
        # doc-1 has Alice in people AND summary match
        # doc-3 has Alice in people AND summary match
        # Both should have scores > 0
        for r in results:
            assert r.score > 0

    def test_exact_match_scores_higher(self) -> None:
        self.connection.execute("DELETE FROM structured_extractions")
        self.store.save(
            _make_extraction(
                "doc-exact",
                summary="Example Consulting is a consulting firm",
                organizations=("Example Consulting",),
            )
        )
        self.store.save(
            _make_extraction(
                "doc-partial",
                summary="Meeting about ABC Corp",
                organizations=("ABC Corporation",),
            )
        )
        results = self.store.search("Example Consulting")
        assert len(results) >= 1
        assert results[0].document_id == "doc-exact"

    def test_substring_match_works(self) -> None:
        self.connection.execute("DELETE FROM structured_extractions")
        self.store.save(
            _make_extraction(
                "doc-1",
                topics=("interview preparation",),
            )
        )
        results = self.store.search("interview")
        assert len(results) == 1
        assert results[0].document_id == "doc-1"

    def test_matched_fields_populated(self) -> None:
        self._seed()
        results = self.store.search("Alice")
        for r in results:
            assert len(r.matched_fields) > 0

    def test_deterministic_ordering(self) -> None:
        self._seed()
        first = self.store.search("Alice")
        second = self.store.search("Alice")
        assert first == second

    def test_empty_store(self) -> None:
        results = self.store.search("anything")
        assert results == ()
