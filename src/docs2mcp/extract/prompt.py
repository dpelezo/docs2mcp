"""Prompts for extraction. Changing them changes results: re-run the extraction report."""

from collections.abc import Sequence

from docs2mcp.ingest.models import Chunk

SYSTEM = """\
You read API documentation and record every HTTP endpoint it documents, exactly as written.

Rules:
- Record only endpoints the documentation actually shows. Never invent endpoints, \
parameters or values.
- path: the path after the base URL, starting with "/". Write path parameters as {name}, and \
give every placeholder a parameter with location "path" and required true. Convert :name or \
<name> to {name}.
- base_urls: the API base URLs shown in these chunks (scheme, host and any version prefix such \
as /v2 or /api/v2). Leave it empty if none is shown.
- If one endpoint accepts several alternatives (for example "id or name"), record it once with \
a single descriptive placeholder.
- default_value: a JSON literal ("10", "\\"celsius\\"", "true"), or empty if the docs give no \
default. allowed_values: only values the docs list explicitly.
- Keep summary and description to one short sentence each. Do not copy response examples.
- source_chunk_id: the id of the chunk where the endpoint is documented.
- served_from: the base URL the docs show for this endpoint (scheme, host and any version \
path), copied from its example URLs. It is how out-of-scope endpoints are found, so do not \
normalise it to the API's main base URL: an image host, a different API version or a \
different service keeps its own URL. When the docs give only a relative path, use the \
API's base URL.
- same_parameters_as: when the docs say this endpoint accepts the same parameters as other \
endpoints ("supports the same parameters as /v2/rates", "takes the same filters as search"), \
list those endpoints' paths as written. Still list any parameters documented for this endpoint \
itself; the shared ones are copied in code. Use an empty list otherwise.
- for_every_resource: true only when the docs describe one generic route that works for every \
resource of the API (for example "calling any endpoint without an id returns a list: \
GET /{endpoint}/"). Record such a route once, with the resource as its first placeholder; it is \
expanded in code. A placeholder that holds a value (a date, an id, a code) is not a resource: \
use false.
- If the chunks document no endpoints (an introduction, a FAQ, a changelog), return an empty \
endpoints list.
"""


def batch_message(chunks: Sequence[Chunk]) -> str:
    """The user message for one batch: each chunk with its id, heading path and source."""
    parts = [
        f'<chunk id="{chunk.id}" source="{chunk.source_url}">\n'
        f"<heading_path>{' > '.join(chunk.heading_path)}</heading_path>\n"
        f"{chunk.markdown}\n</chunk>"
        for chunk in chunks
    ]
    return (
        "Record every endpoint documented in these chunks of API documentation.\n\n"
        + "\n\n".join(parts)
    )


def feedback_message(errors: Sequence[str]) -> str:
    """Ask Claude to fix the endpoints that failed validation, keeping the others."""
    listed = "\n".join(f"- {error}" for error in errors)
    return (
        "Some endpoints you returned are inconsistent:\n"
        f"{listed}\n\n"
        "Return the complete output for these chunks again, with those endpoints corrected "
        "and every other endpoint unchanged."
    )
