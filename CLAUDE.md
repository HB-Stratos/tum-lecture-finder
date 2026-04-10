# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

TUM Lecture Finder is a CLI + web UI for full-text and semantic search across TU Munich's ~24,000 courses. It fetches from the public TUMonline API, stores in local SQLite, and supports BM25 (FTS5), neural (sentence-transformers), and hybrid search.

> **Note:** Most of this codebase was written by Claude Opus 4.6 via GitHub Copilot with limited human supervision. Treat untested code paths as unreviewed.

## Commands

```bash
uv sync                   # install
uv run pytest tests/      # run all tests
pytest tests/test_search.py  # single test module
ruff check src/ tests/    # lint
tlf update                # fetch/refresh course database
tlf serve                 # start web UI
```

## Architecture

**Data flow:** `fetcher.py` → `storage.py` → `search.py` → `cli.py` / `web.py`

- `fetcher.py` — async `httpx` client; knows nothing about storage
- `storage.py` — SQLite + FTS5 (schema v7); FTS virtual table synced via triggers
- `search.py` — keyword (FTS5 BM25), semantic (`all-MiniLM-L6-v2`), hybrid
- `cli.py` — Click entry-point; business logic lives here so `web.py` can reuse it
- `web.py` — FastAPI; rate-limited via slowapi, security headers middleware

### Non-obvious details

- **Hybrid scoring weights:** title 10×, content 2×, objectives 1× (magic numbers in `search.py`)
- **Campus filtering** uses substring matching — `--campus garching` also matches `garching-hochbrück`
- **Cross-semester dedup** groups by `identity_code_id`; `other_semesters` populated by `compute_other_semesters()`
- **Data paths:** `<project_root>/data/courses.db` (SQLite), `<project_root>/data/embeddings.npz` (embeddings cache)

## Coding Standards

- **Linter:** `ruff`, all rules enabled, line length 99, Python ≥ 3.12
- **Docstrings:** Google-style, required on all public symbols
- **Error handling:** let exceptions propagate; handle only at the CLI/web boundary

## Deployment Context

The production target is a **Docker container** running on a Linux machine inside a private VPN. A second Linux machine with a public IP runs **nginx or Traefik** as a reverse proxy and forwards traffic into the container. This is a low-traffic personal/university tool — not a public SaaS product.

Consequences:
- `TLF_TRUST_PROXY=1` must be set in production so rate limiting resolves the real client IP from `X-Forwarded-For`
- The reverse proxy handles volumetric flood protection; app-level rate limiting is defense-in-depth for expensive endpoints
- The container runs **unsupervised** — structured logging to stdout is essential for diagnosing problems after the fact

## Development Philosophy

### Test-First (TDD)

Every code change must be preceded by a test that:
1. **Fails** before the change (proving the test actually exercises the thing)
2. **Passes** after the change

New features require tests written before implementation. This is non-negotiable — the note at the top of this file about AI-generated code means untested paths are genuinely unreviewed.

### DB Safety

`<project_root>/data/courses.db` is expensive to rebuild (~24 000 courses, semantic embeddings take minutes to generate). Never run destructive operations on it casually:
- Schema migrations must be wrapped in a transaction with rollback on error
- Never drop tables outside of a versioned migration path
- Test storage changes against a temporary `tmp_path` DB (see existing fixtures), never the real one

### Logging

Use `structlog` throughout. Production output must be JSON (controlled by `TLF_JSON_LOGS=1`). Development output should be human-readable colored console output. Never use bare `print()` for diagnostic output — use `logger.*` so logs are captured by the container runtime.

## Environment Variables

| Variable | Effect |
|---|---|
| `TLF_TRUST_PROXY=1` | Trust `X-Forwarded-For` (only when behind a trusted reverse proxy) |
| `TLF_PRELOAD_MODEL=0` | Skip pre-loading the semantic model on startup |
| `TLF_JSON_LOGS=1` | Emit structured JSON logs to stdout (default: human-readable) |
| `TLF_UPDATE_CRON` | Cron expression for automatic DB updates (e.g. `0 3 * * *`); disabled if unset |
| `TLF_UPDATE_SEMESTERS` | Number of recent semesters to fetch on scheduled update (default: `4`) |
| `TLF_FULL_UPDATE_EVERY` | Every Nth scheduled run does a full re-fetch of all course details (default: `7`). First run after process start is always full. |
| `TLF_HSTS_PRELOAD` | Set to `1` to include `preload` in HSTS header (default: off — preload list submission is permanent) |
