# TUM Lecture Finder — Roadmap & Ideas

Status legend: done, current, planned, blocked, idea

---

## Done

### Multi-semester fetching

- Reverse-engineered the `$filter` matrix-param format from the Angular
  SPA webpack bundles.
- `tlf update` now fetches the 4 most recent semesters by default
  (~24 000 courses).
- `--semester 25S --semester 25W` picks specific semesters.
- `--recent N` controls how many recent semesters to fetch.

### Full-text search (FTS5 + BM25 weights)

- SQLite FTS5 with 11 weighted columns (titles 10×, descriptions 2×,
  objectives 1×, org/instructors/literature 0.5×).
- Prefix matching (`token*`) for partial-word queries.
- Course number indexed and searchable.

### Semantic search

- sentence-transformers `all-MiniLM-L6-v2` for meaning-based matching.
- Hybrid mode combines FTS + semantic scores.
- Pre-computed embeddings cached to disk (.npz).
- Model cached on disk; noisy HF/safetensors output suppressed.

### Description snippets

- When a search matches a description rather than the title, a relevant
  excerpt is shown in the results table.

### Campus detection (NavigaTUM-based)

- Building codes extracted from room data in courseGroups API responses.
- Each building code resolved via TUM NavigaTUM API
  (`nav.tum.de/api/search?q=<code>`).
- Campus labels parsed from room subtexts — no hardcoded mappings.
- Building cache persisted in SQLite to avoid redundant API calls.
- Substring campus filter: `--campus garching` matches
  `garching`, `garching-hochbrück`, etc.

### Cross-semester deduplication

- Courses sharing the same `identity_code_id` are grouped; search
  results show the best-scoring instance with "Also in: 25S, 24W" etc.

### Web UI (FastAPI)

- FastAPI application with Jinja2 templates and static assets.
- Search page with keyword/semantic/hybrid modes and filter dropdowns.
- Course detail pages with TUMonline link and live schedule loading.
- Stats page with semester, type, and campus distributions.
- JSON API (`/api/search`, `/api/course/{id}`, `/api/stats`,
  `/api/filters`, `/api/course/{id}/schedule`).
- Security headers (CSP, X-Frame-Options, etc.), rate limiting.
- `tlf serve` command to start the web server.

### Course schedule / appointments

- courseGroups API returns appointment and room data for most courses.
- Live schedule fetched on course detail page via
  `/api/course/{id}/schedule`.

---

## Planned / Ideas

### Server-side filtering via `$filter`

**Status:** idea

The API supports `courseTypeKey-eq=VO` and `filterTerm-like=...`
server-side. We could push basic filters to the server to reduce
download size during `update`. Not a priority since local FTS is fast.

### Additional `$filter` fields to explore

**Status:** idea

The Angular bundle references more filter fields that we haven't
tested:

- `orgId` — filter by organisation / department id
- `categoryElementId` — unknown, possibly study programme
- `institutions` — possibly related to org/faculty

These could enable server-side campus or programme filtering.

### Search improvements

**Status:** idea

- **Exact-match boosting:** give higher weight when the query matches a
  title exactly, not just via prefix.
- **German ↔ English cross-language:** boost results where a query in
  one language matches the other language field.
- **Campus display names:** NavigaTUM labels like
  `campus-straubing-cs-biotechnologie-und-nachhaltigkeit` are unwieldy.
  Add a display-name mapping for the UI.
- **Campus grouping:** present sub-campuses (garching, garching-hochbrück)
  under a parent "Garching area" group in filter dropdowns.

### ECTS / credit information

**Status:** idea — check if it's in API

The API returns `courseNormConfigs` with `key=SST` (SWS). Investigate
whether ECTS credits appear under a different key.

### Exam information

**Status:** idea

The detail endpoint has a `displayExamInformation` flag. There may be
a related endpoint for exam registrations or dates.
