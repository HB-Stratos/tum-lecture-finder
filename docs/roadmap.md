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
- Model cached on disk; noisy HF/safetensors output suppressed.

### Description snippets

- When a search matches a description rather than the title, a relevant
  excerpt is shown in the results table.

### Campus filtering

- Org-name keyword heuristic (~50 patterns across 6 campuses).
- `--campus garching`, `--campus muenchen`, etc.

---

## Planned / Ideas

### Room-based campus detection

**Status:** blocked (no public API for room data)

The web UI shows room numbers and locations for each course, and
NavigaTUM can map room codes to buildings and campuses. However, the
public REST API does not expose room/scheduling data. The old
webservice endpoint requires an auth token.

**Options if this becomes unblocked:**

1. If TUM adds room data to the public endpoint → parse and match via
   NavigaTUM.
2. Scrape the rendered web UI (fragile, JS-heavy).
3. Use an authenticated session (not public-friendly).

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

### Course grouping / deduplication across semesters

**Status:** idea

The same course (e.g. "Requirements Engineering") appears in multiple
semesters with different `course_id` values. The detail endpoint
returns `sameCourseDtos` listing sibling offerings. Search results
currently show all of them separately.

**Possible approaches:**

- Group by `courseNumber` and show only the most recent offering.
- Use `sameCourseDtos` to build a course-family graph.
- Add a `--semester` filter to `search` so users see only one semester.

### Search improvements

**Status:** idea

- **Exact-match boosting:** give higher weight when the query matches a
  title exactly, not just via prefix.
- **German ↔ English:** if someone searches in English, also check the
  German title and vice versa (currently both are indexed but a single
  query may not find the translation).
- **Semester filter on search:** only show courses from a specific
  semester.
- **Result caching:** cache semantic embeddings for all courses so
  repeated semantic searches are instant.

### Web / GUI frontend

**Status:** idea

Business logic lives outside `cli.py` by design. A FastAPI or
Streamlit frontend could reuse `storage.py` and `search.py` directly.

### Course schedule / calendar integration

**Status:** blocked (same as room data — no public API)

Appointment times are visible on the web but not in the REST response.
Would require auth or scraping.

### ECTS / credit information

**Status:** idea — check if it's in API

The API returns `courseNormConfigs` with `key=SST` (SWS). Investigate
whether ECTS credits appear under a different key.

### Exam information

**Status:** idea

The detail endpoint has a `displayExamInformation` flag. There may be
a related endpoint for exam registrations or dates.
