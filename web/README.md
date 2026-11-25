# docs2mcp playground

A static Next.js site that shows the agent-eval results and, for each shared API, the tool lists of all four conditions side by side. It makes no model calls and needs no login or keys.

The only data is `data/demo.json`, written by `scripts/export_demo.py` from the committed eval results. A unit test fails if that file is out of date, so regenerate it after a new eval run:

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

## Deploy

Vercel, with the project's root directory set to `web`. Every page is prerendered at build time.

Open Library and Open-Meteo show results only. Their docs are AGPL-licensed, so their specs and tool designs are not published.
