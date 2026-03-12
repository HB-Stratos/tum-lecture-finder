"""Async HTTP fetcher for TUMonline course data."""

from __future__ import annotations

import asyncio
import logging
import random
import re
from typing import TYPE_CHECKING, Any

import httpx

from tum_lecture_finder.config import (
    COURSE_GROUPS_URL,
    COURSES_URL,
    DEFAULT_RECENT_SEMESTERS,
    NAV_API_SEARCH_URL,
    PAGE_SIZE,
    SEMESTERS_URL,
)
from tum_lecture_finder.models import Course

if TYPE_CHECKING:
    from collections.abc import Callable

log = logging.getLogger(__name__)

_MAX_RETRIES = 3
_RETRY_BACKOFF = (2.0, 4.0, 8.0)


async def _get_with_retry(
    client: httpx.AsyncClient,
    url: str,
    *,
    retries: int = _MAX_RETRIES,
) -> httpx.Response:
    """GET with exponential back-off on transient failures.

    Retries on timeouts, connection errors, 429, and 5xx responses.

    Args:
        client: The async HTTP client.
        url: Request URL.
        retries: Maximum number of attempts.

    Returns:
        The successful response.

    Raises:
        httpx.HTTPError: After all retries are exhausted.

    """
    last_exc: httpx.HTTPError | None = None
    for attempt in range(retries):
        try:
            resp = await client.get(url)
            if resp.status_code in {429, 503}:
                # Server says slow down — treat like a transient error
                msg = f"Server returned {resp.status_code}"
                raise httpx.HTTPStatusError(
                    msg,
                    request=resp.request,
                    response=resp,
                )
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            last_exc = exc
            if attempt < retries - 1:
                jitter = random.uniform(0, _RETRY_BACKOFF[attempt] * 0.5)  # noqa: S311
                await asyncio.sleep(_RETRY_BACKOFF[attempt] + jitter)
                continue
            raise
        else:
            return resp
    raise last_exc  # type: ignore[misc]  # unreachable but keeps mypy happy


# ── JSON helpers ───────────────────────────────────────────────────────────


def _lang_value(obj: dict[str, Any] | None, lang: str = "de") -> str:
    """Extract a translated string from a TUMonline langdata structure.

    Args:
        obj: A dict with ``translations.translation`` list.
        lang: The desired language key (``"de"`` or ``"en"``).

    Returns:
        The translated string, or ``""`` if not found.

    """
    if not obj:
        return ""
    translations = obj.get("translations", {}).get("translation", [])
    if isinstance(translations, list):
        for t in translations:
            if isinstance(t, dict) and t.get("lang") == lang and t.get("value"):
                return t["value"]
    return obj.get("value", "") or ""


def _parse_course_list_item(item: dict[str, Any]) -> Course:
    """Parse a course from the list endpoint into a :class:`Course`.

    The semester key is extracted from the ``semesterDto`` in the API response
    rather than trusting the request parameter (which the API ignores).

    Args:
        item: A single course dict from the ``courses`` array.

    Returns:
        A partially populated :class:`Course` (no descriptions yet).

    """
    # Extract actual semester from API response
    semester_dto = item.get("semesterDto", {})
    semester_key = semester_dto.get("key", "") if semester_dto else ""

    instructors = []
    for lec in item.get("lectureships", []):
        identity = lec.get("identityLibDto", {})
        first = identity.get("firstName", "")
        last = identity.get("lastName", "")
        if first or last:
            instructors.append(f"{first} {last}".strip())

    course_type_dto = item.get("courseTypeDto", {})
    type_key = course_type_dto.get("key", "") if course_type_dto else ""

    sws_value = ""
    for norm in item.get("courseNormConfigs", []):
        if norm.get("key") == "SST":
            sws_value = norm.get("value", "")
            break

    org = item.get("organisationResponsibleDto", {})
    org_name = _lang_value(org.get("name")) if org else ""

    lang_parts = []
    for lang_dto in item.get("courseLanguageDtos", []):
        ld = lang_dto.get("languageDto", {})
        if ld and ld.get("key"):
            lang_parts.append(ld["key"])

    return Course(
        course_id=item["id"],
        semester_key=semester_key,
        course_number=item.get("courseNumber", {}).get("courseNumber", ""),
        title_de=_lang_value(item.get("courseTitle"), "de"),
        title_en=_lang_value(item.get("courseTitle"), "en"),
        course_type=type_key,
        sws=sws_value,
        organisation=org_name,
        instructors=", ".join(instructors),
        language=",".join(lang_parts),
        identity_code_id=item.get("identityCodeId", 0) or 0,
    )


def _merge_detail(course: Course, detail: dict[str, Any]) -> Course:
    """Merge description fields from the detail endpoint into a course.

    Args:
        course: The course to enrich.
        detail: The raw JSON from the detail endpoint.

    Returns:
        The same course object, mutated with description fields.

    """
    resource_list = detail.get("resource", [])
    if not resource_list:
        return course
    content = resource_list[0].get("content", {})
    detail_dto = content.get("cpCourseDetailDto", {})
    desc = detail_dto.get("cpCourseDescriptionDto", {})

    course.content_de = _lang_value(desc.get("courseContent"), "de")
    course.content_en = _lang_value(desc.get("courseContent"), "en")
    course.objectives_de = _lang_value(desc.get("courseObjective"), "de")
    course.objectives_en = _lang_value(desc.get("courseObjective"), "en")
    course.prerequisites = _lang_value(desc.get("previousKnowledge"), "de")

    add_info = desc.get("additionalInformation", {})
    course.literature = _lang_value(add_info.get("recommendedLiterature"), "de")

    # Also pick up organisation from detail if list didn't have it
    if not course.organisation:
        course_dto = detail_dto.get("cpCourseDto", {})
        org = course_dto.get("organisationResponsibleDto", {})
        course.organisation = _lang_value(org.get("name")) if org else ""

    return course


_BUILDING_CODE_RE = re.compile(r"\((\d{4})\.\w+\.\w+\)")


def _extract_building_codes(groups_data: dict[str, Any]) -> list[str]:
    """Extract unique 4-digit building codes from course group appointment data.

    Scans ``resourceName`` fields for room identifiers like
    ``"Hörsaal (8120.EG.001)"`` and returns the set of building codes found.

    Args:
        groups_data: JSON response from the ``courseGroups/firstGroups`` endpoint.

    Returns:
        List of unique 4-digit building code strings.

    """
    codes: set[str] = set()
    for group in groups_data.get("courseGroupDtos", []):
        for apt in group.get("appointmentDtos", []):
            resource_name = apt.get("resourceName", "")
            match = _BUILDING_CODE_RE.search(resource_name)
            if match:
                codes.add(match.group(1))
    return sorted(codes)


def _parse_campus_from_subtext(subtext: str) -> str:
    """Parse a campus label from a TUM NavigaTUM room subtext.

    Room subtexts follow the format ``"campus-label, Building Name"`` where
    campus labels are always lowercase (e.g. ``"garching"``,
    ``"stammgelände"``).  Non-standard locations like
    ``"Garmisch-Partenkirchen"`` start with an uppercase letter.

    Args:
        subtext: The ``subtext`` field from a NavigaTUM room search result.

    Returns:
        The campus label (lowercase), or ``""`` if unparseable.

    """
    if not subtext:
        return ""
    parts = subtext.split(", ", 1)
    if len(parts) == 2 and parts[0] and parts[0][0].islower():  # noqa: PLR2004
        return parts[0]
    # Non-standard location: take text before first " (" and lowercase it
    paren_idx = subtext.find(" (")
    if paren_idx > 0:
        return subtext[:paren_idx].lower()
    return subtext.lower()


async def _resolve_building_campus(
    client: httpx.AsyncClient,
    building_code: str,
) -> str:
    """Look up the campus for a building code via the TUM NavigaTUM API.

    Queries the search endpoint for the building code and extracts the campus
    label from room result subtexts.

    Args:
        client: An ``httpx.AsyncClient`` instance.
        building_code: A 4-digit building code (e.g. ``"5602"``).

    Returns:
        The campus label (e.g. ``"garching"``) or ``""`` if unknown.

    """
    try:
        resp = await client.get(
            NAV_API_SEARCH_URL,
            params={"q": building_code},
            timeout=10.0,
        )
        resp.raise_for_status()
    except httpx.HTTPError:
        log.debug("NavigaTUM lookup failed for building %s", building_code)
        return ""

    data = resp.json()
    prefix = building_code + "."
    for section in data.get("sections", []):
        if section.get("facet") != "rooms":
            continue
        for entry in section.get("entries", []):
            if entry.get("id", "").startswith(prefix):
                campus = _parse_campus_from_subtext(entry.get("subtext", ""))
                if campus:
                    return campus
    return ""


async def _resolve_all_buildings(
    client: httpx.AsyncClient,
    codes: set[str],
    *,
    concurrency: int = 5,
) -> dict[str, str]:
    """Resolve a batch of building codes to campus labels via NavigaTUM.

    Args:
        client: An ``httpx.AsyncClient`` instance.
        codes: Set of 4-digit building codes to resolve.
        concurrency: Max parallel NavigaTUM requests.

    Returns:
        Dict mapping building codes to campus labels.

    """
    if not codes:
        return {}

    results: dict[str, str] = {}
    sem = asyncio.Semaphore(concurrency)

    async def _resolve(code: str) -> None:
        async with sem:
            results[code] = await _resolve_building_campus(client, code)

    await asyncio.gather(*[_resolve(c) for c in sorted(codes)])
    return results


# ── async fetch pipeline ──────────────────────────────────────────────────


async def _fetch_available_semesters(client: httpx.AsyncClient) -> list[dict]:
    """Fetch the list of available semesters from the TUMonline API.

    Args:
        client: An ``httpx.AsyncClient`` instance.

    Returns:
        List of semester dicts with ``id``, ``key``, ``isSelected``, etc.,
        ordered most-recent first (as returned by the API).

    """
    resp = await client.get(SEMESTERS_URL)
    resp.raise_for_status()
    data = resp.json()
    return data.get("semesters", data)


async def _fetch_course_list(
    client: httpx.AsyncClient,
    *,
    semester_id: int | None = None,
    on_progress: Callable[[int, int], None] | None = None,
) -> list[Course]:
    """Fetch the paginated course list, optionally filtered by semester.

    When *semester_id* is given the request uses the ``$filter`` matrix-param
    format the TUMonline Angular frontend expects
    (``$filter=termId-eq=<id>``).  Without it the server returns its current
    default semester.

    Args:
        client: An ``httpx.AsyncClient`` instance.
        semester_id: Numeric term id to filter by (e.g. ``204`` for 25S).
        on_progress: Optional callback ``(fetched, total)`` called per page.

    Returns:
        List of :class:`Course` objects (without descriptions).

    """
    skip = 0

    def _build_url(skip_val: int) -> str:
        parts = [f"{COURSES_URL}?"]
        if semester_id is not None:
            parts.append(f"$filter=termId-eq={semester_id}&")
        parts.append(f"$skip={skip_val}&$top={PAGE_SIZE}")
        return "".join(parts)

    # First request to learn total count
    resp = await client.get(_build_url(skip))
    resp.raise_for_status()
    data = resp.json()
    total = data.get("totalCount", 0)

    courses: list[Course] = [_parse_course_list_item(item) for item in data.get("courses", [])]

    if on_progress:
        on_progress(len(courses), total)

    # Fetch remaining pages
    while len(courses) < total:
        skip = len(courses)
        resp = await client.get(_build_url(skip))
        resp.raise_for_status()
        data = resp.json()
        courses.extend(_parse_course_list_item(item) for item in data.get("courses", []))
        if on_progress:
            on_progress(len(courses), total)
        if not data.get("courses"):
            break

    return courses


async def _fetch_course_detail(
    client: httpx.AsyncClient,
    course_id: int,
    semaphore: asyncio.Semaphore,
) -> dict[str, Any] | None:
    """Fetch the detail JSON for a single course.

    Args:
        client: An ``httpx.AsyncClient`` instance.
        course_id: The TUMonline course id.
        semaphore: Concurrency limiter.

    Returns:
        The parsed JSON dict, or ``None`` on error.

    """
    async with semaphore:
        return await _fetch_course_detail_raw(client, course_id)


async def _fetch_course_detail_raw(
    client: httpx.AsyncClient,
    course_id: int,
) -> dict[str, Any] | None:
    """Fetch the detail JSON for a single course (no semaphore).

    Args:
        client: An ``httpx.AsyncClient`` instance.
        course_id: The TUMonline course id.

    Returns:
        The parsed JSON dict, or ``None`` on error.

    """
    try:
        resp = await _get_with_retry(client, f"{COURSES_URL}/{course_id}")
    except httpx.HTTPError as exc:
        log.warning("Failed to fetch details for course %s (%s)", course_id, exc)
        return None
    else:
        return resp.json()


async def _fetch_course_groups(
    client: httpx.AsyncClient,
    course_id: int,
    semaphore: asyncio.Semaphore,
) -> dict[str, Any] | None:
    """Fetch course groups (appointments/rooms) for a single course.

    Args:
        client: An ``httpx.AsyncClient`` instance.
        course_id: The TUMonline course id.
        semaphore: Concurrency limiter.

    Returns:
        The parsed JSON dict, or ``None`` on error.

    """
    async with semaphore:
        return await _fetch_course_groups_raw(client, course_id)


async def _fetch_course_groups_raw(
    client: httpx.AsyncClient,
    course_id: int,
) -> dict[str, Any] | None:
    """Fetch course groups (appointments/rooms) for a single course (no semaphore).

    Args:
        client: An ``httpx.AsyncClient`` instance.
        course_id: The TUMonline course id.

    Returns:
        The parsed JSON dict, or ``None`` on error.

    """
    try:
        resp = await _get_with_retry(client, f"{COURSE_GROUPS_URL}/{course_id}")
    except httpx.HTTPError:
        log.debug("No course groups for course %s", course_id)
        return None
    else:
        return resp.json()


async def _fetch_details(
    client: httpx.AsyncClient,
    courses: list[Course],
    concurrency: int,
    on_detail_progress: Callable[[int, int], None] | None,
) -> dict[int, list[str]]:
    """Fetch course details and room data for all courses concurrently.

    Args:
        client: An ``httpx.AsyncClient`` instance.
        courses: Courses to enrich (mutated in-place with descriptions).
        concurrency: Max parallel requests.
        on_detail_progress: Callback ``(fetched, total)`` for progress.

    Returns:
        Mapping of course_id → list of building codes extracted from rooms.

    """
    total = len(courses)
    sem = asyncio.Semaphore(concurrency)
    done_count = 0
    course_buildings: dict[int, list[str]] = {}

    async def _fetch_and_merge(course: Course) -> None:
        nonlocal done_count
        async with sem:
            detail_resp, groups_resp = await asyncio.gather(
                _fetch_course_detail_raw(client, course.course_id),
                _fetch_course_groups_raw(client, course.course_id),
            )
        if detail_resp:
            _merge_detail(course, detail_resp)
        if groups_resp:
            codes = _extract_building_codes(groups_resp)
            if codes:
                course_buildings[course.course_id] = codes
        done_count += 1
        if on_detail_progress:
            on_detail_progress(done_count, total)

    if on_detail_progress:
        on_detail_progress(0, total)

    await asyncio.gather(*[_fetch_and_merge(c) for c in courses])
    return course_buildings


async def _assign_campuses(
    client: httpx.AsyncClient,
    courses: list[Course],
    course_buildings: dict[int, list[str]],
    building_cache: dict[str, str],
    on_resolve_progress: Callable[[int, int], None] | None,
) -> None:
    """Resolve building codes and assign campus labels to courses.

    Unknown building codes are queried via NavigaTUM; results are merged into
    *building_cache* (mutated in-place).

    Args:
        client: An ``httpx.AsyncClient`` instance.
        courses: Courses to update (mutated in-place).
        course_buildings: Mapping of course_id → building codes.
        building_cache: Mutable dict of building_code → campus.
        on_resolve_progress: Callback ``(resolved, total)`` for progress.

    """
    all_codes = {code for codes in course_buildings.values() for code in codes}
    unknown_codes = all_codes - set(building_cache.keys())

    if unknown_codes:
        log.info(
            "Resolving %d/%d building codes via NavigaTUM…",
            len(unknown_codes),
            len(all_codes),
        )
        resolved = await _resolve_all_buildings(client, unknown_codes)
        building_cache.update(resolved)
        if on_resolve_progress:
            on_resolve_progress(len(unknown_codes), len(unknown_codes))

    for course in courses:
        codes = course_buildings.get(course.course_id, [])
        if not codes:
            continue
        campus_counts: dict[str, int] = {}
        for code in codes:
            campus = building_cache.get(code, "")
            if campus:
                campus_counts[campus] = campus_counts.get(campus, 0) + 1
        if campus_counts:
            course.campus = max(campus_counts, key=campus_counts.get)  # type: ignore[arg-type]


async def fetch_courses(  # noqa: PLR0913
    *,
    semester_ids: list[int] | None = None,
    concurrency: int = 20,
    building_cache: dict[str, str] | None = None,
    on_list_progress: Callable[[int, int], None] | None = None,
    on_detail_progress: Callable[[int, int], None] | None = None,
    on_resolve_progress: Callable[[int, int], None] | None = None,
    on_semester: Callable[[str], None] | None = None,
) -> list[Course]:
    """Fetch courses (optionally from multiple semesters) including descriptions.

    When *semester_ids* is ``None`` the most recent semesters (controlled by
    :data:`DEFAULT_RECENT_SEMESTERS`) are fetched automatically.  Pass an
    explicit list of term ids to override.

    Building codes found in course room data are resolved to campus labels
    via the TUM NavigaTUM API.  Pass *building_cache* (a mutable dict) to
    avoid redundant API calls across runs; newly resolved codes are added
    to it in-place.

    Args:
        semester_ids: Numeric term ids to fetch.  ``None`` = auto-detect recent.
        concurrency: Max parallel detail requests.
        building_cache: Mutable dict of building_code → campus (updated in-place).
        on_list_progress: Callback ``(fetched, total)`` for the list phase.
        on_detail_progress: Callback ``(fetched, total)`` for the detail phase.
        on_resolve_progress: Callback ``(resolved, total)`` for building resolution.
        on_semester: Callback ``(semester_key)`` fired when a semester list starts.

    Returns:
        Fully populated list of :class:`Course` objects.

    """
    if building_cache is None:
        building_cache = {}

    async with httpx.AsyncClient(
        headers={"Accept": "application/json"},
        timeout=httpx.Timeout(30.0),
        limits=httpx.Limits(
            max_connections=concurrency,
            max_keepalive_connections=concurrency,
        ),
    ) as client:
        # Resolve semester ids when not given explicitly
        if semester_ids is None:
            semesters = await _fetch_available_semesters(client)
            semester_ids = [s["id"] for s in semesters[:DEFAULT_RECENT_SEMESTERS]]

        # Phase 1: course lists (sequential per semester, per-page progress)
        courses: list[Course] = []

        for i, sid in enumerate(semester_ids):
            base = len(courses)
            remaining = len(semester_ids) - i - 1  # type: ignore[arg-type]

            def _page_progress(
                fetched: int,
                page_total: int,
                _base: int = base,
                _remaining: int = remaining,
            ) -> None:
                est = _base + page_total + _remaining * 6500
                if on_list_progress:
                    on_list_progress(_base + fetched, est)

            batch = await _fetch_course_list(
                client,
                semester_id=sid,
                on_progress=_page_progress,
            )
            if batch and on_semester:
                on_semester(batch[0].semester_key)
            courses.extend(batch)

        # Final update with exact total
        if on_list_progress:
            on_list_progress(len(courses), len(courses))

        # Phase 2: course details + room/building code extraction
        course_buildings = await _fetch_details(
            client,
            courses,
            concurrency,
            on_detail_progress,
        )

        # Phase 3: resolve buildings & assign campuses
        await _assign_campuses(
            client,
            courses,
            course_buildings,
            building_cache,
            on_resolve_progress,
        )

    return courses


async def fetch_semester_list() -> list[dict]:
    """Return all semesters known to TUMonline (most recent first).

    Each dict contains at least ``id`` (int) and ``key`` (str, e.g. ``"25W"``).
    """
    async with httpx.AsyncClient(
        headers={"Accept": "application/json"},
        timeout=httpx.Timeout(30.0),
    ) as client:
        return await _fetch_available_semesters(client)


def resolve_semester_ids(
    semesters: list[dict],
    keys: list[str],
) -> list[int]:
    """Map semester key strings (e.g. ``["25S", "25W"]``) to numeric ids.

    Args:
        semesters: Full semester list from :func:`fetch_semester_list`.
        keys: Semester keys to look up.

    Returns:
        List of matching numeric ids.

    Raises:
        click.BadParameter: If any key is not found.

    """
    import click  # noqa: PLC0415

    lookup = {s["key"]: s["id"] for s in semesters}
    ids = []
    for k in keys:
        k_upper = k.upper()
        if k_upper not in lookup:
            msg = f"Unknown semester '{k}'. Available: {', '.join(list(lookup)[:10])}"
            raise click.BadParameter(msg, param_hint="'--semester'")
        ids.append(lookup[k_upper])
    return ids
