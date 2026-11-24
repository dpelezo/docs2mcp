"""Ingest: turn documentation URLs into clean, labelled Markdown chunks.

crawl -> cache -> clean (keep <main>) -> convert to Markdown -> chunk on headings.
See docs/adr/0003-ingest-pipeline.md.
"""

from docs2mcp.ingest.models import Chunk, Page, SkippedUrl
from docs2mcp.ingest.pipeline import IngestResult, ingest

__all__ = ["Chunk", "IngestResult", "Page", "SkippedUrl", "ingest"]
