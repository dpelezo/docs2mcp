"""Typed settings, read from environment variables or a local .env file."""

from functools import lru_cache
from pathlib import Path

from pydantic import AliasChoices, Field, SecretStr
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
        description="Claude model used for extraction.",
    )
    http_timeout_seconds: float = Field(default=30.0, gt=0)
    user_agent: str = "docs2mcp (+https://github.com/dpelezo/docs2mcp)"

    # Ingest
    cache_dir: Path = Field(default=Path(".cache/docs2mcp"), description="Fetched pages.")
    ingest_max_pages: int = Field(default=20, ge=1, description="Pages to crawl per run.")
    crawl_delay_seconds: float = Field(default=0.5, ge=0, description="Pause between fetches.")
    chunk_max_tokens: int = Field(default=1500, ge=200)
    chunk_min_tokens: int = Field(default=80, ge=0)

    # Extract
    extract_batch_tokens: int = Field(default=6000, ge=500, description="Input tokens per call.")
    extract_max_output_tokens: int = Field(
        default=16000, ge=1000, description="Output cap per call; a cut-off reply splits the batch."
    )
    extract_max_retries: int = Field(default=2, ge=0, description="Feedback rounds per batch.")
    extract_prompt_cache: bool = Field(
        default=True, description="Cache the system prompt across extraction calls."
    )

    # Design
    design_model: str = Field(
        default="claude-sonnet-5-5",
        description="Claude model used for tool design (ADR 0012).",
    )
    design_max_output_tokens: int = Field(
        default=32000, ge=1000, description="Output cap for one design reply."
    )
    design_max_retries: int = Field(default=2, ge=0, description="Feedback rounds per design.")

    # Tracing (off unless an exporter is configured; see docs2mcp.tracing)
    langfuse_public_key: str | None = Field(default=None, validation_alias="LANGFUSE_PUBLIC_KEY")
    langfuse_secret_key: SecretStr | None = Field(
        default=None, validation_alias="LANGFUSE_SECRET_KEY"
    )
    langfuse_host: str = Field(
        default="https://cloud.langfuse.com",  # the EU region; US is https://us.cloud.langfuse.com
        validation_alias=AliasChoices("LANGFUSE_BASE_URL", "LANGFUSE_HOST"),
    )
    trace_content: bool = Field(
        default=False, description="Add prompts and model replies to traces."
    )

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
