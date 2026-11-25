# Run `just` to list commands.
default:
    @just --list

# Install dependencies and the pre-commit hooks
setup:
    uv sync
    uv run pre-commit install

# Lint and check formatting
lint:
    uv run ruff check .
    uv run ruff format --check .

# Fix lint issues and format
fmt:
    uv run ruff check --fix .
    uv run ruff format .

# Type check (Pyright strict)
typecheck:
    uv run pyright

# Unit tests with coverage (no network); fails under 80% coverage
test:
    uv run pytest -m unit --cov-fail-under=80

# Live tests against real documentation sites (needs network)
integration:
    uv run pytest -m integration --no-cov

# Regenerate docs/spec.schema.json and docs/tools.schema.json after changing the models
schema:
    uv run docs2mcp schema > docs/spec.schema.json
    uv run docs2mcp schema --tools > docs/tools.schema.json

# Record live extraction on the committed snapshots, for the CI replay test (needs ANTHROPIC_API_KEY)
record-replay *ids:
    uv run python scripts/record_replay.py {{ids}}

# Rebuild evals/extraction/reference/ from the APIs' OpenAPI files (needs network)
references *ids:
    uv run python scripts/reference_from_openapi.py {{ids}}

# Run the MCP conformance suite on a generated server (needs npx; no model calls)
conformance:
    ./scripts/conformance.sh

# Everything CI runs; must pass before every commit
check: lint typecheck test
