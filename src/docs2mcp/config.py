"""Typed settings, read from environment variables or a local .env file."""

from functools import lru_cache

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """All runtime settings. Names are documented in .env.example."""

    model_config = SettingsConfigDict(
        env_prefix="DOCS2MCP_",
        env_file=".env",
        extra="ignore",
        frozen=True,
    )

    anthropic_api_key: SecretStr | None = Field(
        default=None,
        validation_alias="ANTHROPIC_API_KEY",
        description="Needed only by steps that call a model (extraction, design, evals).",
    )
    model: str = Field(
        default="claude-haiku-5-5",
        description="Claude model used for extraction and design.",
    )
    http_timeout_seconds: float = Field(default=30.0, gt=0)
    user_agent: str = "docs2mcp (+https://github.com/dpelezo/docs2mcp)"

    def require_anthropic_key(self) -> str:
        """Return the API key, or fail with a clear message if it is missing."""
        if self.anthropic_api_key is None:
            msg = "ANTHROPIC_API_KEY is not set. Add it to your environment or .env file."
            raise RuntimeError(msg)
        return self.anthropic_api_key.get_secret_value()


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Load settings once per process."""
    return Settings()
