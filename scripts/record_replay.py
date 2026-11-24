"""Record extraction on the committed docs snapshots, for the CI replay test.

Needs ANTHROPIC_API_KEY (live calls to Claude), but no access to the docs sites:
the snapshots in tests/fixtures/docs/ are ingested offline.

    uv run python scripts/record_replay.py                     # every API with snapshots
    uv run python scripts/record_replay.py frankfurter         # just one

For each API it writes, under --out (default specs/replay, which git ignores):

    <id>/ingest/        chunks from the snapshots
    <id>/spec.json      the extracted spec, and extraction_report.json
    <id>/recordings/    Claude's replies, keyed by request
    <id>/score.json     the score against evals/extraction/reference/<id>.json

Commit <id>/recordings/ and <id>/score.json to evals/extraction/replay/<id>/ in a
pull request. tests/unit/test_extraction_replay.py then replays them in CI.
"""

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from docs2mcp import cli
from docs2mcp.config import Settings
from docs2mcp.evals.snapshots import ingest_snapshots, load_api

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures" / "docs"
SOURCES = FIXTURES / "sources.toml"
REFERENCES = ROOT / "evals" / "extraction" / "reference"
REPLAYABLE = ("pokeapi", "frankfurter", "postcodes_io")


def record(api_id: str, out: Path) -> int:
    api = load_api(SOURCES, api_id)
    folder = out / api_id
    print(f"===== {api_id}")
    # Default settings, not .env, so the chunks match what the replay test produces.
    result = ingest_snapshots(
        api_id,
        folder / "ingest",
        sources=SOURCES,
        fixtures=FIXTURES,
        settings=Settings(_env_file=None),  # pyright: ignore[reportCallIssue]
    )
    print(f"{len(result.pages)} pages -> {len(result.chunks)} chunks from the snapshots")
    spec = folder / "spec.json"
    code = cli.main(
        [
            "extract",
            str(folder / "ingest"),
            "--out",
            str(spec),
            "--base-url",
            str(api["base_url"]),
            "--record",
            str(folder / "recordings"),
        ]
    )
    if code != 0:
        return code
    reference = REFERENCES / f"{api_id}.json"
    return cli.main(
        ["score", str(spec), "--reference", str(reference), "--out", str(folder / "score.json")]
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument("apis", nargs="*", choices=REPLAYABLE, help="default: all")
    parser.add_argument("--out", type=Path, default=ROOT / "specs" / "replay")
    args = parser.parse_args(sys.argv[1:] if argv is None else argv)
    for api_id in args.apis or REPLAYABLE:
        code = record(api_id, args.out)
        if code != 0:
            return code
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
