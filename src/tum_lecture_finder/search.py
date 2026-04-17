"""Full-text (FTS5) and semantic search across stored courses."""

from __future__ import annotations

import asyncio
import re
import threading
from typing import TYPE_CHECKING

import structlog

from tum_lecture_finder.models import Course, SearchResult
from tum_lecture_finder.storage import parse_other_semesters, row_to_course

if TYPE_CHECKING:
    from collections.abc import Callable

    from tum_lecture_finder.storage import CourseStore

log = structlog.get_logger(__name__)

_SNIPPET_MAX_LEN = 120  # max chars for a description excerpt


def _dedup_by_identity(
    results: list[SearchResult],
) -> list[SearchResult]:
    """Deduplicate results that share the same ``identity_code_id``.

    When multiple semesters of the same course appear in results, keep only the
    highest-scoring entry and annotate it with the other semester keys.  The
    most recent semester is always preferred as the displayed course.

    Each result's ``other_semesters`` list (pre-populated from the database)
    is merged so the final entry contains all known semesters.

    Args:
        results: Sorted search results (best first).

    Returns:
        Deduplicated list preserving order by score.

    """
    seen: dict[int, int] = {}  # identity_code_id -> index in output
    output: list[SearchResult] = []

    for r in results:
        iid = r.course.identity_code_id
        if iid and iid in seen:
            existing = output[seen[iid]]
            new_sem = r.course.semester_key
            old_sem = existing.course.semester_key
            # Merge other_semesters from the incoming result
            for s in r.other_semesters:
                if s and s not in existing.other_semesters:
                    existing.other_semesters.append(s)
            # Prefer the more recent semester as the displayed course
            if new_sem and old_sem and new_sem > old_sem:
                if old_sem not in existing.other_semesters:
                    existing.other_semesters.append(old_sem)
                existing.course = r.course
            elif new_sem and new_sem != old_sem and new_sem not in existing.other_semesters:
                existing.other_semesters.append(new_sem)
            # Remove the displayed semester from other_semesters
            displayed = existing.course.semester_key
            existing.other_semesters = [s for s in existing.other_semesters if s != displayed]
            existing.other_semesters.sort(reverse=True)
            continue
        idx = len(output)
        if iid:
            seen[iid] = idx
        output.append(r)

    return output


def _escape_fts_query(query: str) -> str:
    """Build an FTS5 query from a raw user string.

    Each token is double-quoted (escaping any internal ``"`` per the FTS5
    spec) and gets prefix matching (``*``) so partial words work.
    Tokens are combined with implicit AND.

    Quoting neutralises FTS5 operators (``AND``, ``OR``, ``NOT``, ``NEAR``)
    and special characters so arbitrary user input cannot cause syntax errors.

    Args:
        query: Raw user search string.

    Returns:
        An FTS5-safe query string.

    """
    # Strip everything except word characters (letters, digits, _) and whitespace
    cleaned = re.sub(r"[^\w\s]", " ", query, flags=re.UNICODE)
    tokens = cleaned.split()
    if not tokens:
        return '""'
    # Double-quote each token (escape internal " by doubling) with prefix *
    return " ".join(f'"{t.replace(chr(34), chr(34) + chr(34))}"*' for t in tokens)


def _extract_excerpt(text: str, token: str) -> str:
    """Extract a short excerpt from *text* around the first occurrence of *token*.

    Args:
        text: The source text to extract from.
        token: The search token to centre the excerpt around (lowercase).

    Returns:
        A trimmed excerpt string, or ``""`` if *token* is not found.

    """
    pos = text.lower().find(token)
    if pos == -1:
        return ""
    start = max(0, pos - 40)
    end = min(len(text), pos + len(token) + _SNIPPET_MAX_LEN - 40)
    excerpt = text[start:end].strip()
    # Trim to word boundaries
    if start > 0:
        space = excerpt.find(" ")
        if space != -1:
            excerpt = excerpt[space + 1 :]
    if end < len(text):
        space = excerpt.rfind(" ")
        if space > 0:  # guard: -1 means no space found; 0 would empty the string
            excerpt = excerpt[:space]
    return excerpt


def _generate_snippet(course: Course, query: str) -> str:
    """Generate a description excerpt when the query matches body text, not the title.

    If all query tokens already appear in the title, no snippet is generated
    (the title alone is informative enough).

    Args:
        course: The matched course.
        query: The raw user search string.

    Returns:
        A short excerpt from the description containing the match, or ``""``.

    """
    cleaned = re.sub(r"[^\w\s]", " ", query, flags=re.UNICODE)
    tokens = [t.lower() for t in cleaned.split() if t]
    if not tokens:
        return ""

    # Check if all tokens are already in the title
    title_lower = f"{course.title_de} {course.title_en}".lower()
    if all(t in title_lower for t in tokens):
        return ""

    # Search through description fields for a matching excerpt
    fields = [
        course.content_de,
        course.content_en,
        course.objectives_de,
        course.objectives_en,
        course.prerequisites,
        course.literature,
    ]

    for field in fields:
        if not field:
            continue
        for token in tokens:
            excerpt = _extract_excerpt(field, token)
            if excerpt:
                return excerpt

    return ""


def _matches_campus(course: Course, campus: str) -> bool:
    """Check whether a course matches a campus filter.

    Uses substring matching against the ``campus`` field so that e.g.
    ``"garching"`` matches ``"garching"``, ``"garching-hochbrück"``, etc.

    Args:
        course: The course to check.
        campus: A campus name (case-insensitive).

    Returns:
        True if the course belongs to the given campus.

    """
    if not course.campus:
        return False
    return campus.lower() in course.campus


def fulltext_search(
    store: CourseStore,
    query: str,
    *,
    course_type: str | None = None,
    campus: str | None = None,
    limit: int = 50,
) -> list[SearchResult]:
    """Run a full-text search using SQLite FTS5.

    Args:
        store: The course store to search.
        query: The user's search text.
        course_type: Optional type filter (e.g. ``"VO"``).
        campus: Optional campus filter (e.g. ``"garching"``).
        limit: Maximum number of results.

    Returns:
        Sorted list of :class:`SearchResult`.

    """
    fts_query = _escape_fts_query(query)
    rows = store.fulltext_search(
        fts_query,
        course_type=course_type,
        limit=limit * 3 if campus else limit * 2,  # over-fetch for filtering & dedup
    )

    results: list[SearchResult] = []
    for row, score in rows:
        course = row_to_course(row)
        if campus and not _matches_campus(course, campus):
            continue
        snippet = _generate_snippet(course, query)
        results.append(
            SearchResult(
                course=course,
                score=-score,
                snippet=snippet,
                other_semesters=parse_other_semesters(row),
            )
        )

    results = _dedup_by_identity(results)
    return results[:limit]


# ── Semantic search ────────────────────────────────────────────────────────

_model = None  # lazy-loaded sentence-transformers model
_model_lock = threading.Lock()

# ── Cached course data for semantic search ─────────────────────────────────
_course_cache: tuple[list[Course], dict[int, list[str]]] | None = None
_course_cache_lock = threading.Lock()


def _load_course_data(
    store: CourseStore,
) -> tuple[list[Course], dict[int, list[str]]]:
    """Load and cache courses + other_semesters for semantic search.

    Returns:
        Tuple of (courses, course_id → other_semesters mapping).

    """
    global _course_cache  # noqa: PLW0603
    if _course_cache is not None:
        return _course_cache
    with _course_cache_lock:
        if _course_cache is not None:  # re-check after acquiring lock
            return _course_cache
        all_rows = store.get_all_courses()
        courses = []
        other_sems: dict[int, list[str]] = {}
        for r in all_rows:
            c = row_to_course(r)
            courses.append(c)
            other_sems[c.course_id] = parse_other_semesters(r)
        _course_cache = (courses, other_sems)
    return _course_cache


def invalidate_course_cache() -> None:
    """Clear the in-memory course cache (call after database updates)."""
    global _course_cache  # noqa: PLW0603
    with _course_cache_lock:
        _course_cache = None


def _get_model() -> object:
    """Lazily load the sentence-transformers model.

    Returns:
        A SentenceTransformer model instance.

    """
    global _model  # noqa: PLW0603
    if _model is None:
        with _model_lock:
            if _model is None:  # re-check after acquiring lock
                from sentence_transformers import SentenceTransformer

                _model = SentenceTransformer("all-MiniLM-L6-v2")
    return _model


def ensure_model_loaded() -> None:
    """Pre-load the sentence-transformer model into memory.

    Calling this before :func:`build_embeddings` lets you warm up the model
    inside a quiet context (suppressing noisy HuggingFace output) while
    keeping the encoding step separate so a progress bar can be shown.

    """
    _get_model()


def build_embeddings(
    store: CourseStore,
    on_progress: Callable[[int, int], None] | None = None,
) -> int:
    """Pre-compute and cache embeddings for all stored courses.

    Args:
        store: The course store.
        on_progress: Optional callback invoked after each batch as
            ``on_progress(courses_done, courses_total)``.  When provided,
            encoding runs in batches of 256 so the caller can show a
            progress indicator; otherwise a single ``model.encode`` call
            is made without an external progress bar.

    Returns:
        Number of courses embedded.

    """
    import numpy as np

    model = _get_model()

    all_rows = store.get_all_courses()
    courses = [row_to_course(r) for r in all_rows]
    if not courses:
        return 0

    texts = [c.embedding_text for c in courses]
    course_ids = np.array([c.course_id for c in courses], dtype=np.int64)

    if on_progress is not None:
        batch_size = 256
        total = len(texts)
        chunks = []
        for i in range(0, total, batch_size):
            batch = texts[i : i + batch_size]
            chunk = model.encode(batch, normalize_embeddings=True, show_progress_bar=False)
            chunks.append(chunk)
            on_progress(min(i + batch_size, total), total)
        embeddings = np.vstack(chunks)
    else:
        embeddings = model.encode(texts, normalize_embeddings=True, show_progress_bar=False)

    store.save_embeddings(course_ids, embeddings.astype(np.float32))
    return len(courses)


def semantic_search(
    store: CourseStore,
    query: str,
    *,
    course_type: str | None = None,
    campus: str | None = None,
    limit: int = 20,
) -> list[SearchResult]:
    """Run a semantic similarity search over all stored courses.

    Uses pre-computed embeddings when available (via :func:`build_embeddings`),
    falling back to on-the-fly encoding.

    Args:
        store: The course store.
        query: The user's natural-language query.
        course_type: Optional type filter.
        campus: Optional campus filter.
        limit: Maximum results.

    Returns:
        Sorted list of :class:`SearchResult`.

    """
    import numpy as np

    model = _get_model()

    # Load all courses (needed for metadata regardless) — cached after first call
    all_courses, all_other_sems = _load_course_data(store)
    if not all_courses:
        return []

    # Try cached embeddings first
    cached = store.load_embeddings()
    if cached is not None:
        cached_ids, corpus_embeddings = cached
        # Build id→index map for the cache
        id_to_idx = {int(cid): i for i, cid in enumerate(cached_ids)}
        # Map courses to their cached embedding indices
        courses: list[Course] = []
        emb_indices: list[int] = []
        for c in all_courses:
            idx = id_to_idx.get(c.course_id)
            if idx is not None:
                courses.append(c)
                emb_indices.append(idx)
        corpus_embeddings = corpus_embeddings[emb_indices]
    else:
        log.warning("No cached embeddings — encoding all courses (slow). Run 'tlf build-index'.")
        courses = all_courses
        texts = [c.embedding_text for c in courses]
        corpus_embeddings = model.encode(
            texts,
            normalize_embeddings=True,
            show_progress_bar=False,
        )

    # Apply filters after loading embeddings (so indices stay aligned)
    if course_type or campus:
        mask = []
        for c in courses:
            keep = True
            if course_type and c.course_type.upper() != course_type.upper():
                keep = False
            if campus and not _matches_campus(c, campus):
                keep = False
            mask.append(keep)
        mask_arr = np.array(mask, dtype=np.bool_)
        courses = [c for c, m in zip(courses, mask, strict=True) if m]
        corpus_embeddings = corpus_embeddings[mask_arr]

    if not courses:
        return []

    # Encode query and compute similarities
    query_embedding = model.encode(query, normalize_embeddings=True, show_progress_bar=False)
    similarities = np.dot(corpus_embeddings, query_embedding)

    top_indices = np.argsort(similarities)[::-1][: limit * 2]
    min_score = 0.2
    results = [
        SearchResult(
            course=courses[i],
            score=float(similarities[i]),
            snippet=_generate_snippet(courses[i], query),
            other_semesters=all_other_sems.get(courses[i].course_id, []),
        )
        for i in top_indices
        if similarities[i] > min_score
    ]
    return _dedup_by_identity(results)[:limit]


def hybrid_search(  # noqa: PLR0913
    store: CourseStore,
    query: str,
    *,
    course_type: str | None = None,
    campus: str | None = None,
    limit: int = 20,
    semantic_weight: float = 0.5,
) -> list[SearchResult]:
    """Combine full-text and semantic search with weighted scoring.

    Args:
        store: The course store.
        query: The user's search text.
        course_type: Optional type filter.
        campus: Optional campus filter.
        limit: Maximum results.
        semantic_weight: Weight for semantic scores (0-1); FTS gets the rest.

    Returns:
        Merged and re-ranked list of :class:`SearchResult`.

    """
    fts_results = fulltext_search(
        store,
        query,
        course_type=course_type,
        campus=campus,
        limit=limit * 2,
    )
    sem_results = semantic_search(
        store,
        query,
        course_type=course_type,
        campus=campus,
        limit=limit * 2,
    )

    return _merge_hybrid(fts_results, sem_results, semantic_weight, limit)


def _merge_hybrid(
    fts_results: list[SearchResult],
    sem_results: list[SearchResult],
    semantic_weight: float,
    limit: int,
) -> list[SearchResult]:
    """Merge and re-rank FTS and semantic results with weighted scoring."""
    # Normalize FTS scores to 0-1
    fts_scores: dict[int, float] = {}
    if fts_results:
        max_fts = max(r.score for r in fts_results) or 1.0
        for r in fts_results:
            fts_scores[r.course.course_id] = r.score / max_fts

    # Semantic scores are already 0-1 (cosine similarity)
    sem_scores: dict[int, float] = {}
    for r in sem_results:
        sem_scores[r.course.course_id] = r.score

    # Merge
    all_courses: dict[int, Course] = {}
    snippets: dict[int, str] = {}
    other_sems: dict[int, list[str]] = {}
    for r in fts_results:
        all_courses[r.course.course_id] = r.course
        if r.snippet:
            snippets[r.course.course_id] = r.snippet
        other_sems[r.course.course_id] = r.other_semesters
    for r in sem_results:
        all_courses[r.course.course_id] = r.course
        if r.snippet and r.course.course_id not in snippets:
            snippets[r.course.course_id] = r.snippet
        if r.course.course_id not in other_sems:
            other_sems[r.course.course_id] = r.other_semesters

    combined: list[SearchResult] = []
    fts_weight = 1.0 - semantic_weight
    for cid, course in all_courses.items():
        fts_s = fts_scores.get(cid, 0.0) * fts_weight
        sem_s = sem_scores.get(cid, 0.0) * semantic_weight
        combined.append(
            SearchResult(
                course=course,
                score=fts_s + sem_s,
                snippet=snippets.get(cid, ""),
                other_semesters=other_sems.get(cid, []),
            )
        )

    combined.sort(key=lambda r: r.score, reverse=True)
    return _dedup_by_identity(combined)[:limit]


async def hybrid_search_async(  # noqa: PLR0913
    store: CourseStore,
    query: str,
    *,
    course_type: str | None = None,
    campus: str | None = None,
    limit: int = 20,
    semantic_weight: float = 0.5,
) -> list[SearchResult]:
    """Async version of :func:`hybrid_search` that runs FTS and semantic in parallel.

    Both searches are dispatched to the thread pool concurrently, giving a
    ~30-40 % wall-time improvement over the sequential version.
    """
    fts_coro = asyncio.to_thread(
        fulltext_search,
        store,
        query,
        course_type=course_type,
        campus=campus,
        limit=limit * 2,
    )
    sem_coro = asyncio.to_thread(
        semantic_search,
        store,
        query,
        course_type=course_type,
        campus=campus,
        limit=limit * 2,
    )
    fts_results, sem_results = await asyncio.gather(fts_coro, sem_coro)

    return _merge_hybrid(fts_results, sem_results, semantic_weight, limit)
