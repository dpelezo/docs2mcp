# ADR 0003: How ingest finds, cleans and chunks documentation

- **Status:** Accepted
- **Date:** 2026-11-23

## Context

Extraction (#5) sends documentation to Claude and asks for an `ApiSpec`. It can only find endpoints that ingest gives it, and it reads them better when each piece is small, free of page furniture, and labelled with what it describes. The five eval APIs differ a lot:

- **PokéAPI:** the whole v2 reference is one 289 KB page, with 200 headings and 126 field tables.
- **Open-Meteo:** headings are wrapped in links, and the query parameters are listed as labelled form fields and in tables.
- **Open Library:** docs are split across sibling pages (`/dev/docs/api/search`, `/books`, `/authors`).
- **Frankfurter and REST Countries:** a single short page each.

## Decisions

### Crawl

- **Scope:** start from one or more seed URLs and follow links on the same host at or below each seed's path. Several seeds cover docs in sibling folders without wandering into a blog or product pages.
- **Hygiene:** skip assets, query strings and fragments; respect `robots.txt` (a missing or unreachable file allows everything); drop pages with identical HTML; stop at 20 pages by default.
- **Cache:** fetched pages are stored on disk, so re-runs do not refetch and produce identical output. Failed fetches are not cached. `--refresh` refetches.
- **No JavaScript.** All five eval APIs serve their docs as plain HTML. Sites that only render in a browser are out of scope for v1.

### Clean and convert

- **Keep the documentation part of the page:** the first `<main>`, `<article>` or `role="main"` element, falling back to the whole page. All five sites use `<main>`, and it holds 99–100% of their documentation headings; the headings outside it are footers. Navigation, asides, headers, footers and buttons inside `<main>` are removed too. Forms are kept, because Open-Meteo lists parameters in them.
- **Convert with `html-to-markdown`** (MIT, compiled, no Python dependencies), with compact tables, absolute links, plain highlights and no images. Compared with `markdownify` on the same pages, it:
  - kept all 18 Open-Meteo headings where markdownify kept 3 (markdownify emits `[## Heading](#id)` for a heading inside a link, which is no longer a heading);
  - did not escape underscores, while markdownify turns `temperature_2m` into `temperature\_2m`;
  - dropped in-page tables of contents;
  - reports each heading's HTML `id`, which becomes the chunk anchor;
  - was about 10× faster.

  The cost is a 22 MB install instead of about 0.5 MB.

### Chunk

- **One chunk per heading section,** labelled with its heading path (page title › section › subsection) and its heading's anchor, so every extracted endpoint can link back to its source (`source_url`).
- **Size:** at most 1,500 tokens (estimated as characters / 4). Longer sections split between paragraphs, lists, tables and code blocks:
  - Code blocks are never cut. PokéAPI's 3,857-token example response stays whole.
  - Tables that are still too long split between rows, repeating the header row. The text before a table stays with its first rows, because it says what the table is ("The parameter `&hourly=` accepts the following values").
- **Small sections** (under 80 tokens, often a heading with no text of its own) join the next section, or the previous one at the end of a page. The merged chunk is labelled honestly: a parent and its child keep the child's path; siblings get the parent they share (Frankfurter's "Time Series" and "Grouping" become one chunk under "Rates").
- **Output:** `pages.jsonl` and `chunks.jsonl`, with sorted keys and stable IDs (`p01-c007`). The same input always produces the same bytes.

## Consequences

- Chunk quality is measured for real in #5: if ingest drops or mangles a section, extraction recall for that API falls.
- Sites without `<main>`, or with docs behind JavaScript, will need work when we meet one.
- Token counts are estimates. Extraction should leave headroom rather than fill the model's context exactly.
