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

# Unit tests with coverage (no network)
test:
    uv run pytest -m unit

# Everything CI runs; must pass before every commit
check: lint typecheck test
