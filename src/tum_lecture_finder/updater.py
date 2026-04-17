"""Shared course-update pipeline used by both CLI and web scheduler."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import structlog

if TYPE_CHECKING:
    from collections.abc import Callable

    from tum_lecture_finder.storage import CourseStore

log = structlog.get_logger(__name__)


@dataclass
class UpdateResult:
    """Summary of a completed update run."""

    detailed: int = 0
    """Number of courses fetched with full details."""
    skipped: int = 0
    """Number of courses whose detail fetch was skipped."""
    stored: int = 0
    """Total courses upserted into the database."""
    semesters: set[str] = field(default_factory=set)
    """Semester keys found in the fetched courses."""


async def run_update(  # noqa: PLR0913
    store: CourseStore,
    semester_ids: list[int],
    *,
    concurrency: int = 20,
    skip_detail_ids: set[int] | None = None,
    on_list_progress: Callable[[int, int], None] | None = None,
    on_detail_progress: Callable[[int, int], None] | None = None,
    on_semester: Callable[[str], None] | None = None,
    rebuild_embeddings: bool = True,
    on_embeddings_progress: Callable[[int, int], None] | None = None,
) -> UpdateResult:
    """Run the full fetch → upsert → cross-ref → embed pipeline.

    This is the single source of truth for updating the course database,
    shared by the CLI ``update`` command, ``probe-semesters --fetch-future``,
    and the web scheduler.

    Args:
        store: The course store to update.
        semester_ids: Numeric TUMonline semester IDs to fetch.
        concurrency: Max parallel HTTP requests for fetching.
        skip_detail_ids: Course IDs to skip detail fetching for (incremental).
        on_list_progress: Callback ``(fetched, total)`` for course list progress.
        on_detail_progress: Callback ``(fetched, total)`` for detail fetch progress.
        on_semester: Callback ``(semester_key)`` when a semester is fetched.
        rebuild_embeddings: Whether to rebuild the semantic search index.
        on_embeddings_progress: Callback ``(done, total)`` for embedding progress.

    Returns:
        An :class:`UpdateResult` with counts for the caller to log/display.

    """
    from tum_lecture_finder.fetcher import fetch_courses

    building_cache = store.get_building_cache()

    result = await fetch_courses(
        semester_ids=semester_ids,
        concurrency=concurrency,
        building_cache=building_cache,
        skip_detail_ids=skip_detail_ids,
        on_list_progress=on_list_progress,
        on_detail_progress=on_detail_progress,
        on_semester=on_semester,
    )

    all_courses = result.detailed + result.list_only
    if not all_courses:
        return UpdateResult()

    count = store.upsert_courses(all_courses)
    store.compute_other_semesters()
    store.upsert_building_cache(building_cache)

    semesters_found = {c.semester_key for c in all_courses if c.semester_key}

    if rebuild_embeddings:
        from tum_lecture_finder.search import build_embeddings

        build_embeddings(store, on_progress=on_embeddings_progress)

    return UpdateResult(
        detailed=len(result.detailed),
        skipped=len(result.list_only),
        stored=count,
        semesters=semesters_found,
    )
