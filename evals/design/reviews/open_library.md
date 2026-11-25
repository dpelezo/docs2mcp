# Design review: Open Library (held out)

- **API:** Open Library (11 endpoints). Held out: the design prompt is never changed because of this review.
- **tools.json:** Sonnet 5.5 run 1 on the owner's machine (`tools/open_library/v2/sonnet/run1/`); not committed, because the docs are AGPL
- **Reviewer and date:** drafted by Claude on 2026-11-24; confirmed by the owner (dpelezo) on 2026-11-24, all answers as drafted

## Each tool

1. **When to use it: pass.** There are 10 tools:
   - search: `search_books`, `search_authors`;
   - a work and its parts: `work`, `work_editions`, `work_bookshelves`, `work_ratings`;
   - `edition`;
   - authors: `author`, `author_works`;
   - the legacy bibkeys lookup.
2. **What to pass: fail.** The `books_by_bibkeys` description suggests `jscmd=details`, but the argument's enum only allows `viewapi` and `data`. That claim comes from the spec's own description, which disagrees with its enum. Either way, an agent following the description would be rejected.
3. **One task: pass.** `edition` combines lookup by edition id and by ISBN, which is the same item by different keys. The four work tools stay separate because they take the same argument, and one-call routes are chosen by which arguments are present (ADR 0011).
4. **Route choice: pass.**

## The whole design

5. **No overlap: pass.**
6. **Exclusions: pass.** All 22 of 22 parameters are exposed.
7. **Faithful: fail (minor).** Some descriptions add details the spec does not state:
   - `work`: "the abstract book, across editions";
   - `work_editions`: "publishers and ISBNs";
   - `search_authors`: that results include author keys.

## Verdict

- **Fit to approve:** no, until questions 2 and 7 pass.
- **Failures to fix first:**
  - remove the `jscmd=details` hint, and fix the spec's description or enum in extraction;
  - trim the extra claims.
- **Notes:** runs 2 and 3 needed one retry each. Claude first tried to put all the work endpoints in one tool with flag arguments, which the routing model cannot express. Recorded as held-out behaviour, not tuned against.
