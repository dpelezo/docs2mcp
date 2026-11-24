"""Measure docs2mcp's output against ground truth."""

from docs2mcp.evals.extraction import (
    Counts,
    EndpointResult,
    ExtractionScore,
    format_score,
    route_key,
    score,
    score_json,
)
from docs2mcp.evals.reference import (
    OpenApiError,
    Reference,
    ReferenceEndpoint,
    ReferenceParameter,
    ReferenceSource,
    endpoints_from_openapi,
)

__all__ = [
    "Counts",
    "EndpointResult",
    "ExtractionScore",
    "OpenApiError",
    "Reference",
    "ReferenceEndpoint",
    "ReferenceParameter",
    "ReferenceSource",
    "endpoints_from_openapi",
    "format_score",
    "route_key",
    "score",
    "score_json",
]
