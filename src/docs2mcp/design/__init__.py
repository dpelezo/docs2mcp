"""Turn an API spec into the tools a generated MCP server exposes (ADR 0011)."""

from docs2mcp.design.convert import Repairs, convert, repair
from docs2mcp.design.designer import Attempt, Designer, DesignReport
from docs2mcp.design.direct import direct_design, direct_tool, safe_name
from docs2mcp.design.model import (
    AnthropicDesignModel,
    DesignModel,
    DesignReply,
    RecordingDesignModel,
    ReplayDesignModel,
)
from docs2mcp.design.toolset import (
    ExcludedEndpoint,
    Route,
    Tool,
    ToolParameter,
    ToolSet,
    json_schema,
)
from docs2mcp.design.validate import tool_errors, validate_toolset
from docs2mcp.design.wire import WireDesign

__all__ = [
    "AnthropicDesignModel",
    "Attempt",
    "DesignModel",
    "DesignReply",
    "DesignReport",
    "Designer",
    "ExcludedEndpoint",
    "RecordingDesignModel",
    "Repairs",
    "ReplayDesignModel",
    "Route",
    "Tool",
    "ToolParameter",
    "ToolSet",
    "WireDesign",
    "convert",
    "direct_design",
    "direct_tool",
    "json_schema",
    "repair",
    "safe_name",
    "tool_errors",
    "validate_toolset",
]
