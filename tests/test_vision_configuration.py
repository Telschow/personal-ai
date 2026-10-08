"""Tests for vision extraction model configuration."""

import pytest

from personal_ai.config import (
    DEFAULT_VISION_PROMPT_VERSION,
    VISION_MODEL_ENV,
    VISION_PROMPT_VERSION_ENV,
    VisionSettings,
    load_vision_settings,
)

CHAT_MODEL = "qwen3.5:9b"


class TestVisionSettings:
    def test_configured_model_is_read_correctly(self) -> None:
        settings = load_vision_settings({VISION_MODEL_ENV: "qwen3.5:9b"})
        assert settings.model == "qwen3.5:9b"
        assert settings.prompt_version == DEFAULT_VISION_PROMPT_VERSION

    def test_missing_configuration_disables_vision(self) -> None:
        assert load_vision_settings({}).model is None

    def test_blank_configuration_is_treated_as_unset(self) -> None:
        assert load_vision_settings({VISION_MODEL_ENV: "   "}).model is None

    def test_chat_model_environment_does_not_leak_into_vision(self) -> None:
        environ = {
            "PERSONAL_AI_CHAT_MODEL": CHAT_MODEL,
            "OLLAMA_MODEL": CHAT_MODEL,
            "MODEL": CHAT_MODEL,
        }
        assert load_vision_settings(environ).model is None

    def test_prompt_version_defaults_to_v2(self) -> None:
        assert load_vision_settings({}).prompt_version == "v2"

    def test_prompt_version_is_read_from_environment(self) -> None:
        settings = load_vision_settings(
            {VISION_MODEL_ENV: "vision-model", VISION_PROMPT_VERSION_ENV: "v2"}
        )
        assert settings.prompt_version == "v2"

    def test_blank_prompt_version_falls_back_to_default(self) -> None:
        settings = load_vision_settings({VISION_PROMPT_VERSION_ENV: "   "})
        assert settings.prompt_version == DEFAULT_VISION_PROMPT_VERSION

    def test_real_environment_is_used_by_default(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(VISION_MODEL_ENV, "qwen3.5:9b")
        assert load_vision_settings().model == "qwen3.5:9b"
        monkeypatch.delenv(VISION_MODEL_ENV)
        assert load_vision_settings().model is None

    def test_settings_value_object_remains_a_plain_dataclass(self) -> None:
        assert VisionSettings(model=None).model is None
        assert VisionSettings(model="m").prompt_version == "v2"
