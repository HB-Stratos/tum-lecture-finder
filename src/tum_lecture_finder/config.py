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


# ── TUM campuses (used for filtering) ─────────────────────────────────────
# Keywords matched case-insensitively against the organisation field.
CAMPUS_KEYWORDS: dict[str, list[str]] = {
    "garching": [
        "garching",
        "informatik",
        "maschinenwesen",
        "physik department",
        "department physics",
        "department chemistry",
        "department mathematics",
        "elektrotechnik",
        "department of",
        "raumfahrt",
        "aerospace",
        "spacecraft",
        "boltzmannstr",
        "lichtenberg",
        "walter schottky",
        "(tum school of computation",
        "(tum school of engineering",
        "(tum school of natural sciences",
    ],
    "münchen": [
        "stammgelände",
        "innenstadt",
        "munich",
        "münchen",
        "arcisstr",
        "architektur",
        "bauklimatik",
        "bauprozess",
        "hochbau",
        "städtebau",
        "landschafts",
        "kunstgeschichte",
        "sportwissenschaft",
        "political",
        "soziologie",
        "philosophie",
        "governance",
        "school of social",
    ],
    "freising": [
        "weihenstephan",
        "freising",
        "brau",
        "lebensmittel",
        "ernährung",
        "agrar",
        "forst",
        "landnutzung",
        "ökologie",
        "gartenbau",
        "holzforschung",
        "life sciences",
    ],
    "straubing": ["straubing", "tumcs", "nachwachsende"],
    "heilbronn": ["heilbronn"],
    "singapore": ["singapore", "tumcreate"],
}

# ── TUM building-code → campus mapping ─────────────────────────────────────
# The first digit of a 4-digit building code reliably identifies the campus.
# Derived from scanning room data across hundreds of courses.
BUILDING_PREFIX_CAMPUS: dict[str, str] = {
    "0": "münchen",
    "1": "münchen",
    "2": "münchen",
    "3": "münchen",
    "4": "freising",
    "5": "garching",
    "6": "garching",
    "7": "heilbronn",
    "8": "garching",
    "9": "other",
}

# ── BM25 column weights for FTS5 ──────────────────────────────────────────
# Order must match the column order in the courses_fts virtual table:
#   course_number, title_de, title_en, content_de, content_en,
#   objectives_de, objectives_en, prerequisites, literature,
#   organisation, instructors
BM25_WEIGHTS = (10.0, 10.0, 10.0, 2.0, 2.0, 1.0, 1.0, 1.0, 0.5, 0.5, 0.5)
