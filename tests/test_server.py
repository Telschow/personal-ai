"""Tests for the OpenAI-compatible HTTP API in :mod:`personal_ai.server`.

These tests deliberately avoid Ollama and the real Agent. They exercise:

* configuration resolution (env -> model/host/port)
* request parsing and validation (``CompletionRequest``)
* the OpenAI-compatible response shape via ``complete_chat``
* error mapping (bad requests, model unavailable, agent failures)
* the FastAPI route itself, using a fake agent factory
"""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from personal_ai.config import CHAT_MODEL_ENV, load_api_settings
from personal_ai.ollama_client import ChatMessage, OllamaConnectionError
from personal_ai.server import (
    API_TOKEN_ENV,
    DEFAULT_DATABASE_ENV,
    MODEL_ID,
    CompletionRequest,
    build_response,
    complete_chat,
    create_app,
    main,
    parse_args,
    resolve_config,
)

DEFAULT_MODEL = "qwen3.5:9b"


class FakeAgent:
    def __init__(self, answer: str = "fake answer", error: Exception | None = None):
        self.answer = answer
        self.error = error
        self.calls: list[list[ChatMessage]] = []

    def run(self, messages: list[ChatMessage]) -> str:
        self.calls.append(messages)
        if self.error is not None:
            raise self.error
        return self.answer


class FakeBuilt:
    def __init__(self, agent: FakeAgent):
        self.agent = agent
        self.closed = False

    def close(self) -> None:
        self.closed = True


def make_app(
    answer: str = "ok", error: Exception | None = None, token: str | None = None
):
    fake = FakeAgent(answer=answer, error=error)
    built = FakeBuilt(fake)
    app = create_app(
        None,
        None,
        model=DEFAULT_MODEL,
        token=token,
        agent_factory=lambda: built,
    )
    return app, fake, built


def test_load_api_settings_defaults():
    settings = load_api_settings({})
    assert settings.model == DEFAULT_MODEL
    assert settings.host == "127.0.0.1"
    assert settings.port == 8000


def test_load_api_settings_from_env():
    settings = load_api_settings(
        {
            CHAT_MODEL_ENV: "my-model",
            "PERSONAL_AI_API_HOST": "0.0.0.0",
            "PERSONAL_AI_API_PORT": "9090",
        }
    )
    assert settings.model == "my-model"
    assert settings.host == "0.0.0.0"
    assert settings.port == 9090


def test_load_api_settings_invalid_port():
    with pytest.raises(ValueError):
        load_api_settings({"PERSONAL_AI_API_PORT": "not-a-number"})
    with pytest.raises(ValueError):
        load_api_settings({"PERSONAL_AI_API_PORT": "70000"})


def test_resolve_config_overrides():
    class Args:
        def __init__(self):
            self.workspace = type("P", (), {"resolve": lambda self: "path"})()
            self.database = None
            self.host = "0.0.0.0"
            self.port = 1234
            self.token = None
            self.job_db = None

    cfg = resolve_config(Args(), {})
    assert cfg.host == "0.0.0.0"
    assert cfg.port == 1234
    assert cfg.model == DEFAULT_MODEL


def test_database_env_is_honored_by_parse_args(monkeypatch):
    """The container sets ``PERSONAL_AI_DATABASE`` (see docker-compose) but the
    server must actually read it, otherwise the corpus stores are never wired
    and the live agent answers as if it knows nothing. Mirrors how
    ``PERSONAL_AI_WORKSPACE`` is read for ``--workspace``."""
    monkeypatch.setenv(DEFAULT_DATABASE_ENV, "/data/personal-ai.db")
    args = parse_args([])
    assert isinstance(args.database, Path)
    assert str(args.database) == "/data/personal-ai.db"


def test_database_env_none_when_unset(monkeypatch):
    monkeypatch.delenv(DEFAULT_DATABASE_ENV, raising=False)
    assert parse_args([]).database is None


def test_main_builds_app_without_database(tmp_path):
    """Phase 46 regression: serving with a workspace but no ``--database``
    must not raise ``UnboundLocalError`` (``memory_service`` only exists when a
    database is configured). This guards the no-database configuration path
    (when ``PERSONAL_AI_DATABASE`` is unset and no ``--database`` is given)."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    app = main(["--workspace", str(workspace)], run=False)

    assert app is not None


class TestCompletionRequest:
    def test_valid_converts_messages(self):
        req = CompletionRequest(
            {
                "model": "anything",
                "messages": [
                    {"role": "system", "content": "sys"},
                    {"role": "user", "content": "hi"},
                ],
            }
        )
        result = req.validate()
        assert [m.role for m in result] == ["system", "user"]
        assert [m.content for m in result] == ["sys", "hi"]

    def test_full_conversation_history_preserved_in_order(self):
        req = CompletionRequest(
            {
                "messages": [
                    {"role": "system", "content": "You are a helper."},
                    {"role": "user", "content": "first"},
                    {"role": "assistant", "content": "answer one"},
                    {"role": "user", "content": "second"},
                ]
            }
        )
        result = req.validate()
        assert [(m.role, m.content) for m in result] == [
            ("system", "You are a helper."),
            ("user", "first"),
            ("assistant", "answer one"),
            ("user", "second"),
        ]

    def test_all_supported_roles_mapped(self):
        req = CompletionRequest(
            {
                "messages": [
                    {"role": "system", "content": "s"},
                    {"role": "assistant", "content": "a"},
                    {"role": "user", "content": "u"},
                ]
            }
        )
        result = req.validate()
        assert [m.role for m in result] == ["system", "assistant", "user"]

    def test_rejects_missing_user_message(self):
        with pytest.raises(Exception) as ei:
            CompletionRequest(
                {"messages": [{"role": "system", "content": "sys"}]}
            ).validate()
        assert ei.value.status_code == 400
        assert "user" in ei.value.message

    def test_rejects_blank_content(self):
        with pytest.raises(Exception) as ei:
            CompletionRequest(
                {"messages": [{"role": "user", "content": "   "}]}
            ).validate()
        assert ei.value.status_code == 400

    def test_rejects_non_dict(self):
        with pytest.raises(Exception) as ei:
            CompletionRequest([1, 2]).validate()
        assert ei.value.status_code == 400

    def test_rejects_empty_messages(self):
        with pytest.raises(Exception) as ei:
            CompletionRequest({"messages": []}).validate()
        assert ei.value.status_code == 400

    def test_rejects_missing_messages(self):
        with pytest.raises(Exception) as ei:
            CompletionRequest({}).validate()
        assert ei.value.status_code == 400

    def test_rejects_stream_as_non_boolean(self):
        request = CompletionRequest(
            {"messages": [{"role": "user", "content": "hi"}], "stream": "yes"}
        )
        with pytest.raises(Exception) as ei:
            _ = request.stream
        assert ei.value.status_code == 400

    def test_stream_property_true_and_false(self):
        assert CompletionRequest({}).stream is False
        assert (
            CompletionRequest(
                {"messages": [{"role": "user", "content": "hi"}], "stream": True}
            ).stream
            is True
        )

    def test_rejects_unknown_role(self):
        with pytest.raises(Exception) as ei:
            CompletionRequest(
                {"messages": [{"role": "tool", "content": "x"}]}
            ).validate()
        assert ei.value.status_code == 400

    def test_rejects_non_string_content(self):
        with pytest.raises(Exception) as ei:
            CompletionRequest(
                {"messages": [{"role": "user", "content": ["not-a-string"]}]}
            ).validate()
        assert ei.value.status_code == 400


class TestCompleteChat:
    def test_build_response_shape(self):
        resp = build_response("the answer", MODEL_ID)
        assert resp["object"] == "chat.completion"
        assert resp["model"] == MODEL_ID
        choice = resp["choices"][0]
        assert choice["message"]["role"] == "assistant"
        assert choice["message"]["content"] == "the answer"
        assert choice["finish_reason"] == "stop"
        assert set(resp) >= {"id", "created", "usage", "system_fingerprint"}

    def test_valid_request_runs_agent(self):
        agent = FakeAgent(answer="hello")
        payload = {"messages": [{"role": "user", "content": "hi"}]}
        resp = complete_chat(agent, CompletionRequest(payload), model=DEFAULT_MODEL)
        assert resp["choices"][0]["message"]["content"] == "hello"
        assert agent.calls[0][0].role == "user"
        assert agent.calls[0][0].content == "hi"

    def test_full_history_passed_to_agent_unchanged(self):
        agent = FakeAgent(answer="ok")
        payload = {
            "messages": [
                {"role": "system", "content": "sys"},
                {"role": "user", "content": "u1"},
                {"role": "assistant", "content": "a1"},
                {"role": "user", "content": "u2"},
            ]
        }
        complete_chat(agent, CompletionRequest(payload), model=DEFAULT_MODEL)
        assert [(m.role, m.content) for m in agent.calls[0]] == [
            ("system", "sys"),
            ("user", "u1"),
            ("assistant", "a1"),
            ("user", "u2"),
        ]

    def test_empty_answer_maps_to_500(self):
        agent = FakeAgent(answer="")
        payload = {"messages": [{"role": "user", "content": "hi"}]}
        with pytest.raises(Exception) as ei:
            complete_chat(agent, CompletionRequest(payload), model=DEFAULT_MODEL)
        assert ei.value.status_code == 500

    def test_model_unavailable_maps_to_502(self):
        agent = FakeAgent(error=OllamaConnectionError("down"))
        payload = {"messages": [{"role": "user", "content": "hi"}]}
        with pytest.raises(Exception) as ei:
            complete_chat(agent, CompletionRequest(payload), model=DEFAULT_MODEL)
        assert ei.value.status_code == 502
        assert ei.value.error_type == "model_unavailable"

    def test_agent_error_maps_to_500(self):
        from personal_ai.agent import MaxToolRoundsError

        agent = FakeAgent(error=MaxToolRoundsError("rounds"))
        payload = {"messages": [{"role": "user", "content": "hi"}]}
        with pytest.raises(Exception) as ei:
            complete_chat(agent, CompletionRequest(payload), model=DEFAULT_MODEL)
        assert ei.value.status_code == 500


class TestRoutes:
    def test_chat_completions_route(self):
        app, _, _ = make_app(answer="hi there")
        with TestClient(app) as client:
            res = client.post(
                "/v1/chat/completions",
                json={
                    "model": "ignored",
                    "messages": [{"role": "user", "content": "hi"}],
                },
            )
        assert res.status_code == 200
        body = res.json()
        assert body["choices"][0]["message"]["content"] == "hi there"
        assert body["model"] == MODEL_ID

    def test_route_rejects_invalid_body(self):
        app, _, _ = make_app()
        with TestClient(app) as client:
            res = client.post("/v1/chat/completions", json={"messages": []})
        assert res.status_code == 400
        assert "error" in res.json()

    def test_route_rejects_malformed_json(self):
        app, _, _ = make_app()
        with TestClient(app) as client:
            res = client.post(
                "/v1/chat/completions",
                content=b"{not valid json",
                headers={"Content-Type": "application/json"},
            )
        assert res.status_code == 400
        assert res.json()["error"]["type"] == "invalid_request_error"

    def test_route_models_endpoint(self):
        app, _, _ = make_app()
        with TestClient(app) as client:
            res = client.get("/v1/models")
        assert res.status_code == 200
        body = res.json()
        assert body["object"] == "list"
        assert body["data"][0]["id"] == MODEL_ID
        assert body["data"][0]["object"] == "model"

    def test_route_streams_openai_sse(self):
        app, _, _ = make_app(answer="hi there")
        with TestClient(app) as client:
            res = client.post(
                "/v1/chat/completions",
                json={"messages": [{"role": "user", "content": "hi"}], "stream": True},
            )
        assert res.status_code == 200
        assert res.headers["content-type"].startswith("text/event-stream")
        body = res.text
        assert '"delta": {"role": "assistant", "content": ""}' in body
        assert '"delta": {"content": "hi there"}' in body
        assert '"finish_reason": "stop"' in body
        assert "data: [DONE]" in body
        assert f'"model": "{MODEL_ID}"' in body

    def test_route_stream_maps_model_unavailable(self):
        app, _, _ = make_app(error=OllamaConnectionError("down"))
        with TestClient(app) as client:
            res = client.post(
                "/v1/chat/completions",
                json={"messages": [{"role": "user", "content": "hi"}], "stream": True},
            )
        assert res.status_code == 502
        assert res.json()["error"]["type"] == "model_unavailable"
        assert "data: [DONE]" not in res.text

    def test_route_maps_model_unavailable(self):
        app, _, _ = make_app(error=OllamaConnectionError("down"))
        with TestClient(app) as client:
            res = client.post(
                "/v1/chat/completions",
                json={"messages": [{"role": "user", "content": "hi"}]},
            )
        assert res.status_code == 502
        assert res.json()["error"]["type"] == "model_unavailable"

    def test_token_required(self):
        app, _, _ = make_app(answer="secret", token="s3cret")
        with TestClient(app) as client:
            res = client.post(
                "/v1/chat/completions",
                json={"messages": [{"role": "user", "content": "hi"}]},
            )
            assert res.status_code == 401
            wrong = client.post(
                "/v1/chat/completions",
                headers={"Authorization": "Bearer wrong-token"},
                json={"messages": [{"role": "user", "content": "hi"}]},
            )
            assert wrong.status_code == 401
            ok = client.post(
                "/v1/chat/completions",
                headers={"Authorization": "Bearer s3cret"},
                json={"messages": [{"role": "user", "content": "hi"}]},
            )
        assert ok.status_code == 200

    def test_built_closed_on_shutdown(self):
        fake = FakeAgent()
        built = FakeBuilt(fake)
        app = create_app(
            None,
            None,
            model=DEFAULT_MODEL,
            agent_factory=lambda: built,
        )
        with TestClient(app):
            assert app.state.agent is fake
        assert built.closed is True

    def test_static_dashboard_served_at_root(self):
        app, _, _ = make_app(answer="hi there")
        with TestClient(app) as client:
            index = client.get("/")
            assert index.status_code == 200
            assert "Personal Job Agent" in index.text
            app_js = client.get("/app.js")
            assert app_js.status_code == 200
            assert "text/javascript" in app_js.headers["content-type"]
            styles = client.get("/styles.css")
            assert styles.status_code == 200
            assert "text/css" in styles.headers["content-type"]

    def test_discover_writes_to_app_job_db(self, tmp_path):
        import sqlite3

        import pytest

        from personal_ai.server import JOB_AGENT_AVAILABLE

        if not JOB_AGENT_AVAILABLE:
            pytest.skip("job_agent module not installed")

        from job_agent import discovery_search, pipeline

        job_db = tmp_path / "app-job.sqlite3"

        class FakeResult:
            def __init__(self):
                self.persisted_by_source = {"remote_remoteok": 2}
                self.total_duplicates = 0
                self.jobs_seen = {"job-1"}

        class FakePacingReport:
            def to_dict(self):
                return {"ok": True}

        class FakePlan:
            def queries(self):
                return ["site:remoteok.com product jobs"]

        def fake_discovery(*args, **kwargs):
            return FakePlan(), [], [], [], FakePacingReport()

        def fake_ingest(
            conn, jobs, profile, policy, provenance=None, run_started_at=None
        ):
            conn.execute("CREATE TABLE IF NOT EXISTS marker (x INTEGER)")
            conn.execute("INSERT INTO marker VALUES (1)")
            conn.commit()
            return FakeResult()

        def fake_lifecycle(conn, cfg, seen_ids):
            return {"active": 1}

        monkeypatch = pytest.MonkeyPatch()
        monkeypatch.setattr(discovery_search, "run_planned_discovery", fake_discovery)
        monkeypatch.setattr(pipeline, "ingest_global_jobs", fake_ingest)
        monkeypatch.setattr(pipeline, "run_lifecycle", fake_lifecycle)

        fake = FakeAgent()
        app = create_app(
            None,
            None,
            model=DEFAULT_MODEL,
            agent_factory=lambda: FakeBuilt(fake),
            job_db_path=job_db,
        )
        try:
            with TestClient(app) as client:
                res = client.post("/api/job-agent/discover", json={})
            assert res.status_code == 200
            body = res.json()
            assert body["jobs_persisted"] == 2
            marker = (
                sqlite3.connect(str(job_db))
                .execute("SELECT COUNT(*) FROM marker")
                .fetchone()[0]
            )
            assert marker == 1
        finally:
            monkeypatch.undo()

    def test_evaluation_total_is_normalized_to_01(self, tmp_path):

        import pytest

        from personal_ai.server import JOB_AGENT_AVAILABLE

        if not JOB_AGENT_AVAILABLE:
            pytest.skip("job_agent module not installed")

        from job_agent.models import Job, Score

        from job_agent import db as job_db

        job_file = tmp_path / "jobs.sqlite3"
        conn = job_db.connect(str(job_file))
        job = Job(
            id="test:1",
            title="Senior Product Manager",
            company="Test Corp",
            url="https://example.org/job",
            source="jsonld",
            source_type="ats",
            description="A product management role.",
        )
        job_db.upsert_job(conn, job)
        job_db.record_evaluation(
            conn,
            "test:1",
            Score(
                total=72.8,
                decision="review",
                reasons=["strong title match"],
                gaps=["no travel"],
                confidence=0.7,
            ),
            scoring_version="1.0",
            profile_version="p1",
        )
        conn.commit()
        conn.close()

        fake = FakeAgent()
        app = create_app(
            None,
            None,
            model=DEFAULT_MODEL,
            agent_factory=lambda: FakeBuilt(fake),
            job_db_path=job_file,
        )
        with TestClient(app) as client:
            res = client.get("/api/job-agent/jobs/test:1")
            assert res.status_code == 200
            body = res.json()
            assert body["evaluation"]["total"] == pytest.approx(0.728)
            assert body["evaluation"]["decision"] == "review"


class TestConfigPrecedence:
    def make_args(self, token=None, host=None, port=None):
        class Args:
            def __init__(self):
                self.workspace = type("P", (), {"resolve": lambda self: "path"})()
                self.database = None
                self.host = host
                self.port = port
                self.token = token
                self.job_db = None

        return Args()

    def test_no_token_when_unset(self):
        cfg = resolve_config(self.make_args(), {})
        assert cfg.token is None

    def test_env_token_works(self):
        cfg = resolve_config(
            self.make_args(),
            {API_TOKEN_ENV: "env-token"},
        )
        assert cfg.token == "env-token"

    def test_cli_token_overrides_env_token(self):
        cfg = resolve_config(
            self.make_args(token="cli-token"),
            {API_TOKEN_ENV: "env-token"},
        )
        assert cfg.token == "cli-token"

    def test_cli_host_port_override_env(self):
        cfg = resolve_config(
            self.make_args(host="0.0.0.0", port=9999),
            {"PERSONAL_AI_API_HOST": "127.0.0.2", "PERSONAL_AI_API_PORT": "8001"},
        )
        assert cfg.host == "0.0.0.0"
        assert cfg.port == 9999

    def test_env_host_port_used_when_no_cli(self):
        cfg = resolve_config(
            self.make_args(),
            {"PERSONAL_AI_API_HOST": "127.0.0.9", "PERSONAL_AI_API_PORT": "8008"},
        )
        assert cfg.host == "127.0.0.9"
        assert cfg.port == 8008
