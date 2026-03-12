"""Application-wide paths and constants."""

from pathlib import Path

# ── Data directory ──────────────────────────────────────────────────────────
DATA_DIR: Path = Path.home() / ".tum_lecture_finder"
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
    import datetime  # noqa: PLC0415

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

    """
    yy = int(key[:-1])
    kind = key[-1]
    century = 2000
    year = century + yy
    if kind.upper() == "W":
        return f"Winter {year}/{(year + 1) % 100:02d}"
    return f"Summer {year}"


# ── BM25 column weights for FTS5 ──────────────────────────────────────────
# Order must match the column order in the courses_fts virtual table:
#   course_number, title_de, title_en, content_de, content_en,
#   objectives_de, objectives_en, prerequisites, literature,
#   organisation, instructors
BM25_WEIGHTS = (10.0, 10.0, 10.0, 2.0, 2.0, 1.0, 1.0, 1.0, 0.5, 0.5, 0.5)
