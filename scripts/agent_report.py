"""Write the agent eval report from a run's results.jsonl (ADR 0013, #51).

    uv run python -m scripts.agent_report runs/full/results.jsonl > report.md

The headline compares every condition on the APIs all of them cover (the three
with an official OpenAPI file). Prices are claude-haiku-5-5's, per million tokens.
"""

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from docs2mcp.evals.agent import load_runs, report

SHARED = ("frankfurter", "pokeapi", "postcodes_io")
HAIKU = (0.10, 0.50)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Write the agent eval report.")
    parser.add_argument("results", type=Path, help="a run's results.jsonl")
    args = parser.parse_args(argv)
    runs = load_runs(args.results)
    if not runs:
        print(f"No runs in {args.results}", file=sys.stderr)
        return 1
    sys.stdout.write(report(runs, SHARED, HAIKU))
    return 0


if __name__ == "__main__":
    sys.exit(main())
