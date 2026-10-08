"""Tests for the deterministic, synthetic-only architecture demonstration.

These tests prove the properties the demo claims: synthetic-only inputs, no
network requirement, no personal data, deterministic output, preserved
evidence and provenance, an enforced policy boundary, and a visible approval
boundary. They also assert that no model is required for the run to succeed.
"""

from __future__ import annotations

import ast
import json
import re
import socket
from pathlib import Path

import pytest

from personal_ai.agents.models import Permission, PolicyDecision
from personal_ai.demo import corpus, scenario
from personal_ai.demo.artifacts import (
    METADATA_FILENAME,
    RESULTS_FILENAME,
    build_metadata,
    write_artifacts,
)
from personal_ai.demo.scenario import demo_stages, run_demo

REPO_ROOT = Path(__file__).resolve().parent.parent
DEMO_PACKAGE = REPO_ROOT / "src" / "personal_ai" / "demo"

# Reserved documentation domains (RFC 2606 / RFC 5737). Any address-shaped
# string in the demo must use one of these.
RESERVED_DOMAINS = ("example.invalid", "example.com", "example.test", "example.org")


@pytest.fixture(scope="module")
def result() -> scenario.DemoResult:
    return run_demo()


@pytest.fixture(scope="module")
def stages(result: scenario.DemoResult) -> dict:
    return result.stages


def test_demo_declares_itself_synthetic(result: scenario.DemoResult) -> None:
    assert result.synthetic is True
    assert corpus.SYNTHETIC_MARKER == "SYNTHETIC-DEMO-DATA"
    assert result.stages["private_input"]["marker"] == corpus.SYNTHETIC_MARKER


def test_every_synthetic_input_carries_the_marker() -> None:
    for _label, _key, text in corpus.SYNTHETIC_SOURCES:
        assert corpus.SYNTHETIC_MARKER in text, "every corpus document must be marked"
    assert corpus.SYNTHETIC_MARKER in corpus.SYNTHETIC_PROJECT_DOCUMENT
    assert corpus.SYNTHETIC_MARKER in corpus.SYNTHETIC_JOB_POSTING


def test_no_real_contact_details_in_the_corpus() -> None:
    blob = (
        f"{corpus.SYNTHETIC_PROJECT_DOCUMENT}\n"
        f"{corpus.SYNTHETIC_JOB_POSTING}\n"
        f"{corpus.SYNTHETIC_QUERY}\n"
        f"{corpus.PROPOSED_ACTION}"
    )
    assert "Daniel" not in blob
    assert "Telschow" not in blob
    assert "hotmail" not in blob

    addresses = re.findall(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", blob)
    assert addresses, "the synthetic corpus should still contain a contact address"
    for address in addresses:
        domain = address.rsplit("@", 1)[1].lower()
        assert any(
            domain == reserved or domain.endswith(f".{reserved}")
            for reserved in RESERVED_DOMAINS
        ), f"non-reserved address domain in corpus: {domain!r}"


def test_no_financial_or_real_job_market_data() -> None:
    blob = corpus.SYNTHETIC_PROJECT_DOCUMENT + corpus.SYNTHETIC_JOB_POSTING
    for banned in ("salary", "€", "$", "bonus", "stock option", "Gehalt"):
        assert banned not in blob, f"unexpected real-world financial token: {banned}"
    assert "Example Systems" in blob, "employer must be explicitly fictional"
    assert "fictional" in blob


def test_scenario_uses_only_real_repository_components() -> None:
    """Every component named in the demo must exist in the shipped package."""
    from personal_ai.demo.artifacts import DEMONSTRATED_COMPONENTS

    for dotted in DEMONSTRATED_COMPONENTS:
        module_path, _, attribute = dotted.rpartition(".")
        module = __import__(module_path, fromlist=[attribute])
        assert hasattr(module, attribute), f"demo claims unimplemented {dotted}"


def test_demo_runs_without_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """The demo must succeed even if every socket connection is refused."""

    def _forbidden(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("the demonstration must not open a network connection")

    monkeypatch.setattr(socket, "socket", _forbidden)
    monkeypatch.setattr(socket, "create_connection", _forbidden)

    offline = run_demo()
    assert offline.stages["retrieve"]["hit_count"] >= 1


def test_demo_runs_without_a_model(monkeypatch: pytest.MonkeyPatch) -> None:
    """No Ollama client may be constructed during the run."""
    from personal_ai import ollama_client

    def _forbidden(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("the demonstration must not require a local model")

    monkeypatch.setattr(ollama_client.OllamaClient, "__init__", _forbidden)

    model_free = run_demo()
    assert model_free.stages["local_reasoning"]["llm_invoked"] is False
    assert model_free.stages["ingest"]["embedding_model_calls"] == 0
    assert model_free.stages["ingest"]["documents"][0]["structured_extraction"] is False


def test_demo_module_imports_no_network_or_model_client() -> None:
    """Static guarantee: the demo package must not import provider clients."""
    banned = {"requests", "httpx", "urllib.request", "personal_ai.ollama_client"}
    for path in sorted(DEMO_PACKAGE.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            for name in names:
                assert name not in banned, f"{path.name} imports {name}"


def test_output_is_deterministic_across_runs() -> None:
    first = run_demo().to_json()
    second = run_demo().to_json()
    assert first == second


def test_artifacts_are_byte_identical_across_writes(tmp_path: Path) -> None:
    first = write_artifacts("demo", root=tmp_path)
    first_bytes = {name: path.read_bytes() for name, path in first.items()}
    second = write_artifacts("demo", root=tmp_path)
    for name, path in second.items():
        assert path.read_bytes() == first_bytes[name], f"{name} is not byte-stable"


def test_written_artifacts_are_valid_json_without_wall_clock(tmp_path: Path) -> None:
    written = write_artifacts("demo", root=tmp_path)
    results = json.loads(written["results"].read_text(encoding="utf-8"))
    metadata = json.loads(written["metadata"].read_text(encoding="utf-8"))

    assert results["synthetic"] is True
    # results.json is written with sorted keys for byte-stability, so it
    # carries the stage set; metadata.json carries the canonical order.
    assert set(results["stages"]) == set(demo_stages())
    assert metadata["stages"] == list(demo_stages())
    assert metadata["deterministic"] is True
    assert metadata["requires_network"] is False
    assert metadata["requires_llm"] is False
    assert metadata["action_performed"] is False
    # A recorded generation time would break byte-determinism.
    assert "generated_at" not in metadata
    assert "timestamp" not in metadata


def test_ingestion_uses_real_pipeline_and_stores_provenance(stages: dict) -> None:
    ingest = stages["ingest"]
    assert ingest["documents_ingested"] == 2
    assert ingest["total_chunks"] >= 2
    for document in ingest["documents"]:
        assert document["kind"] == "text_heavy", (
            "real classifier must route to chunking"
        )
        assert document["chunk_count"] >= 1
        assert len(document["document_id"]) == 64, "id must be a sha256 hex digest"
    sources = stages["private_input"]["sources"]
    for source in sources:
        assert len(source["content_hash"]) == 64
        assert source["bytes"] > 0


def test_retrieval_is_keyword_and_returns_ranked_hits(stages: dict) -> None:
    retrieve = stages["retrieve"]
    assert retrieve["backend"] == "sqlite_fts5_bm25_keyword"
    assert retrieve["hit_count"] >= 1
    ranks = [hit["rank"] for hit in retrieve["retrieved"]]
    assert ranks == sorted(ranks), "hits must be ordered best (smallest rank) first"


def test_evidence_preserves_identity_and_provenance(stages: dict) -> None:
    evidence = stages["evidence"]
    assert evidence["status"] == "results"
    assert evidence["total_returned"] == evidence["evidence_count"]
    assert evidence["all_provenance_complete"] is True
    for item in evidence["evidence"]:
        assert item["chunk_id"] and len(item["chunk_id"]) == 64
        assert item["document_id"] and len(item["document_id"]) == 64
        assert item["source_type"] == "file"
        assert item["source"].startswith("synthetic/")
        assert item["content_hash"] and len(item["content_hash"]) == 64
        assert item["provenance_complete"] is True


def test_local_reasoning_cites_evidence_without_hidden_reasoning(stages: dict) -> None:
    reasoning = stages["local_reasoning"]
    assert reasoning["llm_invoked"] is False
    assert reasoning["mode"] == "deterministic_evidence_summary"
    assert reasoning["cited_evidence_count"] == stages["evidence"]["total_returned"]
    assert reasoning["cited_chunk_ids"], "reasoning must cite the retrieved chunks"
    cited = set(reasoning["cited_chunk_ids"])
    stored = {item["chunk_id"] for item in stages["evidence"]["evidence"]}
    assert cited == stored, "every citation must resolve to real retrieved evidence"
    assert "chain-of-thought" in reasoning["disclosure"]


def test_policy_boundary_is_enforced_in_code(stages: dict) -> None:
    policy = stages["policy"]
    assert policy["decision"] == PolicyDecision.APPROVAL_REQUIRED.value
    assert policy["allowed"] is False
    assert policy["required_permission"] == Permission.NETWORK.value
    assert policy["enforced_in_code"] is True
    assert policy["decisions_recorded"] >= 1, "decisions must be recorded for audit"


def test_approval_boundary_is_visible_in_the_output(stages: dict) -> None:
    approval = stages["human_approval"]
    assert approval["execution_blocked"] is True
    assert "requires approval" in approval["approval_required_error"]
    assert approval["handler_invoked"] is False
    assert approval["plan_status"] == "needs_approval"
    assert approval["completion_transition_blocked"] is True
    assert approval["action_performed"] is False
    assert approval["awaiting"] == "explicit human approval"


def test_action_is_bounded_and_never_performed(stages: dict) -> None:
    bounded = stages["bounded_action"]
    assert bounded["action_performed"] is False
    assert bounded["actions_performed"] == 0
    assert "human approval" in bounded["reason"]


def test_demo_does_not_describe_the_system_as_autonomous() -> None:
    written_metadata = build_metadata(run_demo().to_json())
    autonomy = written_metadata["autonomy_claim"].lower()
    assert "not autonomous" in autonomy
    assert written_metadata["stops_at"] == "human approval boundary"
    assert written_metadata["is_benchmark"] is False


def test_metadata_declares_no_benchmark_or_model_claims() -> None:
    metadata = build_metadata(run_demo().to_json())
    assert metadata["is_benchmark"] is False
    assert "not a benchmark" in metadata["benchmark_disclosure"].lower()
    assert metadata["privacy"]["personal_data"] is False
    assert metadata["privacy"]["real_emails"] is False
    assert metadata["privacy"]["real_finances"] is False
    assert metadata["privacy"]["real_job_searches"] is False
    assert metadata["privacy"]["external_services_called"] is False
    assert metadata["privacy"]["secrets_exposed"] is False


def test_generated_artifacts_are_not_tracked_by_git() -> None:
    ignore = (REPO_ROOT / ".gitignore").read_text(encoding="utf-8")
    assert "/artifacts/" in ignore, "generated demo artifacts must stay untracked"


def test_snapshot_is_optional_and_not_required_for_results(tmp_path: Path) -> None:
    written = write_artifacts("demo", root=tmp_path)
    assert set(written) == {"results", "metadata"}
    assert not (tmp_path / "demo" / "snapshot.png").exists()
    metadata = json.loads(written["metadata"].read_text(encoding="utf-8"))
    assert metadata["snapshot"]["filename"] == "snapshot.png"


def test_artifact_filenames_are_stable(tmp_path: Path) -> None:
    written = write_artifacts("demo", root=tmp_path)
    assert written["results"].name == RESULTS_FILENAME
    assert written["metadata"].name == METADATA_FILENAME


# --- Optional snapshot rasterizer -------------------------------------------
# The PNG is a convenience artifact: demo.html is the authoritative rendering.
# These tests cover the pure helpers and the failure path, and deliberately do
# not require the generated artifacts to exist.


def _load_snapshot_module():
    import importlib.util

    path = REPO_ROOT / "scripts" / "render_demo_snapshot.py"
    spec = importlib.util.spec_from_file_location("render_demo_snapshot", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_snapshot_script_extracts_the_single_inline_svg() -> None:
    module = _load_snapshot_module()
    html = (
        "<html><body><div>ignore me</div>"
        '<svg viewBox="0 0 1240 676"><rect/></svg>'
        "<p>trailing</p></body></html>"
    )
    svg = module.extract_svg(html)
    assert svg.startswith("<svg")
    assert svg.endswith("</svg>")
    assert module.parse_view_box(svg) == (1240.0, 676.0)


def test_snapshot_script_rejects_html_without_an_svg() -> None:
    module = _load_snapshot_module()
    with pytest.raises(module.RenderUnavailable):
        module.extract_svg("<html><body>no diagram here</body></html>")


def test_snapshot_script_reports_missing_rasterizer_instead_of_writing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    module = _load_snapshot_module()

    def _missing() -> None:
        raise module.RenderUnavailable("missing system rasterization library")

    monkeypatch.setattr(module, "_load_libraries", _missing)
    with pytest.raises(module.RenderUnavailable):
        module.render_png('<svg viewBox="0 0 10 10"></svg>', tmp_path / "out.png")
    assert not (tmp_path / "out.png").exists()


def test_snapshot_script_is_not_imported_by_the_demo_package() -> None:
    """The rasterizer stays a build helper; runtime never needs it."""
    module = _load_snapshot_module()
    assert module.DEFAULT_WIDTH == 1920
    assert module.DEFAULT_HEIGHT == 1080
    for path in sorted(DEMO_PACKAGE.glob("*.py")):
        assert "render_demo_snapshot" not in path.read_text(encoding="utf-8")


# --- Presentation assets -----------------------------------------------------
# The README is the GitHub landing surface for the demo. These tests keep the
# image and the interactive file reachable and keep the section honest.

DOCS_ASSETS = (
    REPO_ROOT / "docs" / "architecture" / "demo-snapshot.png",
    REPO_ROOT / "docs" / "architecture" / "demo.html",
    REPO_ROOT / "docs" / "architecture" / "demo.workflow.json",
)


def _readme() -> str:
    return (REPO_ROOT / "README.md").read_text(encoding="utf-8")


def _demo_section() -> str:
    readme = _readme()
    start = readme.index("## See it in action")
    end = readme.index("## Features", start)
    return readme[start:end]


def test_readme_shows_the_snapshot_before_any_other_demo_visual() -> None:
    section = _demo_section()
    image = section.index("![")
    html_link = section.index("demo.html")
    assert image < html_link, "the static PNG must come first in the demo section"


def test_readme_links_exactly_the_presentation_assets() -> None:
    section = _demo_section()
    assert "docs/architecture/demo-snapshot.png" in section
    assert "docs/architecture/demo.html" in section
    assert "artifacts/demo/results.json" in section
    assert "artifacts/demo/metadata.json" in section


@pytest.mark.parametrize("asset", DOCS_ASSETS, ids=lambda p: p.name)
def test_presentation_assets_exist_and_are_committable(asset: Path) -> None:
    """A gitignored asset would render as a broken image on GitHub."""
    assert asset.exists(), f"missing presentation asset: {asset}"
    ignored = _git_ignored(asset)
    assert not ignored, f"{asset.name} is gitignored and would break the README"


@pytest.mark.parametrize("asset", DOCS_ASSETS, ids=lambda p: p.name)
def test_presentation_assets_are_small_enough_to_check_in(asset: Path) -> None:
    assert asset.stat().st_size < 1_500_000, f"{asset.name} is unexpectedly large"


def test_snapshot_is_a_real_1920x1080_png() -> None:
    snapshot = REPO_ROOT / "docs" / "architecture" / "demo-snapshot.png"
    header = snapshot.read_bytes()[:24]
    assert header[:8] == b"\x89PNG\r\n\x1a\n"
    width = int.from_bytes(header[16:20], "big")
    height = int.from_bytes(header[20:24], "big")
    assert (width, height) == (1920, 1080)


def test_snapshot_copy_matches_the_regenerable_artifact() -> None:
    """Both PNGs come from one deterministic source and must not drift."""
    published = REPO_ROOT / "docs" / "architecture" / "demo-snapshot.png"
    generated = REPO_ROOT / "artifacts" / "demo" / "snapshot.png"
    if not generated.exists():
        pytest.skip("regenerable artifact not present in this checkout")
    assert published.read_bytes() == generated.read_bytes()


def test_published_html_matches_the_delivered_artifact() -> None:
    published = REPO_ROOT / "docs" / "architecture" / "demo.html"
    generated = REPO_ROOT / "artifacts" / "demo" / "demo.html"
    if not generated.exists():
        pytest.skip("delivered artifact not present in this checkout")
    assert published.read_bytes() == generated.read_bytes()


def test_interactive_demo_stays_self_contained() -> None:
    """No external font, script, or CDN may be referenced by the demo HTML."""
    html = (REPO_ROOT / "docs" / "architecture" / "demo.html").read_text(
        encoding="utf-8"
    )
    for pattern in (
        r'src\s*=\s*["\']https?://',
        r'href\s*=\s*["\']https?://',
        r"@import",
        r"fonts\.googleapis",
        r"cdn\.",
        r"unpkg",
        r"jsdelivr",
    ):
        assert not re.search(pattern, html, re.IGNORECASE), f"external ref: {pattern}"
    # Any embedded font must be inlined, never linked.
    for url in re.findall(r"src:\s*url\(([^)]{0,80})", html):
        assert url.strip().startswith("data:"), f"remote font source: {url}"


def test_readme_demo_section_makes_no_forbidden_claim() -> None:
    """Guard the section against reintroducing an overstated claim."""
    section = _demo_section().lower()
    forbidden = (
        "state-of-the-art",
        "state of the art",
        "outperforms",
        "best-in-class",
        "best in class",
        "production-ready",
        "production ready",
        "enterprise-grade",
        "enterprise grade",
        "battle-tested",
        "battle tested",
        "hands-free",
        "hands free",
        "fully autonomous",
        "fully automated",
        "without human approval",
        "no human approval",
        "without human oversight",
    )
    for phrase in forbidden:
        assert phrase not in section, (
            f"forbidden claim in README demo section: {phrase}"
        )
    # The section must positively disclaim the four risk areas.
    for required in (
        "not a benchmark",
        "no language model is called",
        "not autonomous",
        "no real data",
    ):
        assert required in section, f"missing disclaimer: {required}"


def test_readme_demo_section_states_synthetic_and_offline() -> None:
    section = _demo_section().lower()
    assert "synthetic" in section
    assert "offline" in section
    assert "without explicit" in section and "approval" in section


def test_readme_demo_section_lists_all_eight_stages() -> None:
    section = _demo_section().lower()
    for stage in demo_stages():
        label = stage.replace("_", " ").lower()
        assert label in section, f"README stage table missing {label}"


def _git_ignored(path: Path) -> bool:
    import subprocess

    result = subprocess.run(
        ["git", "check-ignore", "-q", str(path)],
        cwd=REPO_ROOT,
        capture_output=True,
        check=False,
    )
    return result.returncode == 0
