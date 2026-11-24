"""Records written by ingest and read by extraction."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Page(_Model):
    """One fetched documentation page."""

    url: str = Field(description="Normalised URL the crawler requested.")
    final_url: str = Field(description="URL after redirects.")
    title: str
    fetched_at: datetime
    sha256: str = Field(description="Hash of the raw HTML, used to drop duplicate pages.")


class Chunk(_Model):
    """One section of documentation, small enough to send to the model."""

    id: str = Field(description="Stable identifier, e.g. 'p01-c007'.")
    url: str = Field(description="Page the chunk came from.")
    anchor: str | None = Field(
        default=None, description="HTML id of the chunk's heading, for linking back."
    )
    heading_path: list[str] = Field(description="Headings from the page title down to this chunk.")
    markdown: str
    tokens: int = Field(ge=1, description="Approximate token count (characters / 4).")

    @property
    def source_url(self) -> str:
        return f"{self.url}#{self.anchor}" if self.anchor else self.url


class SkippedUrl(_Model):
    """A URL the crawler saw but did not ingest, and why."""

    url: str
    reason: str
