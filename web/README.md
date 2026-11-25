# docs2mcp playground

A Next.js site that shows the agent-eval results and, for each shared API, the tool lists of all four conditions side by side. Each API page can also run a tool live (#74): `api/call.py`, a Vercel Python function, calls the generated B or C server over MCP, in process, against the real API. Nothing calls a model, and nothing needs a login or a key.

The data comes from `scripts/export_demo.py`. It writes `data/demo.json` from the committed eval results, and `api/_servers/` (the generated servers and a manifest of their GET-only tools) from the committed specs and designs. Unit tests fail if either is out of date, so regenerate after changing the generator or the eval inputs:

```bash
uv run python scripts/export_demo.py
```

## Run locally

```bash
cd web
npm ci
npm run dev          # http://localhost:3000
npm run lint && npm run typecheck && npm run build
```

To try live calls locally, run the function with the repo's Python environment and point the site at it. The rewrite is read at build time, so set it for the build too:

```bash
uv run uvicorn --app-dir web/api call:app --port 8001          # from the repo root
cd web && LIVE_API_URL=http://127.0.0.1:8001 npm run build && npm start
```

## Live calls: limits

- Only PokéAPI, Frankfurter and Postcodes.io, conditions B and C, and only tools whose every route is a GET (`api/_servers/manifest.json`).
- Arguments must be strings, numbers or booleans, up to 16 of them, and strings up to 200 characters. The body can be 4 KB at most.
- 10 calls a minute per visitor and 120 per instance. Each instance counts on its own, so these are best-effort limits.
- 10 s upstream timeout, and the generated server's 20,000-character response limit.
- Errors never include a traceback.

## Deploy

Vercel, with the project's root directory set to `web`. Every page is prerendered at build time. `api/call.py` runs on Python 3.14 (`.python-version`) with the pinned packages in `requirements.txt`. `vercel.json` keeps the site's files out of the function's bundle.

Open Library and Open-Meteo show results only. Their docs are AGPL-licensed, so their specs and tool designs are not published.
