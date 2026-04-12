"""Application-wide paths and constants."""

import logging
from pathlib import Path

log = logging.getLogger(__name__)


def get_project_root() -> Path:
    """Return the project root as a pathlib.Path object.

    Works no matter which file in the project you call it from.
    """
    # Start from the file that contains this function (or any file in your project)
    current = Path(__file__).resolve().parent   # .resolve() handles symlinks & makes it absolute

    # Walk up until we find a common project marker
    for parent in current.parents:              # .parents is a generator of ancestor directories
        # Common markers (add/remove as needed for your project)
        if (parent / ".git").exists():          # Almost every real project has this
            return parent
        if (parent / "pyproject.toml").exists():  # Modern Python projects (PEP 518+)
            return parent
        if (parent / "setup.py").exists():      # Older setuptools projects
            return parent
        if (parent / "requirements.txt").exists():
            return parent

    # Fallback (should rarely happen)
    msg = "Could not find project root (no marker found)"
    raise RuntimeError(msg)

# ── Data directory ──────────────────────────────────────────────────────────
DATA_DIR: Path = get_project_root() / "data"
DB_PATH: Path = DATA_DIR / "courses.db"

# ── TUMonline public REST API ──────────────────────────────────────────────
API_BASE = "https://campus.tum.de/tumonline/ee/rest/slc.tm.cp/student"
COURSES_URL = f"{API_BASE}/courses"
COURSE_GROUPS_URL = f"{API_BASE}/courseGroups/firstGroups"
SEMESTERS_URL = "https://campus.tum.de/tumonline/ee/rest/slc.lib.tm/semesters/student"
PAGE_SIZE = 100
DEFAULT_RECENT_SEMESTERS = 4

# ── TUM NavigaTUM API (building → campus resolution) ──────────────────────
NAV_API_SEARCH_URL = "https://nav.tum.de/api/search"

# ── Semester key helpers ───────────────────────────────────────────────────
# TUMonline semester keys look like "25W" (winter 25/26) or "25S" (summer 25).


def current_semester_key() -> str:
    """Return the semester key for the current calendar date.

    Winter semester: October - March  -> ``<YY>W``
    Summer semester: April - September -> ``<YY>S``
    """
    import datetime

    today = datetime.datetime.now(tz=datetime.UTC).date()
    year_short = today.year % 100
    if today.month >= 10:  # noqa: PLR2004 - Oct starts winter semester
        return f"{year_short}W"
    if today.month >= 4:  # noqa: PLR2004 - Apr starts summer semester
        return f"{year_short}S"
    # Jan-Mar belong to the *previous* year's winter semester.
    return f"{(year_short - 1) % 100}W"


def format_semester(key: str) -> str:
    """Format a semester key into a human-readable string.

    Args:
        key: A semester key like ``"25W"``.

    Returns:
        E.g. ``"Winter 2025/26"`` or ``"Summer 2025"``.
        Returns the raw key unchanged if it cannot be parsed.

    """
    if len(key) < 2 or key[-1].upper() not in {"W", "S"}:  # noqa: PLR2004
        if key:
            log.warning("malformed_semester_key: %s", key)
        return key
    try:
        yy = int(key[:-1])
    except ValueError:
        log.warning("malformed_semester_key: %s", key)
        return key
    kind = key[-1]
    year = (1900 + yy) if yy >= 50 else (2000 + yy)  # noqa: PLR2004
    if kind.upper() == "W":
        next_yy = (year + 1) % 100
        return f"Winter {year}/{next_yy:02d}"
    return f"Summer {year}"


def semester_sort_key(key: str) -> tuple[int, int]:
    """Return a ``(full_year, half)`` tuple for chronological semester ordering.

    Two-digit years >= 50 are treated as 1900s; years < 50 as 2000s.  This
    handles the century boundary so ``"99W"`` (Winter 1999) sorts before
    ``"00S"`` (Summer 2000) and correctly before ``"25W"`` (Winter 2025).

    Args:
        key: Semester key (e.g. ``"25W"`` or ``"99S"``).

    Returns:
        ``(full_year, semester_half)`` where summer = 0, winter = 1.

    """
    yy = int(key[:-1])
    kind = key[-1].upper()
    full_year = (1900 + yy) if yy >= 50 else (2000 + yy)  # noqa: PLR2004
    return (full_year, 0 if kind == "S" else 1)


def is_current_or_future_semester(key: str, current: str) -> bool:
    """Return True if *key* is the current semester or a future one.

    Args:
        key: Semester key to test (e.g. ``"26S"``).
        current: The current semester key (e.g. ``"25W"``).

    Returns:
        True if *key* >= *current* in chronological order.

    """
    return semester_sort_key(key) >= semester_sort_key(current)


# ── BM25 column weights for FTS5 ──────────────────────────────────────────
# Order must match the column order in the courses_fts virtual table:
#   course_number, title_de, title_en, content_de, content_en,
#   objectives_de, objectives_en, prerequisites, literature,
#   organisation, instructors
BM25_WEIGHTS = (10.0, 10.0, 10.0, 2.0, 2.0, 1.0, 1.0, 1.0, 0.5, 0.5, 0.5)
