#!/usr/bin/env python3
"""Human-readable log viewer and stats for TUM Lecture Finder.

Reads JSON log lines from ``docker compose logs`` (auto-detected) or stdin.

Usage examples::

    # Live tail, human-readable (auto-runs docker compose logs -f)
    python scripts/logview.py

    # Show only requests (skip scheduler/startup noise)
    python scripts/logview.py --requests

    # Show only errors and warnings
    python scripts/logview.py --errors

    # Filter by HTTP status code or range
    python scripts/logview.py --status 404
    python scripts/logview.py --status 4xx

    # Show slow requests (> 100ms)
    python scripts/logview.py --slow 100

    # Show only the last hour / today
    python scripts/logview.py --since 1h
    python scripts/logview.py --since today

    # Show what people searched for (ranked)
    python scripts/logview.py --top-searches

    # Aggregate stats: top queries, busiest IPs, status breakdown
    python scripts/logview.py --stats

    # Pipe mode still works
    docker compose logs | python scripts/logview.py --searches

"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from collections import Counter
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import parse_qs, urlparse

if TYPE_CHECKING:
    from collections.abc import Iterator

# ANSI colour helpers
_RESET = "\033[0m"
_DIM = "\033[2m"
_BOLD = "\033[1m"
_RED = "\033[31m"
_YELLOW = "\033[33m"
_GREEN = "\033[32m"
_CYAN = "\033[36m"
_MAGENTA = "\033[35m"

_STATUS_COLOURS = {
    2: _GREEN,
    3: _CYAN,
    4: _YELLOW,
    5: _RED,
}


def _colour_status(status: int) -> str:
    colour = _STATUS_COLOURS.get(status // 100, "")
    return f"{colour}{status}{_RESET}"


def _colour_level(level: str) -> str:
    level = level.upper()
    if level == "ERROR":
        return f"{_RED}{_BOLD}{level:>7}{_RESET}"
    if level == "WARNING":
        return f"{_YELLOW}{level:>7}{_RESET}"
    if level == "INFO":
        return f"{_GREEN}{level:>7}{_RESET}"
    return f"{_DIM}{level:>7}{_RESET}"


def _parse_timestamp(ts: str) -> datetime | None:
    """Parse an ISO timestamp string to a datetime."""
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None


def _format_time(ts: str) -> str:
    """Extract HH:MM:SS from an ISO timestamp."""
    dt = _parse_timestamp(ts)
    if dt:
        return dt.strftime("%H:%M:%S")
    return ts[:8] if len(ts) >= 8 else ts  # noqa: PLR2004


def _parse_since(value: str) -> datetime:
    """Parse a --since value into a UTC datetime cutoff.

    Supports: ``30m``, ``1h``, ``6h``, ``1d``, ``today``, ``yesterday``.
    """
    now = datetime.now(UTC)
    value = value.strip().lower()

    if value == "today":
        return now.replace(hour=0, minute=0, second=0, microsecond=0)
    if value == "yesterday":
        yesterday = now - timedelta(days=1)
        return yesterday.replace(hour=0, minute=0, second=0, microsecond=0)

    match = re.fullmatch(r"(\d+)\s*([mhd])", value)
    if not match:
        print(  # noqa: T201
            f"Invalid --since value: {value!r}. "
            "Use e.g. 30m, 1h, 6h, 1d, today, yesterday.",
        )
        sys.exit(1)

    amount = int(match.group(1))
    unit = match.group(2)
    delta = {
        "m": timedelta(minutes=amount),
        "h": timedelta(hours=amount),
        "d": timedelta(days=amount),
    }
    return now - delta[unit]


def _parse_status_filter(value: str) -> tuple[int | None, int | None]:
    """Parse a --status value into (exact, range_prefix).

    Returns ``(exact_code, None)`` for e.g. ``404``,
    or ``(None, class_digit)`` for e.g. ``4xx``.
    """
    value = value.strip().lower()
    if re.fullmatch(r"[1-5]xx", value):
        return None, int(value[0])
    try:
        return int(value), None
    except ValueError:
        print(f"Invalid --status value: {value!r}. Use e.g. 404, 4xx, 5xx.")  # noqa: T201
        sys.exit(1)


def _extract_search_query(path: str) -> str | None:
    """Extract the 'q' parameter from a request path like /api/search?q=foo."""
    if "?" not in path:
        return None
    parsed = urlparse(path)
    params = parse_qs(parsed.query)
    q_values = params.get("q")
    if q_values:
        return q_values[0]
    return None


# ── Formatters ───────────────────────────────────────────────────────────


def _format_request(entry: dict) -> str:
    """Format a request log line."""
    ts = _format_time(entry.get("timestamp", ""))
    method = entry.get("method", "???")
    path = entry.get("path", "???")
    status = entry.get("status", 0)
    duration = entry.get("duration_ms", 0)
    client_ip = entry.get("client_ip", "")

    status_str = _colour_status(status)
    dur_str = f"{duration:>7.1f}ms"

    return (
        f"{_DIM}{ts}{_RESET}  {status_str}  {method:<4} {path:<60}"
        f" {dur_str}  {_DIM}{client_ip}{_RESET}"
    )


def _format_schedule_fetch(entry: dict) -> str:
    """Format a schedule_fetch log line."""
    ts = _format_time(entry.get("timestamp", ""))
    course_id = entry.get("course_id", "?")
    status = entry.get("status", 0)
    duration = entry.get("duration_ms", 0)
    url = entry.get("url", "")

    status_str = _colour_status(status)
    return (
        f"{_DIM}{ts}{_RESET}  {status_str}"
        f"  {_MAGENTA}>> TUMonline{_RESET} course={course_id}"
        f"  {duration:>7.1f}ms  {_DIM}{url}{_RESET}"
    )


def _format_generic(entry: dict) -> str:
    """Format a non-request log line."""
    ts = _format_time(entry.get("timestamp", ""))
    level = entry.get("level", "?")
    event = entry.get("event", "")
    logger = entry.get("logger", "")

    extras = {
        k: v
        for k, v in entry.items()
        if k not in {"timestamp", "level", "event", "logger"}
    }
    extra_str = ""
    if extras:
        parts = [f"{k}={v}" for k, v in extras.items()]
        extra_str = f"  {_DIM}{', '.join(parts)}{_RESET}"

    logger_str = f"  {_DIM}[{logger}]{_RESET}" if logger else ""

    return f"{_DIM}{ts}{_RESET}  {_colour_level(level)}  {event}{logger_str}{extra_str}"


def _format_entry(entry: dict) -> str:
    """Route to the appropriate formatter."""
    event = entry.get("event", "")
    if event == "request":
        return _format_request(entry)
    if event == "schedule_fetch":
        return _format_schedule_fetch(entry)
    return _format_generic(entry)


# ── Parsing & filtering ──────────────────────────────────────────────────


def _parse_line(line: str) -> dict | None:
    """Parse a JSON log line, stripping docker compose prefix if present."""
    if "|" in line:
        line = line.split("|", 1)[1].strip()
    line = line.strip()
    if not line or not line.startswith("{"):
        return None
    try:
        return json.loads(line)
    except json.JSONDecodeError:
        return None


def _matches_filters(entry: dict, args: argparse.Namespace) -> bool:  # noqa: PLR0911
    """Check if a log entry matches the active filters."""
    event = entry.get("event", "")
    level = entry.get("level", "").lower()

    # Time filter
    if args.since_cutoff is not None:
        ts = _parse_timestamp(entry.get("timestamp", ""))
        if ts is None or ts < args.since_cutoff:
            return False

    if args.errors and level not in {"error", "warning"}:
        return False

    if args.requests and event not in {"request", "schedule_fetch"}:
        return False

    # Status filter (exact or range)
    if args.status_exact is not None and (
        event != "request" or entry.get("status") != args.status_exact
    ):
        return False
    if args.status_class is not None and (
        event != "request" or entry.get("status", 0) // 100 != args.status_class
    ):
        return False

    # Slow filter
    if args.slow is not None and (
        event != "request" or entry.get("duration_ms", 0) < args.slow
    ):
        return False

    return not (
        args.searches
        and (
            event != "request"
            or not entry.get("path", "").startswith("/api/search")
        )
    )


# ── Input source ─────────────────────────────────────────────────────────


def _line_source(args: argparse.Namespace) -> Iterator[str]:
    """Yield log lines from stdin or docker compose logs."""
    if not sys.stdin.isatty():
        yield from sys.stdin
        return

    # stdin is a TTY — auto-run docker compose logs
    compose_dir = args.compose_dir or _find_compose_dir()
    if compose_dir is None:
        print(  # noqa: T201
            "No compose.yaml found. Either pipe logs to stdin or "
            "run from the project directory.",
        )
        sys.exit(1)

    cmd = ["docker", "compose", "logs", "--no-log-prefix"]
    if args.follow:
        cmd.append("-f")

    proc = subprocess.Popen(  # noqa: S603
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        cwd=compose_dir,
    )
    try:
        yield from proc.stdout  # type: ignore[union-attr]
    finally:
        proc.terminate()


def _find_compose_dir() -> str | None:
    """Walk up from cwd looking for compose.yaml."""
    path = Path.cwd().resolve()
    while True:
        if (path / "compose.yaml").is_file():
            return str(path)
        if (path / "docker-compose.yml").is_file():
            return str(path)
        parent = path.parent
        if parent == path:
            return None
        path = parent


# ── Modes ────────────────────────────────────────────────────────────────


def _run_tail(args: argparse.Namespace) -> None:
    """Stream and format log lines."""
    for line in _line_source(args):
        entry = _parse_line(line)
        if entry is None:
            continue
        if _matches_filters(entry, args):
            print(_format_entry(entry))  # noqa: T201


def _run_top_searches(args: argparse.Namespace) -> None:
    """Show ranked list of search queries."""
    queries: Counter[str] = Counter()

    for line in _line_source(args):
        entry = _parse_line(line)
        if entry is None:
            continue

        # Time filter
        if args.since_cutoff is not None:
            ts = _parse_timestamp(entry.get("timestamp", ""))
            if ts is None or ts < args.since_cutoff:
                continue

        if entry.get("event") != "request":
            continue
        query = _extract_search_query(entry.get("path", ""))
        if query:
            queries[query] += 1

    if not queries:
        print("No search queries found.")  # noqa: T201
        return

    print(f"\n{_BOLD}Top Searches:{_RESET}\n")  # noqa: T201
    for query, count in queries.most_common(30):
        print(f"  {count:>4}x  {query}")  # noqa: T201
    print()  # noqa: T201


def _print_stats(  # noqa: PLR0913
    total_requests: int,
    total_duration: float,
    slowest: tuple[float, str],
    errors: int,
    statuses: Counter[int],
    queries: Counter[str],
    paths: Counter[str],
    ips: Counter[str],
) -> None:
    """Print formatted statistics."""
    avg_duration = total_duration / total_requests

    print(f"\n{_BOLD}=== TUM Lecture Finder Log Stats ==={_RESET}\n")  # noqa: T201
    print(f"  Total requests:  {total_requests}")  # noqa: T201
    print(f"  Avg latency:     {avg_duration:.1f}ms")  # noqa: T201
    print(  # noqa: T201
        f"  Slowest:         {slowest[0]:.1f}ms"
        f"  {_DIM}{slowest[1]}{_RESET}"
    )
    print(f"  Errors/warnings: {errors}")  # noqa: T201

    print(f"\n{_BOLD}Status Codes:{_RESET}")  # noqa: T201
    for status, count in sorted(statuses.items()):
        pct = count / total_requests * 100
        bar = "#" * int(pct / 2)
        print(f"  {_colour_status(status)}  {count:>6}  ({pct:4.1f}%)  {_DIM}{bar}{_RESET}")  # noqa: T201

    if queries:
        print(f"\n{_BOLD}Top Searches:{_RESET}")  # noqa: T201
        for query, count in queries.most_common(20):
            print(f"  {count:>4}x  {query}")  # noqa: T201

    print(f"\n{_BOLD}Top Paths:{_RESET}")  # noqa: T201
    for path, count in paths.most_common(15):
        print(f"  {count:>4}x  {path}")  # noqa: T201

    print(f"\n{_BOLD}Top Client IPs:{_RESET}")  # noqa: T201
    for ip, count in ips.most_common(10):
        print(f"  {count:>4}x  {ip}")  # noqa: T201

    print()  # noqa: T201


def _run_stats(args: argparse.Namespace) -> None:
    """Aggregate and display statistics."""
    queries: Counter[str] = Counter()
    ips: Counter[str] = Counter()
    statuses: Counter[int] = Counter()
    paths: Counter[str] = Counter()
    total_requests = 0
    errors = 0
    total_duration = 0.0
    slowest: tuple[float, str] = (0.0, "")

    for line in _line_source(args):
        entry = _parse_line(line)
        if entry is None:
            continue

        # Time filter
        if args.since_cutoff is not None:
            ts = _parse_timestamp(entry.get("timestamp", ""))
            if ts is None or ts < args.since_cutoff:
                continue

        if entry.get("event") != "request":
            if entry.get("level", "").lower() in {"error", "warning"}:
                errors += 1
            continue

        total_requests += 1
        status = entry.get("status", 0)
        statuses[status] += 1
        duration = entry.get("duration_ms", 0)
        total_duration += duration

        path = entry.get("path", "")
        ips[entry.get("client_ip", "")] += 1
        paths[path.split("?")[0]] += 1

        if duration > slowest[0]:
            slowest = (duration, path)

        query = _extract_search_query(path)
        if query:
            queries[query] += 1

    if total_requests == 0:
        print("No request log entries found.")  # noqa: T201
        return

    _print_stats(
        total_requests, total_duration, slowest, errors,
        statuses, queries, paths, ips,
    )


# ── CLI ──────────────────────────────────────────────────────────────────


def main() -> None:
    """Entry point."""
    parser = argparse.ArgumentParser(
        description="Human-readable log viewer for TUM Lecture Finder.",
        epilog="When stdin is not piped, auto-runs 'docker compose logs'.",
    )
    parser.add_argument(
        "--requests", action="store_true",
        help="Show only HTTP request and schedule_fetch lines.",
    )
    parser.add_argument(
        "--errors", action="store_true",
        help="Show only warnings and errors.",
    )
    parser.add_argument(
        "--status", default=None, metavar="CODE",
        help="Filter by HTTP status code (e.g. 404) or class (e.g. 4xx).",
    )
    parser.add_argument(
        "--slow", type=float, default=None, metavar="MS",
        help="Show requests slower than this threshold in ms.",
    )
    parser.add_argument(
        "--searches", action="store_true",
        help="Show only search API requests.",
    )
    parser.add_argument(
        "--top-searches", action="store_true",
        help="Ranked list of search queries.",
    )
    parser.add_argument(
        "--stats", action="store_true",
        help="Aggregate mode: show summary statistics.",
    )
    parser.add_argument(
        "--since", default=None, metavar="TIME",
        help="Only show entries after TIME (e.g. 30m, 1h, 6h, 1d, today).",
    )
    parser.add_argument(
        "-f", "--follow", action="store_true",
        help="Follow log output (like tail -f). Only when auto-running docker.",
    )
    parser.add_argument(
        "--compose-dir", default=None, dest="compose_dir",
        help="Directory containing compose.yaml (auto-detected by default).",
    )
    args = parser.parse_args()

    # Pre-parse composite filters
    args.since_cutoff = _parse_since(args.since) if args.since else None
    args.status_exact = None
    args.status_class = None
    if args.status is not None:
        args.status_exact, args.status_class = _parse_status_filter(args.status)

    try:
        if args.top_searches:
            _run_top_searches(args)
        elif args.stats:
            _run_stats(args)
        else:
            _run_tail(args)
    except KeyboardInterrupt:
        pass  # Clean exit on Ctrl+C


if __name__ == "__main__":
    main()
