import pytest

from docs2mcp.config import Settings


def test_settings_read_prefixed_environment_variables(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DOCS2MCP_MODEL", "claude-sonnet-5-5")
    monkeypatch.setenv("DOCS2MCP_HTTP_TIMEOUT_SECONDS", "5")
    settings = Settings(_env_file=None)  # pyright: ignore[reportCallIssue]
    assert settings.model == "claude-sonnet-5-5"
    assert settings.http_timeout_seconds == 5.0


def test_api_key_is_never_shown_in_repr(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-secret")
    settings = Settings(_env_file=None)  # pyright: ignore[reportCallIssue]
    assert "sk-ant-test-secret" not in repr(settings)
    assert settings.require_anthropic_key() == "sk-ant-test-secret"


def test_missing_api_key_fails_with_a_clear_message(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    settings = Settings(_env_file=None)  # pyright: ignore[reportCallIssue]
    with pytest.raises(RuntimeError, match="ANTHROPIC_API_KEY is not set"):
        settings.require_anthropic_key()


def test_invalid_timeout_is_rejected_at_startup(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DOCS2MCP_HTTP_TIMEOUT_SECONDS", "0")
    with pytest.raises(ValueError, match="greater than 0"):
        Settings(_env_file=None)  # pyright: ignore[reportCallIssue]
