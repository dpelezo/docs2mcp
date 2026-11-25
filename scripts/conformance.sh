#!/usr/bin/env bash
# Run the official MCP conformance suite against a generated server (#31).
#
# Generates the Frankfurter server from its committed design (every tool approved,
# in a temporary copy), serves it over streamable HTTP, and runs the suite with the
# baseline of failures expected by design. No model calls; the suite never calls the
# Frankfurter API. Needs uv and npx; Node 22 is fetched by npx for this run only.
set -euo pipefail

SUITE="@modelcontextprotocol/conformance@0.1.16"
PORT="${PORT:-8765}"
WORK="$(mktemp -d)"
trap 'kill "${SERVER_PID:-0}" 2>/dev/null || true; rm -rf "$WORK"' EXIT

cp evals/design/replay/frankfurter/tools.json "$WORK/tools.json"
uv run docs2mcp approve "$WORK/tools.json" --all --note "conformance run" >/dev/null
uv run docs2mcp generate "$WORK/tools.json" \
  --spec tests/fixtures/specs/frankfurter.json --out "$WORK/server" >/dev/null

uv run --quiet --directory "$WORK/server" server.py --http --port "$PORT" &
SERVER_PID=$!
for _ in $(seq 1 120); do
  if (echo >"/dev/tcp/127.0.0.1/$PORT") 2>/dev/null; then break; fi
  sleep 0.5
done

npx --yes -p node@22 -p "$SUITE" conformance server \
  --url "http://127.0.0.1:$PORT/mcp" \
  --expected-failures evals/conformance/baseline.yml
