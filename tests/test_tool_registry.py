"""Unit tests for the tool registry."""

import pytest

from personal_ai.tools import (
    DuplicateToolError,
    InvalidToolNameError,
    ToolArgumentError,
    ToolDefinition,
    ToolError,
    ToolExecutionError,
    ToolRegistry,
    UnknownToolError,
)


def echo_handler(arguments: dict[str, object]) -> object:
    return arguments


def make_tool(
    name: str = "list_directory",
    description: str | None = None,
    parameters: dict[str, object] | None = None,
    handler: object = None,
) -> ToolDefinition:
    return ToolDefinition(
        name=name,
        description=description if description is not None else f"Tool {name}",
        parameters=parameters if parameters is not None else {"type": "object"},
        handler=handler if handler is not None else echo_handler,
    )


def test_schemas_produce_ollama_function_format() -> None:
    registry = ToolRegistry()
    registry.register(make_tool())

    assert registry.schemas() == [
        {
            "type": "function",
            "function": {
                "name": "list_directory",
                "description": "Tool list_directory",
                "parameters": {"type": "object"},
            },
        }
    ]


def test_schemas_preserve_registration_order() -> None:
    registry = ToolRegistry()
    for name in ("zebra", "alpha", "middle"):
        registry.register(make_tool(name=name))

    names = [schema["function"]["name"] for schema in registry.schemas()]

    assert names == ["zebra", "alpha", "middle"]


def test_schemas_do_not_leak_mutation_into_registry() -> None:
    registry = ToolRegistry()
    registry.register(make_tool())

    schema = registry.schemas()[0]
    schema["type"] = "mutated"
    function = schema["function"]
    assert isinstance(function, dict)
    function["name"] = "renamed"
    parameters = function["parameters"]
    assert isinstance(parameters, dict)
    parameters["type"] = "string"

    fresh = registry.schemas()

    assert fresh == [
        {
            "type": "function",
            "function": {
                "name": "list_directory",
                "description": "Tool list_directory",
                "parameters": {"type": "object"},
            },
        }
    ]


def test_duplicate_names_are_rejected() -> None:
    registry = ToolRegistry()
    registry.register(make_tool())

    with pytest.raises(DuplicateToolError):
        registry.register(make_tool(description="second"))


def test_duplicate_error_is_typed_tool_error() -> None:
    registry = ToolRegistry()
    registry.register(make_tool())

    with pytest.raises(ToolError, match="already registered"):
        registry.register(make_tool())


@pytest.mark.parametrize(
    "name",
    [
        pytest.param("", id="empty"),
        pytest.param("has space", id="space"),
        pytest.param("dot.name", id="dot"),
        pytest.param("slash/name", id="slash"),
        pytest.param("ümlaut", id="non-ascii"),
        pytest.param("eval;", id="semicolon"),
    ],
)
def test_invalid_names_are_rejected(name: str) -> None:
    registry = ToolRegistry()

    with pytest.raises(InvalidToolNameError):
        registry.register(make_tool(name=name))


@pytest.mark.parametrize(
    "name",
    [
        pytest.param("a", id="single-char"),
        pytest.param("list_directory", id="underscore"),
        pytest.param("read-file", id="dash"),
        pytest.param("tool9", id="digits"),
    ],
)
def test_valid_names_are_accepted(name: str) -> None:
    registry = ToolRegistry()

    registry.register(make_tool(name=name))

    assert registry.schemas()[0]["function"]["name"] == name


def test_execute_returns_handler_result() -> None:
    def add(arguments: dict[str, object]) -> object:
        a = arguments["a"]
        b = arguments["b"]
        if isinstance(a, int) and isinstance(b, int):
            return a + b
        msg = "expected ints"
        raise ValueError(msg)

    registry = ToolRegistry()
    registry.register(make_tool(name="add", handler=add))

    assert registry.execute("add", {"a": 2, "b": 3}) == 5


def test_execute_passes_arguments_unchanged() -> None:
    seen: list[dict[str, object]] = []

    def record(arguments: dict[str, object]) -> object:
        seen.append(arguments)
        return None

    registry = ToolRegistry()
    registry.register(make_tool(handler=record))

    registry.execute("list_directory", {"path": "/tmp"})

    assert seen == [{"path": "/tmp"}]


def test_execute_unknown_name_raises_unknown_tool_error() -> None:
    registry = ToolRegistry()
    calls: list[dict[str, object]] = []
    registry.register(make_tool(handler=lambda args: calls.append(args)))

    with pytest.raises(UnknownToolError) as exc_info:
        registry.execute("missing_tool", {})

    assert "missing_tool" in str(exc_info.value)
    assert calls == []


@pytest.mark.parametrize(
    "arguments",
    [
        pytest.param(None, id="none"),
        pytest.param('{"path": "/tmp"}', id="json-string"),
        pytest.param(["/tmp"], id="list"),
        pytest.param(42, id="int"),
    ],
)
def test_execute_non_dict_arguments_raise_argument_error(arguments: object) -> None:
    executed: list[dict[str, object]] = []

    def spy(arguments: dict[str, object]) -> object:
        executed.append(arguments)
        return None

    registry = ToolRegistry()
    registry.register(make_tool(handler=spy))

    with pytest.raises(ToolArgumentError):
        registry.execute("list_directory", arguments)  # type: ignore[arg-type]

    assert executed == []


def test_execute_wraps_handler_failure_in_execution_error() -> None:
    def boom(arguments: dict[str, object]) -> object:
        msg = "disk on fire"
        raise RuntimeError(msg)

    registry = ToolRegistry()
    registry.register(make_tool(name="boom", handler=boom))

    with pytest.raises(ToolExecutionError) as exc_info:
        registry.execute("boom", {})

    assert isinstance(exc_info.value.__cause__, RuntimeError)
    assert "disk on fire" in str(exc_info.value.__cause__)


def test_execute_does_not_call_unregistered_handlers() -> None:
    called: list[str] = []

    def registered(arguments: dict[str, object]) -> object:
        called.append("registered")
        return "ok"

    def unregistered(arguments: dict[str, object]) -> object:
        called.append("unregistered")
        return "nope"

    registry = ToolRegistry()
    registry.register(make_tool(name="registered", handler=registered))

    result = registry.execute("registered", {})

    assert result == "ok"
    assert called == ["registered"]


def test_registries_are_independent() -> None:
    first = ToolRegistry()
    first.register(make_tool())
    second = ToolRegistry()

    assert second.schemas() == []
    with pytest.raises(UnknownToolError):
        second.execute("list_directory", {})
