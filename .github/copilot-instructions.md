# TUM Lecture Finder — Coding Standards & Project Requirements

## Overview

TUM Lecture Finder is a CLI tool for full-text and semantic search across all
academic courses offered at TU Munich. It fetches course data from the public
TUMonline (CAMPUSonline) REST API, stores it locally, and provides fast
full-text + fuzzy/semantic search with filtering by discipline and campus.

## Data Source

- **Public REST endpoint** (no auth required):
  `https://campus.tum.de/tumonline/ee/rest/slc.tm.cp/student/courses`
- Course list: paginated via `$skip` / `$top`, filtered by
  `$filter=termId-eq=<semesterId>` (matrix-param style — see
  `docs/api.md` for the full reverse-engineering story)
- Semesters list:
  `https://campus.tum.de/tumonline/ee/rest/slc.lib.tm/semesters/student`
- Course detail: `…/courses/{id}` — includes descriptions, objectives, etc.
- ~6 500 courses per semester; default update fetches 4 semesters (~24 000)

## Architecture

```
src/tum_lecture_finder/
├── __init__.py          # package root
├── cli.py               # Click CLI entry-point
├── config.py            # paths, constants
├── fetcher.py           # async HTTP fetcher (httpx)
├── models.py            # dataclasses / typed dicts
├── storage.py           # SQLite + FTS5 persistence
└── search.py            # full-text + semantic search
```

## Coding Standards

| Area               | Rule                                                |
| ------------------ | --------------------------------------------------- |
| Formatter / Linter | **ruff** — all rules enabled (see `pyproject.toml`) |
| Line length        | 99                                                  |
| Python version     | ≥ 3.12                                              |
| Type hints         | Required on every public function                   |
| Docstrings         | Google-style, required on every public symbol       |
| Imports            | Sorted by ruff (isort-compatible)                   |
| Error handling     | Let exceptions propagate; handle at CLI boundary    |
| Async              | Use `httpx.AsyncClient` for I/O; `asyncio.run()`    |
| Data classes       | Prefer `dataclasses.dataclass` or `TypedDict`       |
| Testing            | `pytest`; tests live in `tests/`                    |

## Key Conventions

1. **Modularity** — each module has a single responsibility; the CLI wires them
   together.
2. **Separation of concerns** — fetcher knows nothing about storage; search
   knows nothing about fetching.
3. **Local-first** — all searches run against a local SQLite database; network
   is only needed for `update`.
4. **Idempotent update** — re-running `update` replaces stale data; never
   duplicates.
5. **CLI-first, UI-ready** — business logic lives outside `cli.py` so a future
   web/GUI frontend can reuse it.

## TUM Campuses

Campus labels are resolved dynamically via the TUM NavigaTUM API at
`nav.tum.de/api/search?q=<building_code>`. No hardcoded mappings exist.
Common labels returned by NavigaTUM:

| Label                    | Typical locations                  |
| ------------------------ | ---------------------------------- |
| stammgelände             | Main Munich campus                 |
| garching                 | Garching research campus           |
| garching-hochbrück       | Garching-Hochbrück Business Campus |
| weihenstephan            | Freising / Weihenstephan           |
| campus-heilbronn         | Heilbronn campus                   |
| campus-straubing-…       | Straubing TUMCS                    |
| campus-im-olympiapark-sz | Sport campus Munich                |
| marsstraße 20, 21, 22    | Munich Marsstraße                  |
| taufkirchen-ottobr.      | Ottobrunn / Taufkirchen            |

Campus filtering uses substring matching: `--campus garching` matches
both `garching` and `garching-hochbrück`.

## Build & Run

```bash
# Install (editable)
pip install -e .

# Fetch / update local course database (last 4 semesters)
tlf update

# Fetch specific semesters
tlf update -s 25S -s 25W

# Search
tlf search "PCB design"

# With filters
tlf search "machine learning" --campus garching --type VO
```
