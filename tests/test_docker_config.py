"""Offline validation of the Phase 45 Docker deployment artifacts.

These tests read the actual files in the repository (``docker/Dockerfile``,
``.dockerignore``, ``docker/docker-compose.yml``) and assert the properties that
make the Personal AI gateway deployable alongside Open WebUI:

* the image is built with the pinned ``uv.lock`` on Python >= 3.14 and runs the
  existing module entrypoint (no invented console script),
* private personal data and local dev artifacts can never enter the image,
* the compose service joins the external ``open-webui_default`` /
  ``ollama_default`` networks, exposes no host ports, health-checks via
  ``/v1/models``, and runtime-mounts the SQLite database + workspace.

No Docker daemon, network, or Ollama is required — this is static file
validation so the normal test suite stays hermetic.
"""

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DOCKERFILE = (REPO_ROOT / "docker" / "Dockerfile").read_text()
DOCKERIGNORE = (REPO_ROOT / ".dockerignore").read_text()
COMPOSE = (REPO_ROOT / "docker" / "docker-compose.yml").read_text()

PRIVATE_DATA = (
    "Email_Outlook",
    "Financial_data",
    "Workouts",
    "raw_data.zip",
    "raw_data_extracted",
    "previous_project_and_raw_data",
)


def _exclusion_lines(text: str) -> list[str]:
    """Non-comment lines: the actual Docker ignore patterns."""
    return [ln for ln in text.splitlines() if ln and not ln.startswith("#")]


class TestDockerfile:
    def test_builds_from_python_314_runtime(self) -> None:
        assert "uv:python3.14-bookworm-slim" in DOCKERFILE

    def test_installs_dependencies_speculatively_from_lockfile(self) -> None:
        assert "uv sync --frozen --no-dev --no-editable" in DOCKERFILE

    def test_runs_existing_module_entrypoint(self) -> None:
        assert 'python", "-m", "personal_ai.server"' in DOCKERFILE

    def test_listens_on_all_interfaces_for_the_docker_network(self) -> None:
        assert '"0.0.0.0"' in DOCKERFILE
        assert '"8000"' in DOCKERFILE
        assert "EXPOSE 8000" in DOCKERFILE

    def test_does_not_install_test_or_dev_tooling(self) -> None:
        assert "pytest" not in DOCKERFILE
        assert "ruff" not in DOCKERFILE

    def test_does_not_copy_private_data_or_tests(self) -> None:
        for entry in (*PRIVATE_DATA, "tests", "data", "knowledge.db"):
            assert f"COPY {entry}" not in DOCKERFILE, entry


class TestDockerignore:
    def test_private_personal_data_is_excluded(self) -> None:
        for entry in PRIVATE_DATA:
            assert entry in DOCKERIGNORE, entry

    def test_local_runtime_and_dev_artifacts_are_excluded(self) -> None:
        for entry in (
            ".git",
            ".venv",
            "tests",
            "data",
            "knowledge.db",
            ".pytest_cache",
            "__pycache__",
        ):
            assert entry in DOCKERIGNORE, entry

    def test_build_critical_files_are_kept(self) -> None:
        # hatchling needs README.md and uv needs the lockfile + pyproject; they
        # may be mentioned in comments but must never be exclusion lines.
        assert "README.md" not in _exclusion_lines(DOCKERIGNORE)
        assert "pyproject.toml" not in _exclusion_lines(DOCKERIGNORE)
        assert "uv.lock" not in _exclusion_lines(DOCKERIGNORE)


class TestCompose:
    def test_service_joins_external_open_webui_network(self) -> None:
        assert "open-webui" in COMPOSE
        assert "name: open-webui_default" in COMPOSE
        assert "external: true" in COMPOSE

    def test_service_joins_external_ollama_network(self) -> None:
        assert "ollama" in COMPOSE
        assert "name: ollama_default" in COMPOSE

    def test_no_host_port_is_exposed(self) -> None:
        assert "ports:" not in COMPOSE

    def test_healthcheck_uses_models_endpoint(self) -> None:
        assert "http://127.0.0.1:8000/v1/models" in COMPOSE
        assert "healthcheck:" in COMPOSE

    def test_database_and_workspace_are_runtime_mounts(self) -> None:
        assert "PERSONAL_AI_WORKSPACE: /data/workspace" in COMPOSE
        assert "PERSONAL_AI_DATABASE: /data/personal-ai.db" in COMPOSE
        assert "../data:/data" in COMPOSE
        assert "../data/workspace:/data/workspace" in COMPOSE

    def test_ollama_endpoint_is_configurable_per_network(self) -> None:
        assert "OLLAMA_BASE_URL: http://ollama:11434" in COMPOSE

    def test_build_context_and_dockerfile_reference_the_repo(self) -> None:
        assert "context: .." in COMPOSE
        assert "dockerfile: docker/Dockerfile" in COMPOSE

    def test_chat_model_is_environment_overridable(self) -> None:
        assert "PERSONAL_AI_CHAT_MODEL" in COMPOSE
        assert "qwen3.5:9b" in COMPOSE
