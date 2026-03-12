"""FastAPI web application for TUM Lecture Finder."""

from __future__ import annotations

import logging
import re
from contextlib import asynccontextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Any

import httpx
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address
from starlette.responses import JSONResponse, Response

from tum_lecture_finder.config import DB_PATH, format_semester
from tum_lecture_finder.search import (
    fulltext_search,
    hybrid_search,
    semantic_search,
)
from tum_lecture_finder.storage import CourseStore, row_to_course

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable

    from tum_lecture_finder.models import SearchResult

log = logging.getLogger(__name__)

# ── Paths ──────────────────────────────────────────────────────────────────
_HERE = Path(__file__).parent
_TEMPLATES_DIR = _HERE / "templates"
_STATIC_DIR = _HERE / "static"

# ── Rate limiter ───────────────────────────────────────────────────────────
limiter = Limiter(key_func=get_remote_address)

# ── Global store (initialised on startup) ──────────────────────────────────
_store: CourseStore | None = None


def _get_store() -> CourseStore:
    """Return the global CourseStore instance."""
    if _store is None:
        msg = "Database not initialised"
        raise RuntimeError(msg)
    return _store


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:  # noqa: ARG001
    """Open DB on startup, preload model, close on shutdown."""
    global _store  # noqa: PLW0603
    _store = CourseStore(check_same_thread=False)
    # Preload the sentence-transformers model so first search is fast
    log.info("Pre-loading semantic search model…")
    from tum_lecture_finder.search import _get_model  # noqa: PLC0415

    _get_model()
    log.info("Model loaded.")
    yield
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


@app.exception_handler(RateLimitExceeded)
async def _rate_limit_handler(request: Request, exc: RateLimitExceeded) -> Response:  # noqa: ARG001
    return JSONResponse(
        status_code=429,
        content={"detail": "Rate limit exceeded. Please slow down."},
    )


# Security headers middleware
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
    # CSP allows inline styles for Dark Reader compatibility
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; "
        "script-src 'self'; "
        "style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data:; "
        "font-src 'self'; "
        "connect-src 'self'; "
        "frame-ancestors 'none'"
    )
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
            from datetime import datetime  # noqa: PLC0415

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
            appointments.append({
                "weekday": weekday,
                "time": time_range,
                "room": room,
                "room_link": _extract_room_link(room),
            })
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
    """Return the last-modified timestamp of the SQLite DB file."""
    from datetime import UTC, datetime  # noqa: PLC0415

    try:
        mtime = DB_PATH.stat().st_mtime
        dt = datetime.fromtimestamp(mtime, tz=UTC)
        return dt.strftime("%Y-%m-%d %H:%M UTC")
    except OSError:
        return "unknown"


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
    offset: Annotated[int, Query(ge=0, le=10000)] = 0,
) -> dict[str, Any]:
    """Search courses by keyword."""
    store = _get_store()
    q = _sanitize_query(q)
    campus_clean = campus.strip().lower() if campus else None
    type_clean = type.strip().upper() if type else None

    # Fetch extra results to allow post-filtering by semester and offset pagination.
    # FTS and semantic both handle large limits efficiently (FTS via SQL LIMIT,
    # semantic processes all embeddings regardless), so we can be generous.
    fetch_limit = limit + offset + 200

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
    from tum_lecture_finder.config import COURSE_GROUPS_URL  # noqa: PLC0415

    url = f"{COURSE_GROUPS_URL}/{course_id}"
    try:
        async with httpx.AsyncClient(
            headers={"Accept": "application/json"},
            timeout=httpx.Timeout(15.0),
        ) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            data = resp.json()
    except httpx.HTTPError:
        return {"appointments": []}

    return {"appointments": _parse_appointments(data)}


@app.get("/api/stats")
@limiter.limit("30/minute")
async def api_stats(request: Request) -> dict[str, Any]:  # noqa: ARG001
    """Get database statistics."""
    store = _get_store()
    total = store.course_count()
    semesters = store.semester_counts()
    type_counts = _get_type_counts(store)
    campus_counts = _get_campus_counts(store)

    return {
        "total_courses": total,
        "semesters": [{"key": k, "display": format_semester(k), "count": c} for k, c in semesters],
        "course_types": type_counts,
        "campuses": campus_counts,
    }


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

_type_counts_cache: tuple[int, list[dict[str, Any]]] | None = None
_campus_counts_cache: tuple[int, list[dict[str, Any]]] | None = None


def _get_type_counts(store: CourseStore) -> list[dict[str, Any]]:
    """Get course type distribution (cached per store instance)."""
    global _type_counts_cache  # noqa: PLW0603
    if _type_counts_cache is None or _type_counts_cache[0] != id(store):
        data = [{"type": t, "count": c} for t, c in store.type_counts()]
        _type_counts_cache = (id(store), data)
    return _type_counts_cache[1]


def _get_campus_counts(store: CourseStore) -> list[dict[str, Any]]:
    """Get campus distribution (cached per store instance)."""
    global _campus_counts_cache  # noqa: PLW0603
    if _campus_counts_cache is None or _campus_counts_cache[0] != id(store):
        data = [
            {"campus": ca, "display": _campus_display_name(ca), "count": c}
            for ca, c in store.campus_counts()
        ]
        _campus_counts_cache = (id(store), data)
    return _campus_counts_cache[1]


def run_server(host: str = "127.0.0.1", port: int = 8000) -> None:
    """Start the web server.

    Args:
        host: Bind address.
        port: Listen port.

    """
    import uvicorn  # noqa: PLC0415

    uvicorn.run(app, host=host, port=port)
