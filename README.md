# TUM Lecture Finder

![page screenshot](media/page_thumbnail.png)
I was a bit sick of the TUM official module search. It only allows one to search titles of courses, not their descriptions. It also doesn't do any semantic matching, only exact word matches.

So I - or rather an LLM under my supervision - built this. I am fully aware that this is therefore not 'my' finest code, but I could not have built this in two days without, and frankly, that's all the motivation I had spare.

That said, what is present works pretty well in my testing. If anything breaks in your testing please leave an issue!

Course data is pulled from the public (but undocumented) TUMonline REST API, stored in a local SQLite database, and searchable via BM25 full-text search, sentence-transformer embeddings, or a hybrid of both (which is standard). A FastAPI web UI is included alongside the CLI.

> [!CAUTION]
> The majority of this codebase was written by **Claude Opus 4.6** (via GitHub Copilot) with limited human supervision. While tests pass and the tool functions, the code should be treated with appropriate skepticism. Review any module carefully before relying on it for anything beyond personal use.

## Status

Working for personal use. Indexes ~24 000 courses across the 4 most recent semesters in roughly 15 minutes on first run. Subsequent updates are incremental. Search latency: ~10 ms keyword, ~80 ms hybrid (warm).

> [!Warning]
> Be aware that building the local database sends tens of thousands of requests to the TUM API. Please take caution when deploying this tool locally so the API doesn't become private in the future.

## Features

- **Keyword search** — SQLite FTS5 with BM25 weights (title ranked 10×, description 2×, objectives 1×)
- **Semantic search** — `all-MiniLM-L6-v2` embeddings cached to disk
- **Hybrid mode** — FTS + semantic score fusion
- **Filters** — campus, course type (VO/UE/SE/…), semester
- **Campus resolution** — building codes resolved via the NavigaTUM API, no hardcoded mappings
- **Cross-semester dedup** — courses with the same identity across semesters are grouped; a frequency badge indicates if a course runs every semester, yearly, or is a one-off
- **Web UI** — search page, course detail with live schedule, stats page
- **CLI** — `tlf update` / `tlf search` / `tlf serve`

## Installation

Requires Python ≥ 3.12.

```bash
git clone https://github.com/HB-Stratos/tum-lecture-finder.git
cd TumLectureFinder
pip install -e .
```

## Usage

```bash
# Fetch / update the local course database (last 4 semesters)
tlf update

# Fetch specific semesters only
tlf update -s 25S -s 25W

# Full-text search
tlf search "PCB design"

# With filters
tlf search "machine learning" --campus garching --type VO

# Start the web UI (default: http://127.0.0.1:8000)
tlf serve
```

> [!NOTE]
> The first `tlf update` also downloads the embedding model (~90 MB) and computes embeddings for all courses. This takes a while. Everything is cached locally afterward.

## Architecture

```
src/tum_lecture_finder/
├── cli.py        # Click entry-point
├── config.py     # Paths and constants
├── fetcher.py    # Async HTTP (httpx) — TUMonline + NavigaTUM API
├── models.py     # Dataclasses / TypedDicts
├── storage.py    # SQLite + FTS5 persistence (schema v7)
├── search.py     # Keyword / semantic / hybrid search
└── web.py        # FastAPI application
    templates/    # Jinja2 HTML templates
    static/       # JS and CSS
```

Business logic lives outside `cli.py` so the web UI reuses it directly. The fetcher knows nothing about storage; search knows nothing about fetching.

## Data Source

All data comes from the public, unauthenticated TUMonline (CAMPUSonline) REST API. See [`docs/api.md`](docs/api.md) for the reverse-engineered endpoint details.

## Development

```bash
# Run tests
pytest tests/

# Lint
ruff check src/ tests/
```

263 tests, ruff clean (all rules enabled).

> [!IMPORTANT]
> Given the AI authorship, treat any untested code path as unreviewed. The test suite covers the happy path well but edge cases may not be handled correctly. If something breaks in an unexpected way, read the relevant module rather than assuming the architecture is sound.
