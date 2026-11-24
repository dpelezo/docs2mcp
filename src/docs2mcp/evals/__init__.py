"""Measure docs2mcp's output against ground truth."""

from docs2mcp.evals.design import (
    DesignMetrics,
    ToolMetrics,
    format_metrics,
    input_schema,
    listed_tool,
    measure_design,
    metrics_json,
)
from docs2mcp.evals.extraction import (
    Counts,
    EndpointResult,
    ExtractionScore,
    format_score,
    score,
    score_json,
)
from docs2mcp.evals.reference import (
    AdditionError,
    OpenApiError,
    ParameterAddition,
    Reference,
    ReferenceAdditions,
    ReferenceEndpoint,
    ReferenceParameter,
    ReferenceSource,
    apply_additions,
    endpoints_from_openapi,
    route_key,
)

__all__ = [
    "AdditionError",
    "Counts",
    "DesignMetrics",
    "EndpointResult",
    "ExtractionScore",
    "OpenApiError",
    "ParameterAddition",
    "Reference",
    "ReferenceAdditions",
    "ReferenceEndpoint",
    "ReferenceParameter",
    "ReferenceSource",
    "ToolMetrics",
    "apply_additions",
    "endpoints_from_openapi",
    "format_metrics",
    "format_score",
    "input_schema",
    "listed_tool",
    "measure_design",
    "metrics_json",
    "route_key",
    "score",
    "score_json",
]
