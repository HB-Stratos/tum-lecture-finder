"""FastAPI web application for TUM Lecture Finder."""

from __future__ import annotations

import os
import re
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Any

import httpx
import structlog
from asgi_correlation_id import CorrelationIdMiddleware, correlation_id
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address
from starlette.responses import JSONResponse, Response
from structlog.contextvars import bind_contextvars, clear_contextvars

from tum_lecture_finder.config import DB_PATH, format_semester
from tum_lecture_finder.logging_config import setup_logging
from tum_lecture_finder.search import (
    fulltext_search,
    hybrid_search,
    semantic_search,
)
from tum_lecture_finder.storage import CourseStore, row_to_course

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable

    from tum_lecture_finder.models import SearchResult

log: structlog.stdlib.BoundLogger = structlog.get_logger(__name__)

# ── Paths ──────────────────────────────────────────────────────────────────
_HERE = Path(__file__).parent
_TEMPLATES_DIR = _HERE / "templates"
_STATIC_DIR = _HERE / "static"


_TRUST_PROXY = os.environ.get("TLF_TRUST_PROXY", "0") == "1"
_PRELOAD_MODEL = os.environ.get("TLF_PRELOAD_MODEL", "1") == "1"


def _real_ip(request: Request) -> str:
    """Return client IP, optionally trusting X-Forwarded-For from a proxy."""
    if _TRUST_PROXY:
        # Only trust X-Forwarded-For if explicitly enabled
        xff = request.headers.get("x-forwarded-for")
        if xff:
            return xff.split(",")[0].strip()
    return get_remote_address(request)


# application_limits caps the *total* request rate across all IPs so that a
# distributed flood (many IPs, each under the per-IP ceiling) still gets
# throttled at the server level.
limiter = Limiter(key_func=_real_ip, application_limits=["1000/minute"])

# ── Global store (initialised on startup) ──────────────────────────────────
_store: CourseStore | None = None


def _get_store() -> CourseStore:
    """Return the global CourseStore instance."""
    if _store is None:
        msg = "Database not initialised"
        raise RuntimeError(msg)
    return _store


_UPDATE_CRON = os.environ.get("TLF_UPDATE_CRON", "")
_UPDATE_SEMESTERS = int(os.environ.get("TLF_UPDATE_SEMESTERS", "4"))
_FULL_UPDATE_EVERY = max(1, int(os.environ.get("TLF_FULL_UPDATE_EVERY", "7")))
_update_run_count = -1  # first increment yields 0 → 0 % N == 0 → full run
_update_running = False


async def _scheduled_update() -> None:  # noqa: PLR0915
    """Run an incremental or full DB update in the background (APScheduler).

    Two-tier scheduling:
    - Every run: fetches course lists for all configured semesters.
    - Every Nth run (``TLF_FULL_UPDATE_EVERY``): fetches details for ALL courses.
    - Other runs: fetches details only for current/future semester courses and
      new courses not yet in the DB.

    Errors are caught and logged so the scheduler keeps running.
    """
    global _update_run_count, _update_running  # noqa: PLW0603
    global _type_counts_cache, _campus_counts_cache  # noqa: PLW0603

    sched_log = structlog.get_logger("tlf.scheduler")

    if _update_running:
        sched_log.warning("scheduled_update_skipped", reason="previous run still in progress")
        return
    _update_running = True

    try:
        from datetime import UTC, datetime

        from tum_lecture_finder.config import (
            current_semester_key,
            is_current_or_future_semester,
        )
        from tum_lecture_finder.fetcher import fetch_courses, fetch_semester_list
        from tum_lecture_finder.search import (
            build_embeddings,
            invalidate_course_cache,
        )

        _update_run_count += 1
        is_full = (_update_run_count % _FULL_UPDATE_EVERY == 0)
        tier = "full" if is_full else "incremental"

        store = _get_store()

        # Resolve which semesters to fetch
        all_semesters = await fetch_semester_list()
        semester_ids = [s["id"] for s in all_semesters[:_UPDATE_SEMESTERS]]
        semester_keys = [s["key"] for s in all_semesters[:_UPDATE_SEMESTERS]]

        sched_log.info(
            "scheduled_update_start",
            run_count=_update_run_count,
            tier=tier,
            semesters=semester_keys,
        )

        # Build skip set for incremental runs
        skip_ids: set[int] | None = None
        if not is_full:
            current = current_semester_key()
            past_keys = [k for k in semester_keys if not is_current_or_future_semester(k, current)]
            if past_keys:
                skip_ids = store.get_course_ids_with_details(past_keys)
                sched_log.info(
                    "detail_skip_summary",
                    past_semesters=past_keys,
                    skip_count=len(skip_ids),
                )

        building_cache = store.get_building_cache()
        result = await fetch_courses(
            semester_ids=semester_ids,
            building_cache=building_cache,
            skip_detail_ids=skip_ids,
        )

        all_courses = result.detailed + result.list_only
        if all_courses:
            # Upsert in a single transaction
            store.upsert_courses(result.detailed, commit=False)
            if result.list_only:
                store.upsert_course_list_fields(result.list_only, commit=False)
            store.commit()

            store.compute_other_semesters()
            store.upsert_building_cache(building_cache)

            # Invalidate caches BEFORE rebuilding embeddings
            invalidate_course_cache()
            build_embeddings(store)

            # Invalidate web-layer caches
            _type_counts_cache = None
            _campus_counts_cache = None

            # Persist update stats
            store.set_meta("last_update_time", datetime.now(UTC).isoformat())
            store.set_meta("last_update_tier", tier)
            store.set_meta("last_update_detailed", str(len(result.detailed)))
            store.set_meta("last_update_skipped", str(len(result.list_only)))
            store.set_meta("last_update_semesters", ",".join(semester_keys))

            sched_log.info(
                "scheduled_update_done",
                courses_detailed=len(result.detailed),
                courses_skipped=len(result.list_only),
                total_stored=len(all_courses),
            )
        else:
            sched_log.warning("scheduled_update_no_courses")
    except Exception:  # noqa: BLE001
        sched_log.exception("scheduled_update_error")
    finally:
        _update_running = False


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:  # noqa: ARG001
    """Open DB on startup, configure logging, preload model, close on shutdown."""
    setup_logging()
    global _store  # noqa: PLW0603
    _store = CourseStore(check_same_thread=False)
    # Preload can be disabled for faster/smaller container startup.
    if _PRELOAD_MODEL:
        log.info("Pre-loading semantic search model...")
        from tum_lecture_finder.search import _get_model

        _get_model()
        log.info("Model loaded.")
    else:
        log.info("Skipping model preload (TLF_PRELOAD_MODEL=0).")

    # Start background scheduler if TLF_UPDATE_CRON is set
    scheduler = None
    if _UPDATE_CRON:
        from apscheduler.schedulers.asyncio import AsyncIOScheduler
        from apscheduler.triggers.cron import CronTrigger

        scheduler = AsyncIOScheduler()
        trigger = CronTrigger.from_crontab(_UPDATE_CRON)
        scheduler.add_job(_scheduled_update, trigger)
        scheduler.start()
        log.info(
            "scheduler_started",
            cron=_UPDATE_CRON,
            recent_semesters=_UPDATE_SEMESTERS,
            full_update_every=_FULL_UPDATE_EVERY,
        )

    yield

    if scheduler is not None:
        scheduler.shutdown(wait=False)
        log.info("scheduler_stopped")
    if _store is not None:
        _store.close()
        _store = None


# ── App ────────────────────────────────────────────────────────────────────
app = FastAPI(
    title="TUM Lecture Finder",
    description="Search TU Munich's course catalog",
    lifespan=_lifespan,
    docs_url=None,
    redoc_url=None,
)

app.state.limiter = limiter

# Attach correlation-id middleware (generates / forwards X-Request-ID header)
app.add_middleware(CorrelationIdMiddleware)


@app.exception_handler(RateLimitExceeded)
async def _rate_limit_handler(request: Request, exc: RateLimitExceeded) -> Response:  # noqa: ARG001
    log.warning(
        "rate_limit_exceeded",
        path=request.url.path,
        client_ip=_real_ip(request),
    )
    return JSONResponse(
        status_code=429,
        content={"detail": "Rate limit exceeded. Please slow down."},
        headers={"Retry-After": "60"},
    )


# Request logging middleware — logs every request with method, path, status, latency
@app.middleware("http")
async def _log_requests(
    request: Request,
    call_next: Callable[[Request], Any],
) -> Response:
    """Log each request with structured fields and bind a correlation ID."""
    clear_contextvars()
    req_id = correlation_id.get()
    if req_id:
        bind_contextvars(request_id=req_id)

    started = time.perf_counter()
    try:
        response = await call_next(request)
    except Exception:
        log.exception(
            "unhandled_exception",
            method=request.method,
            path=request.url.path,
        )
        raise
    duration_ms = (time.perf_counter() - started) * 1000

    log.info(
        "request",
        method=request.method,
        path=request.url.path,
        status=response.status_code,
        duration_ms=round(duration_ms, 1),
        client_ip=_real_ip(request),
    )
    return response


# Security headers middleware
_HSTS_PRELOAD = os.environ.get("TLF_HSTS_PRELOAD", "0") == "1"


@app.middleware("http")
async def _security_headers(
    request: Request,
    call_next: Callable[[Request], Any],
) -> Response:
    """Add security headers to all responses."""
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    hsts = "max-age=63072000; includeSubDomains"
    if _HSTS_PRELOAD:
        hsts += "; preload"
    response.headers["Strict-Transport-Security"] = hsts
    # Hardened CSP: no object/embed, no base-uri, restrict form-action
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; "
        "script-src 'self'; "
        "style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data:; "
        "font-src 'self'; "
        "connect-src 'self'; "
        "frame-ancestors 'none'; "
        "object-src 'none'; "
        "base-uri 'none'; "
        "form-action 'self'"
    )
    # Remove the default 'server: uvicorn' header to avoid revealing the
    # tech stack to potential attackers.
    if "server" in response.headers:
        del response.headers["server"]
    return response


# Static files
app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")

# Templates
templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))
templates.env.globals["format_semester"] = format_semester


# ── Helpers ────────────────────────────────────────────────────────────────
_MAX_QUERY_LENGTH = 200
_MAX_LIMIT = 100


def _sanitize_query(q: str) -> str:
    """Strip and limit query length."""
    return q.strip()[:_MAX_QUERY_LENGTH]


def _result_to_dict(r: SearchResult) -> dict[str, Any]:
    """Convert a SearchResult to a JSON-serializable dict."""
    c = r.course
    return {
        "course_id": c.course_id,
        "course_number": c.course_number,
        "title_de": c.title_de,
        "title_en": c.title_en,
        "title": c.title_en or c.title_de,
        "course_type": c.course_type,
        "semester_key": c.semester_key,
        "semester_display": format_semester(c.semester_key) if c.semester_key else "",
        "sws": c.sws,
        "organisation": c.organisation,
        "instructors": _dedup_instructors(c.instructors),
        "language": c.language,
        "campus": c.campus,
        "campus_display": _campus_display_name(c.campus),
        "score": round(r.score, 3),
        "snippet": r.snippet,
        "other_semesters": r.other_semesters,
        "other_semesters_display": [format_semester(s) for s in r.other_semesters if s],
        "offering_frequency": _offering_frequency(c.semester_key, r.other_semesters),
    }


def _extract_weekday(date_entry: dict[str, Any]) -> str:
    """Extract English weekday name from a course appointment entry."""
    wd_obj = date_entry.get("weekday", {})
    lang_data = wd_obj.get("langDataType", {})
    translations = lang_data.get("translations", {}).get("translation", [])
    if isinstance(translations, dict):
        translations = [translations]
    for t in translations:
        if t.get("lang") == "en":
            return t.get("value", "")
    for t in translations:
        return t.get("value", "")
    # Fallback to plain value or key
    return lang_data.get("value", "") or wd_obj.get("key", "")


def _extract_time_range(date_entry: dict[str, Any]) -> str:
    """Extract formatted time range from a course appointment entry."""
    time_from = date_entry.get("timestampFrom", {}).get("value", "")
    time_to = date_entry.get("timestampTo", {}).get("value", "")
    if time_from and time_to:
        try:
            from datetime import datetime

            tf = datetime.fromisoformat(time_from)
            tt = datetime.fromisoformat(time_to)
        except ValueError:
            return f"{time_from} - {time_to}"
        else:
            return f"{tf:%H:%M} - {tt:%H:%M}"
    return ""


_NAVIGATUM_ROOM_URL = "https://nav.tum.de/room/"
_ROOM_CODE_RE = re.compile(r"\((\d{4}\.\w+\.\w+)\)")

# ── Campus display names ───────────────────────────────────────────────────
# NavigaTUM returns slugs like "campus-straubing-cs-biotechnologie-und-nachhaltigkeit".
# Map common ones to short readable labels; fall back to title-casing the slug.
_CAMPUS_DISPLAY: dict[str, str] = {
    "stammgelände": "München (Stammgelände)",
    "garching": "Garching",
    "garching-hochbrück": "Garching-Hochbrück",
    "weihenstephan": "Freising (Weihenstephan)",
    "campus-im-olympiapark-sz": "Olympiapark",
    "campus-straubing-cs-biotechnologie-und-nachhaltigkeit": "Straubing",
    "campus-heilbronn": "Heilbronn",
    "marsstraße 20, 21, 22": "Marsstraße",
    "karlstraße 45/47": "Karlstraße",
    "taufkirchen-ottobr.": "Ottobrunn",
    "newton": "Newton",
    "gewächshauslaborzentrum": "Gewächshauslaborzentrum",
    "pasing-franz-langinger-straße": "Pasing",
    "residenz münchen": "Residenz München",
    "kapuzinerhölzl-motorenlabor-lehrstuhl-für-verbrennungskraftmaschinen": "Kapuzinerhölzl",
    "eichenau lindenweg": "Eichenau",
    "limnologische-station-iffeldorf": "Iffeldorf",
}


def _campus_display_name(raw: str) -> str:
    """Convert a NavigaTUM campus slug to a human-readable name."""
    if not raw:
        return ""
    return _CAMPUS_DISPLAY.get(raw, raw.replace("-", " ").title())


def _dedup_instructors(instructors: str) -> str:
    """Remove duplicate instructor names (API sometimes repeats them)."""
    if not instructors:
        return ""
    names = [n.strip() for n in instructors.split(",") if n.strip()]
    seen: list[str] = []
    for name in names:
        if name not in seen:
            seen.append(name)
    return ", ".join(seen)


def _offering_frequency(semester_key: str, other_semesters: list[str]) -> str:
    """Determine how frequently a course is offered.

    Args:
        semester_key: The displayed course's semester (e.g. ``"25W"``).
        other_semesters: Semester keys of other offerings.

    Returns:
        ``"every semester"``, ``"yearly"``, or ``""`` (one-off / unknown).

    """
    all_sems = {semester_key, *other_semesters} if semester_key else set(other_semesters)
    if len(all_sems) < 2:  # noqa: PLR2004
        return ""
    has_summer = any(s.endswith("S") for s in all_sems)
    has_winter = any(s.endswith("W") for s in all_sems)
    if has_summer and has_winter:
        return "every semester"
    return "yearly"


def _extract_room_link(resource_name: str) -> str:
    """Extract a NavigaTUM room URL from a TUMonline resource name.

    Resource names look like ``"0.A01, Seminarraum A0 (3501.EG.001A)"``.
    The code in parentheses is the NavigaTUM room identifier.

    Args:
        resource_name: The raw ``resourceName`` from the API.

    Returns:
        A NavigaTUM room URL, or ``""`` if no code is found.

    """
    m = _ROOM_CODE_RE.search(resource_name)
    if m:
        return f"{_NAVIGATUM_ROOM_URL}{m.group(1)}"
    return ""


def _parse_appointments(data: dict[str, Any]) -> list[dict[str, str]]:
    """Extract unique appointment slots from courseGroups API response.

    Repeating weekly appointments (same weekday, time, room) are collapsed
    into a single entry so the schedule shows a clean overview.
    """
    seen: set[tuple[str, str, str]] = set()
    appointments: list[dict[str, str]] = []
    groups = data.get("courseGroupDtos", [])
    if isinstance(groups, dict):
        groups = [groups]
    for group in groups:
        apt_list = group.get("appointmentDtos", [])
        if isinstance(apt_list, dict):
            apt_list = [apt_list]
        for apt in apt_list:
            weekday = _extract_weekday(apt)
            time_range = _extract_time_range(apt)
            room = apt.get("resourceName", "")
            key = (weekday, time_range, room)
            if key in seen:
                continue
            seen.add(key)
            appointments.append(
                {
                    "weekday": weekday,
                    "time": time_range,
                    "room": room,
                    "room_link": _extract_room_link(room),
                }
            )
    return appointments


def _course_to_dict(row: object) -> dict[str, Any]:
    """Convert a DB row to a full course dict."""
    c = row_to_course(row)
    return {
        "course_id": c.course_id,
        "course_number": c.course_number,
        "title_de": c.title_de,
        "title_en": c.title_en,
        "title": c.title_en or c.title_de,
        "course_type": c.course_type,
        "semester_key": c.semester_key,
        "semester_display": format_semester(c.semester_key) if c.semester_key else "",
        "sws": c.sws,
        "organisation": c.organisation,
        "instructors": _dedup_instructors(c.instructors),
        "language": c.language,
        "campus": c.campus,
        "campus_display": _campus_display_name(c.campus),
        "identity_code_id": c.identity_code_id,
        "content_de": c.content_de,
        "content_en": c.content_en,
        "objectives_de": c.objectives_de,
        "objectives_en": c.objectives_en,
        "prerequisites": c.prerequisites,
        "literature": c.literature,
    }


# ── Favicon ─────────────────────────────────────────────────────────────────

_FAVICON_SVG = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32">'
    '<rect width="32" height="32" rx="6" fill="#0065bd"/>'
    '<text x="16" y="23" text-anchor="middle" font-size="18" '
    'font-family="Arial,sans-serif" font-weight="bold" fill="#fff">T</text>'
    "</svg>"
)


@app.get("/favicon.ico", include_in_schema=False)
async def favicon() -> Response:
    """Return an SVG favicon."""
    return Response(content=_FAVICON_SVG, media_type="image/svg+xml")


# ── HTML pages ─────────────────────────────────────────────────────────────


def _db_last_updated() -> str:
    """Return the last-update timestamp, preferring meta table over file mtime."""
    from datetime import UTC, datetime

    try:
        store = _get_store()
        meta_time = store.get_meta("last_update_time")
        if meta_time:
            return meta_time
    except RuntimeError:
        pass

    try:
        mtime = DB_PATH.stat().st_mtime
        dt = datetime.fromtimestamp(mtime, tz=UTC)
        return dt.strftime("%Y-%m-%d %H:%M UTC")
    except OSError:
        return "unknown"


def _get_update_info() -> dict[str, Any] | None:
    """Return last scheduled update stats from the meta table, or None."""
    try:
        store = _get_store()
    except RuntimeError:
        return None

    time = store.get_meta("last_update_time")
    if not time:
        return None

    semesters_csv = store.get_meta("last_update_semesters", default="")
    return {
        "time": time,
        "tier": store.get_meta("last_update_tier", default="unknown"),
        "courses_detailed": int(store.get_meta("last_update_detailed", default="0")),
        "courses_skipped": int(store.get_meta("last_update_skipped", default="0")),
        "semesters": [s for s in semesters_csv.split(",") if s],
    }


@app.get("/", response_class=HTMLResponse)
@limiter.limit("60/minute")
async def index(request: Request) -> HTMLResponse:
    """Render the main search page."""
    store = _get_store()
    total = store.course_count()
    semesters = store.semester_counts()
    semester_keys = sorted([s[0] for s in semesters], reverse=True)
    semester_list = [{"key": k, "display": format_semester(k)} for k in semester_keys]
    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "total_courses": total,
            "semesters": semesters,
            "semester_list": semester_list,
            "last_updated": _db_last_updated(),
        },
    )


@app.get("/course/{course_id}", response_class=HTMLResponse)
@limiter.limit("60/minute")
async def course_detail(request: Request, course_id: int) -> HTMLResponse:
    """Render the course detail page."""
    store = _get_store()
    row = store.get_course(course_id)
    if not row:
        raise HTTPException(status_code=404, detail="Course not found")
    course = _course_to_dict(row)

    # Find other semesters this course appears in
    other_semesters: list[dict[str, str]] = []
    identity = course.get("identity_code_id")
    if identity:
        sem_rows = store.get_other_semesters(identity, course_id)
        other_semesters = [
            {
                "course_id": cid,
                "semester_key": sk,
                "semester_display": format_semester(sk),
            }
            for cid, sk in sem_rows
        ]

    return templates.TemplateResponse(
        request,
        "course.html",
        {
            "course": course,
            "other_semesters": other_semesters,
            "last_updated": _db_last_updated(),
        },
    )


@app.get("/stats", response_class=HTMLResponse)
@limiter.limit("30/minute")
async def stats_page(request: Request) -> HTMLResponse:
    """Render the stats page."""
    store = _get_store()
    total = store.course_count()
    semesters = store.semester_counts()

    # Get course type distribution
    type_counts = _get_type_counts(store)
    campus_counts = _get_campus_counts(store)

    return templates.TemplateResponse(
        request,
        "stats.html",
        {
            "total_courses": total,
            "semesters": semesters,
            "type_counts": type_counts,
            "campus_counts": campus_counts,
            "last_updated": _db_last_updated(),
            "update_info": _get_update_info(),
        },
    )


# ── JSON API ───────────────────────────────────────────────────────────────


@app.get("/api/search")
@limiter.limit("30/minute")
async def api_search(  # noqa: PLR0913
    request: Request,  # noqa: ARG001 - required by slowapi
    q: Annotated[
        str,
        Query(min_length=1, max_length=_MAX_QUERY_LENGTH, description="Search query"),
    ],
    campus: Annotated[str | None, Query(max_length=50)] = None,
    type: Annotated[str | None, Query(max_length=10)] = None,  # noqa: A002
    semester: Annotated[str | None, Query(max_length=10)] = None,
    mode: Annotated[str, Query(pattern="^(keyword|semantic|hybrid)$")] = "hybrid",
    limit: Annotated[int, Query(ge=1, le=_MAX_LIMIT)] = 20,
    offset: Annotated[int, Query(ge=0, le=2000)] = 0,
) -> dict[str, Any]:
    """Search courses by keyword."""
    store = _get_store()
    q = _sanitize_query(q)
    campus_clean = campus.strip().lower() if campus else None
    type_clean = type.strip().upper() if type else None

    # Fetch extra results to allow post-filtering by semester and offset pagination.
    # FTS and semantic both handle large limits efficiently (FTS via SQL LIMIT,
    # semantic processes all embeddings regardless), so we can be generous.
    # Cap at 2500 to prevent a large offset from causing the DB to scan and
    # materialise thousands of rows it will immediately discard.
    fetch_limit = min(limit + offset + 200, 2500)

    if mode == "keyword":
        results = fulltext_search(
            store,
            q,
            course_type=type_clean,
            campus=campus_clean,
            limit=fetch_limit,
        )
    elif mode == "semantic":
        results = semantic_search(
            store,
            q,
            course_type=type_clean,
            campus=campus_clean,
            limit=fetch_limit,
        )
    else:
        results = hybrid_search(
            store,
            q,
            course_type=type_clean,
            campus=campus_clean,
            limit=fetch_limit,
        )

    # Post-filter by semester if requested
    # A result matches if the displayed course OR any of its other_semesters
    # includes the requested semester.
    if semester:
        sem_clean = semester.strip().upper()
        results = [
            r
            for r in results
            if r.course.semester_key == sem_clean or sem_clean in r.other_semesters
        ]

    total_count = len(results)
    results = results[offset : offset + limit]

    return {
        "query": q,
        "mode": mode,
        "count": len(results),
        "total_count": total_count,
        "has_more": offset + limit < total_count,
        "offset": offset,
        "results": [_result_to_dict(r) for r in results],
    }


@app.get("/api/course/{course_id}")
@limiter.limit("60/minute")
async def api_course(request: Request, course_id: int) -> dict[str, Any]:  # noqa: ARG001
    """Get full course details by ID."""
    store = _get_store()
    row = store.get_course(course_id)
    if not row:
        raise HTTPException(status_code=404, detail="Course not found")
    return _course_to_dict(row)


@app.get("/api/course/{course_id}/schedule")
@limiter.limit("30/minute")
async def api_course_schedule(
    request: Request,  # noqa: ARG001
    course_id: int,
) -> dict[str, Any]:
    """Fetch live schedule/room data from TUMonline for a course."""
    from tum_lecture_finder.config import COURSE_GROUPS_URL

    url = f"{COURSE_GROUPS_URL}/{course_id}"
    try:
        async with httpx.AsyncClient(
            headers={"Accept": "application/json"},
            timeout=httpx.Timeout(15.0),
        ) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            data = resp.json()
    except httpx.HTTPError as exc:
        log.warning("schedule_fetch_error", course_id=course_id, error=str(exc))
        return {"appointments": [], "error": "TUMonline unavailable"}

    return {"appointments": _parse_appointments(data)}


@app.get("/health")
async def health() -> dict[str, str]:
    """Lightweight health check for Docker / reverse-proxy probing.

    Returns HTTP 200 when the application is running and the database is
    accessible.  Does not require authentication or rate limiting.
    """
    store = _get_store()
    # A cheap query to verify the DB connection is alive
    store.course_count()
    return {"status": "ok", "db": "ok"}


@app.get("/api/stats")
@limiter.limit("30/minute")
async def api_stats(request: Request) -> dict[str, Any]:  # noqa: ARG001
    """Get database statistics."""
    store = _get_store()
    total = store.course_count()
    semesters = store.semester_counts()
    type_counts = _get_type_counts(store)
    campus_counts = _get_campus_counts(store)

    result: dict[str, Any] = {
        "total_courses": total,
        "semesters": [
            {"key": k, "display": format_semester(k), "count": c} for k, c in semesters
        ],
        "course_types": type_counts,
        "campuses": campus_counts,
    }
    update_info = _get_update_info()
    if update_info:
        result["last_update"] = update_info
    return result


@app.get("/api/filters")
@limiter.limit("30/minute")
async def api_filters(request: Request) -> dict[str, Any]:  # noqa: ARG001
    """Get available filter values for dropdowns."""
    store = _get_store()
    semesters = store.semester_counts()
    type_counts = _get_type_counts(store)
    campus_counts = _get_campus_counts(store)

    return {
        "semesters": [{"key": k, "display": format_semester(k), "count": c} for k, c in semesters],
        "course_types": type_counts,
        "campuses": campus_counts,
    }


# ── Internal helpers ───────────────────────────────────────────────────────

# Cache entries: (expiry_monotonic_time, data).  TTL prevents stale counts
# after a scheduled DB update in a long-running container.
_CACHE_TTL = 3600.0  # 1 hour

_type_counts_cache: tuple[float, list[dict[str, Any]]] | None = None
_campus_counts_cache: tuple[float, list[dict[str, Any]]] | None = None


def _get_type_counts(store: CourseStore) -> list[dict[str, Any]]:
    """Get course type distribution, cached with a 1-hour TTL."""
    global _type_counts_cache  # noqa: PLW0603
    now = time.monotonic()
    if _type_counts_cache is None or now >= _type_counts_cache[0]:
        data = [{"type": t, "count": c} for t, c in store.type_counts()]
        _type_counts_cache = (now + _CACHE_TTL, data)
    return _type_counts_cache[1]


def _get_campus_counts(store: CourseStore) -> list[dict[str, Any]]:
    """Get campus distribution, cached with a 1-hour TTL."""
    global _campus_counts_cache  # noqa: PLW0603
    now = time.monotonic()
    if _campus_counts_cache is None or now >= _campus_counts_cache[0]:
        data = [
            {"campus": ca, "display": _campus_display_name(ca), "count": c}
            for ca, c in store.campus_counts()
        ]
        _campus_counts_cache = (now + _CACHE_TTL, data)
    return _campus_counts_cache[1]


def run_server(host: str = "127.0.0.1", port: int = 8000) -> None:
    """Start the web server.

    Args:
        host: Bind address.
        port: Listen port.

    """
    import uvicorn

    uvicorn.run(app, host=host, port=port, server_header=False)
