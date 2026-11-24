"""Extract: turn ingested chunks into a validated ApiSpec with Claude.

batch chunks -> Claude fills WireBatch -> validate -> feedback and retry -> merge -> ApiSpec.
See docs/adr/0004-extraction.md.
"""

from docs2mcp.extract.extractor import BatchLog, ExtractionReport, Extractor, make_batches
from docs2mcp.extract.model import (
    AnthropicModel,
    ExtractionModel,
    ModelOutputError,
    ModelReply,
    RecordingModel,
    ReplayModel,
    Turn,
)

__all__ = [
    "AnthropicModel",
    "BatchLog",
    "ExtractionModel",
    "ExtractionReport",
    "Extractor",
    "ModelOutputError",
    "ModelReply",
    "RecordingModel",
    "ReplayModel",
    "Turn",
    "make_batches",
]
