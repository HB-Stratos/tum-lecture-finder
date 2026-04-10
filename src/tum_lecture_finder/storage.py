"""SQLite + FTS5 persistence layer for course data."""

from __future__ import annotations

import sqlite3
from typing import TYPE_CHECKING

from tum_lecture_finder.config import BM25_WEIGHTS, DATA_DIR, DB_PATH
from tum_lecture_finder.models import Course

if TYPE_CHECKING:
    from collections.abc import Iterable
    from pathlib import Path

    import numpy as np
    from numpy.typing import NDArray

# Embeddings are stored alongside the database as a compact numpy file.
EMBEDDINGS_PATH = DATA_DIR / "embeddings.npz"

# ── SQL statements ─────────────────────────────────────────────────────────
_SCHEMA_VERSION = 7  # bump when schema changes

_CREATE_META = """\
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""

_CREATE_COURSES = """\
CREATE TABLE IF NOT EXISTS courses (
    course_id      INTEGER PRIMARY KEY,
    semester_key   TEXT    NOT NULL DEFAULT '',
    course_number  TEXT    NOT NULL DEFAULT '',
    title_de       TEXT    NOT NULL DEFAULT '',
    title_en       TEXT    NOT NULL DEFAULT '',
    course_type    TEXT    NOT NULL DEFAULT '',
    sws            TEXT    NOT NULL DEFAULT '',
    organisation   TEXT    NOT NULL DEFAULT '',
    instructors    TEXT    NOT NULL DEFAULT '',
    language       TEXT    NOT NULL DEFAULT '',
    campus             TEXT    NOT NULL DEFAULT '',
    identity_code_id   INTEGER NOT NULL DEFAULT 0,
    content_de         TEXT    NOT NULL DEFAULT '',
    content_en     TEXT    NOT NULL DEFAULT '',
    objectives_de  TEXT    NOT NULL DEFAULT '',
    objectives_en  TEXT    NOT NULL DEFAULT '',
    prerequisites  TEXT    NOT NULL DEFAULT '',
    literature     TEXT    NOT NULL DEFAULT '',
    other_semesters    TEXT    NOT NULL DEFAULT ''
);
"""

_CREATE_BUILDING_CACHE = """\
CREATE TABLE IF NOT EXISTS building_campuses (
    building_code TEXT PRIMARY KEY,
    campus        TEXT NOT NULL DEFAULT ''
);
"""

_CREATE_FTS = """\
CREATE VIRTUAL TABLE IF NOT EXISTS courses_fts USING fts5(
    course_number,
    title_de,
    title_en,
    content_de,
    content_en,
    objectives_de,
    objectives_en,
    prerequisites,
    literature,
    organisation,
    instructors,
    content = 'courses',
    content_rowid = 'rowid',
    tokenize = 'unicode61 remove_diacritics 2'
);
"""

_FTS_TRIGGERS = """\
CREATE TRIGGER IF NOT EXISTS courses_ai AFTER INSERT ON courses BEGIN
    INSERT INTO courses_fts(
        rowid, course_number, title_de, title_en, content_de, content_en,
        objectives_de, objectives_en, prerequisites, literature,
        organisation, instructors
    ) VALUES (
        new.rowid, new.course_number, new.title_de, new.title_en,
        new.content_de, new.content_en,
        new.objectives_de, new.objectives_en,
        new.prerequisites, new.literature,
        new.organisation, new.instructors
    );
END;

CREATE TRIGGER IF NOT EXISTS courses_ad AFTER DELETE ON courses BEGIN
    INSERT INTO courses_fts(
        courses_fts, rowid, course_number, title_de, title_en, content_de, content_en,
        objectives_de, objectives_en, prerequisites, literature,
        organisation, instructors
    ) VALUES (
        'delete', old.rowid, old.course_number, old.title_de, old.title_en,
        old.content_de, old.content_en,
        old.objectives_de, old.objectives_en,
        old.prerequisites, old.literature,
        old.organisation, old.instructors
    );
END;

CREATE TRIGGER IF NOT EXISTS courses_au
AFTER UPDATE OF
    course_number, title_de, title_en,
    content_de, content_en,
    objectives_de, objectives_en,
    prerequisites, literature,
    organisation, instructors
ON courses BEGIN
    INSERT INTO courses_fts(
        courses_fts, rowid, course_number, title_de, title_en, content_de, content_en,
        objectives_de, objectives_en, prerequisites, literature,
        organisation, instructors
    ) VALUES (
        'delete', old.rowid, old.course_number, old.title_de, old.title_en,
        old.content_de, old.content_en,
        old.objectives_de, old.objectives_en,
        old.prerequisites, old.literature,
        old.organisation, old.instructors
    );
    INSERT INTO courses_fts(
        rowid, course_number, title_de, title_en, content_de, content_en,
        objectives_de, objectives_en, prerequisites, literature,
        organisation, instructors
    ) VALUES (
        new.rowid, new.course_number, new.title_de, new.title_en,
        new.content_de, new.content_en,
        new.objectives_de, new.objectives_en,
        new.prerequisites, new.literature,
        new.organisation, new.instructors
    );
END;
"""

_CREATE_IDENTITY_INDEX = """\
CREATE INDEX IF NOT EXISTS idx_courses_identity ON courses (identity_code_id);
"""

_UPSERT = """\
INSERT INTO courses (
    course_id, semester_key, course_number,
    title_de, title_en, course_type, sws,
    organisation, instructors, language, campus,
    identity_code_id,
    content_de, content_en,
    objectives_de, objectives_en,
    prerequisites, literature
) VALUES (
    :course_id, :semester_key, :course_number,
    :title_de, :title_en, :course_type, :sws,
    :organisation, :instructors, :language, :campus,
    :identity_code_id,
    :content_de, :content_en,
    :objectives_de, :objectives_en,
    :prerequisites, :literature
)
ON CONFLICT(course_id) DO UPDATE SET
    semester_key     = excluded.semester_key,
    course_number    = excluded.course_number,
    title_de         = excluded.title_de,
    title_en         = excluded.title_en,
    course_type      = excluded.course_type,
    sws              = excluded.sws,
    organisation     = excluded.organisation,
    instructors      = excluded.instructors,
    language         = excluded.language,
    campus           = excluded.campus,
    identity_code_id = excluded.identity_code_id,
    content_de       = excluded.content_de,
    content_en       = excluded.content_en,
    objectives_de    = excluded.objectives_de,
    objectives_en    = excluded.objectives_en,
    prerequisites    = excluded.prerequisites,
    literature       = excluded.literature;
"""

_UPSERT_LIST_ONLY = """\
INSERT INTO courses (
    course_id, semester_key, course_number,
    title_de, title_en, course_type, sws,
    organisation, instructors, language, campus,
    identity_code_id,
    content_de, content_en,
    objectives_de, objectives_en,
    prerequisites, literature
) VALUES (
    :course_id, :semester_key, :course_number,
    :title_de, :title_en, :course_type, :sws,
    :organisation, :instructors, :language, :campus,
    :identity_code_id,
    :content_de, :content_en,
    :objectives_de, :objectives_en,
    :prerequisites, :literature
)
ON CONFLICT(course_id) DO UPDATE SET
    semester_key     = excluded.semester_key,
    course_number    = excluded.course_number,
    title_de         = excluded.title_de,
    title_en         = excluded.title_en,
    course_type      = excluded.course_type,
    sws              = excluded.sws,
    organisation     = excluded.organisation,
    instructors      = excluded.instructors,
    language         = excluded.language,
    identity_code_id = excluded.identity_code_id;
"""


def _dict_from_course(c: Course) -> dict[str, object]:
    """Convert a :class:`Course` to a dict suitable for SQL parameter binding."""
    from dataclasses import asdict

    return asdict(c)


def row_to_course(row: sqlite3.Row) -> Course:
    """Convert a ``sqlite3.Row`` to a :class:`Course`.

    Filters out non-model columns (e.g. ``score`` from FTS queries).

    Args:
        row: A sqlite3.Row with column-name access.

    Returns:
        A Course dataclass instance.

    """
    keys = row.keys()
    return Course(**{k: row[k] for k in keys if k not in {"score", "other_semesters"}})


def parse_other_semesters(row: sqlite3.Row) -> list[str]:
    """Extract the pre-computed other_semesters list from a database row.

    Args:
        row: A sqlite3.Row that may contain an ``other_semesters`` column.

    Returns:
        List of semester keys (e.g. ``["25W", "24W"]``), or empty list.

    """
    csv: str = row["other_semesters"] if "other_semesters" in row.keys() else ""  # noqa: SIM118
    return [s for s in csv.split(",") if s]


class CourseStore:
    """SQLite-backed course store with FTS5 full-text index.

    Args:
        db_path: Override the default database path (useful for testing).

    """

    def __init__(
        self,
        db_path: Path = DB_PATH,
        *,
        check_same_thread: bool = True,
    ) -> None:
        """Initialise the store, creating the schema if necessary.

        Args:
            db_path: Path to the SQLite database file.
            check_same_thread: If False, allow cross-thread access (for web servers).

        """
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(db_path, check_same_thread=check_same_thread)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL;")
        self._conn.execute("PRAGMA foreign_keys=ON;")
        self._embeddings_cache: tuple[NDArray[np.int64], NDArray[np.float32]] | None = None
        self._init_schema()

    # ── schema ─────────────────────────────────────────────────────────
    def _init_schema(self) -> None:
        # Check schema version; recreate tables if outdated
        self._conn.execute(_CREATE_META)
        row = self._conn.execute("SELECT value FROM meta WHERE key = 'schema_version'").fetchone()
        current = int(row[0]) if row else 0

        if current < _SCHEMA_VERSION:
            # Drop old schema and recreate — wrapped in a transaction so a
            # mid-migration crash leaves the DB in a consistent (empty) state
            # rather than a partially-migrated one.  Note: executescript() always
            # issues a COMMIT first, so individual execute() calls are used here
            # to preserve transaction semantics.
            try:
                self._conn.execute("BEGIN")
                for stmt in (
                    "DROP TRIGGER IF EXISTS courses_ai",
                    "DROP TRIGGER IF EXISTS courses_au",
                    "DROP TRIGGER IF EXISTS courses_ad",
                    "DROP TABLE IF EXISTS courses_fts",
                    "DROP TABLE IF EXISTS courses",
                ):
                    self._conn.execute(stmt)
                self._conn.execute(
                    "INSERT OR REPLACE INTO meta (key, value) VALUES ('schema_version', ?)",
                    (str(_SCHEMA_VERSION),),
                )
                self._conn.execute("COMMIT")
            except Exception:
                self._conn.execute("ROLLBACK")
                raise

        cur = self._conn.cursor()
        cur.executescript(_CREATE_COURSES)
        cur.executescript(_CREATE_BUILDING_CACHE)
        cur.executescript(_CREATE_FTS)
        # Always drop and recreate the UPDATE trigger so that column-scope
        # changes (e.g. adding UPDATE OF to skip other_semesters) take effect
        # on existing databases without a full schema version bump.
        self._conn.execute("DROP TRIGGER IF EXISTS courses_au")
        cur.executescript(_FTS_TRIGGERS)
        cur.executescript(_CREATE_IDENTITY_INDEX)
        self._conn.commit()

    # ── write ──────────────────────────────────────────────────────────
    def upsert_courses(self, courses: Iterable[Course], *, commit: bool = True) -> int:
        """Insert or update courses. Returns the number of rows affected.

        Args:
            courses: Course objects to upsert.
            commit: If True (default), commit the transaction. Pass False to
                let the caller commit manually (e.g. for batching with other writes).

        """
        rows = [_dict_from_course(c) for c in courses]
        if not rows:
            return 0
        cur = self._conn.cursor()
        cur.executemany(_UPSERT, rows)
        if commit:
            self._conn.commit()
        return cur.rowcount

    def upsert_course_list_fields(
        self,
        courses: Iterable[Course],
        *,
        commit: bool = True,
    ) -> int:
        """Upsert only list-level metadata, preserving existing descriptions and campus.

        Use this for incremental updates where detail API calls were skipped.
        Description columns (content, objectives, prerequisites, literature) and
        campus are NOT overwritten for existing rows.

        Args:
            courses: Course objects whose list-level fields should be upserted.
            commit: If True (default), commit the transaction.

        Returns:
            Number of rows affected.

        """
        rows = [_dict_from_course(c) for c in courses]
        if not rows:
            return 0
        cur = self._conn.cursor()
        cur.executemany(_UPSERT_LIST_ONLY, rows)
        if commit:
            self._conn.commit()
        return cur.rowcount

    def delete_semester(self, semester_key: str) -> int:
        """Remove all courses for a given semester.

        Args:
            semester_key: e.g. ``"25W"``.

        Returns:
            Number of rows deleted.

        """
        cur = self._conn.execute("DELETE FROM courses WHERE semester_key = ?", (semester_key,))
        self._conn.commit()
        return cur.rowcount

    # ── read ───────────────────────────────────────────────────────────
    def fulltext_search(
        self,
        query: str,
        *,
        course_type: str | None = None,
        limit: int = 50,
    ) -> list[tuple[sqlite3.Row, float]]:
        """Run an FTS5 query and return rows with BM25 scores.

        Args:
            query: The user's search string (FTS5 match expression).
            course_type: Optional course-type filter (e.g. ``"VO"``).
            limit: Maximum results.

        Returns:
            List of ``(row, score)`` tuples ordered by relevance.

        """
        clauses = ["courses_fts MATCH :q"]
        params: dict[str, object] = {"q": query, "limit": limit}
        if course_type:
            clauses.append("c.course_type = :ct")
            params["ct"] = course_type.upper()
        where = " AND ".join(clauses)
        weights_csv = ", ".join(str(w) for w in BM25_WEIGHTS)
        sql = f"""
            SELECT c.*, bm25(courses_fts, {weights_csv}) AS score
            FROM courses_fts
            JOIN courses c ON c.rowid = courses_fts.rowid
            WHERE {where}
            ORDER BY score
            LIMIT :limit
        """
        rows = self._conn.execute(sql, params).fetchall()
        return [(r, r["score"]) for r in rows]

    def get_course(self, course_id: int) -> sqlite3.Row | None:
        """Fetch a single course by its TUMonline id."""
        return self._conn.execute(
            "SELECT * FROM courses WHERE course_id = ?", (course_id,)
        ).fetchone()

    def get_all_courses(self) -> list[sqlite3.Row]:
        """Return all stored courses.

        Returns:
            List of all course rows.

        """
        return self._conn.execute("SELECT * FROM courses").fetchall()

    def course_count(self) -> int:
        """Return the number of stored courses.

        Returns:
            Course count.

        """
        row = self._conn.execute("SELECT COUNT(*) FROM courses").fetchone()
        return row[0]  # type: ignore[index]

    def semester_counts(self) -> list[tuple[str, int]]:
        """Return a list of ``(semester_key, count)`` pairs for all stored semesters.

        Returns:
            Pairs sorted by semester key.

        """
        rows = self._conn.execute(
            "SELECT semester_key, COUNT(*) AS cnt "
            "FROM courses GROUP BY semester_key ORDER BY semester_key"
        ).fetchall()
        return [(r["semester_key"], r["cnt"]) for r in rows]

    def get_other_semesters(
        self,
        identity_code_id: int,
        exclude_course_id: int,
    ) -> list[tuple[int, str]]:
        """Find other semester offerings of a course.

        Args:
            identity_code_id: The identity linking the same course across semesters.
            exclude_course_id: Course id to exclude (the one being viewed).

        Returns:
            List of ``(course_id, semester_key)`` tuples, most recent first.

        """
        rows = self._conn.execute(
            "SELECT course_id, semester_key FROM courses "
            "WHERE identity_code_id = ? AND course_id != ? "
            "ORDER BY semester_key DESC",
            (identity_code_id, exclude_course_id),
        ).fetchall()
        return [(r[0], r[1]) for r in rows]

    def compute_other_semesters(self) -> None:
        """Populate the ``other_semesters`` column for every course.

        For each course, stores a comma-separated list of semester keys from
        other offerings that share the same ``identity_code_id``, sorted
        descending (most recent first).  Runs in a single UPDATE statement.

        """
        self._conn.execute(
            "UPDATE courses SET other_semesters = COALESCE("
            "  (SELECT GROUP_CONCAT(sk, ',') FROM ("
            "    SELECT DISTINCT c2.semester_key AS sk"
            "    FROM courses c2"
            "    WHERE c2.identity_code_id = courses.identity_code_id"
            "      AND c2.course_id != courses.course_id"
            "      AND c2.identity_code_id != 0"
            "    ORDER BY sk DESC"
            "  )), '')"
        )
        self._conn.commit()

    def type_counts(self) -> list[tuple[str, int]]:
        """Return course type distribution.

        Returns:
            List of ``(type_key, count)`` pairs sorted by count descending.

        """
        rows = self._conn.execute(
            "SELECT course_type, COUNT(*) AS cnt "
            "FROM courses WHERE course_type != '' "
            "GROUP BY course_type ORDER BY cnt DESC",
        ).fetchall()
        return [(r["course_type"], r["cnt"]) for r in rows]

    def campus_counts(self) -> list[tuple[str, int]]:
        """Return campus distribution.

        Returns:
            List of ``(campus, count)`` pairs sorted by count descending.

        """
        rows = self._conn.execute(
            "SELECT campus, COUNT(*) AS cnt "
            "FROM courses WHERE campus != '' "
            "GROUP BY campus ORDER BY cnt DESC",
        ).fetchall()
        return [(r["campus"], r["cnt"]) for r in rows]

    def commit(self) -> None:
        """Commit the current transaction."""
        self._conn.commit()

    def close(self) -> None:
        """Close the database connection."""
        self._conn.close()

    # ── incremental update helpers ─────────────────────────────────────
    def get_course_ids_with_details(
        self,
        semester_keys: list[str] | None = None,
    ) -> set[int]:
        """Return course IDs that already have at least one non-empty description.

        Use this to build a skip-set for incremental updates: courses in the
        returned set do not need their details re-fetched from the API.

        Args:
            semester_keys: If given, only consider courses in these semesters.
                When ``None``, all semesters are included.

        Returns:
            Set of course IDs with populated descriptions.

        """
        sql = (
            "SELECT course_id FROM courses "
            "WHERE (content_de != '' OR content_en != '' "
            "       OR objectives_de != '' OR objectives_en != '')"
        )
        params: list[str] = []
        if semester_keys:
            placeholders = ",".join("?" for _ in semester_keys)
            sql += f" AND semester_key IN ({placeholders})"
            params = list(semester_keys)
        rows = self._conn.execute(sql, params).fetchall()
        return {r[0] for r in rows}

    # ── meta key-value store ───────────────────────────────────────────
    def set_meta(self, key: str, value: str) -> None:
        """Store a key-value pair in the meta table.

        Args:
            key: The meta key.
            value: The meta value.

        """
        self._conn.execute(
            "INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)",
            (key, value),
        )
        self._conn.commit()

    def get_meta(self, key: str, *, default: str | None = None) -> str | None:
        """Retrieve a value from the meta table.

        Args:
            key: The meta key.
            default: Value to return if the key does not exist.

        Returns:
            The stored value, or *default* if the key is not found.

        """
        row = self._conn.execute(
            "SELECT value FROM meta WHERE key = ?", (key,)
        ).fetchone()
        return row[0] if row else default

    # ── building cache ─────────────────────────────────────────────────
    def get_building_cache(self) -> dict[str, str]:
        """Return the cached building-code → campus mapping.

        Returns:
            Dict mapping 4-digit building codes to campus labels.

        """
        rows = self._conn.execute("SELECT building_code, campus FROM building_campuses").fetchall()
        return {r["building_code"]: r["campus"] for r in rows}

    def upsert_building_cache(self, mapping: dict[str, str]) -> None:
        """Insert or update building-code → campus entries.

        Args:
            mapping: Dict of building_code → campus label.

        """
        if not mapping:
            return
        self._conn.executemany(
            "INSERT INTO building_campuses (building_code, campus) VALUES (?, ?) "
            "ON CONFLICT(building_code) DO UPDATE SET campus = excluded.campus",
            list(mapping.items()),
        )
        self._conn.commit()

    # ── embeddings cache ───────────────────────────────────────────────
    def save_embeddings(
        self,
        course_ids: NDArray[np.int64],
        embeddings: NDArray[np.float32],
    ) -> None:
        """Persist pre-computed embeddings to disk.

        Writes to a temporary file first, then atomically renames to the
        final path.  This avoids a concurrent reader seeing a half-written
        file during long-running scheduled updates.

        Args:
            course_ids: 1-D array of course ids.
            embeddings: 2-D array of shape ``(n, dim)``.

        """
        import numpy as np

        tmp = EMBEDDINGS_PATH.with_name(EMBEDDINGS_PATH.stem + "_tmp.npz")
        np.savez_compressed(tmp, ids=course_ids, emb=embeddings)
        tmp.replace(EMBEDDINGS_PATH)
        self.invalidate_embeddings_cache()

    def load_embeddings(self) -> tuple[NDArray[np.int64], NDArray[np.float32]] | None:
        """Load cached embeddings from disk (memoized after first load).

        Returns:
            ``(course_ids, embeddings)`` or ``None`` if not cached.

        """
        if self._embeddings_cache is not None:
            return self._embeddings_cache
        if not EMBEDDINGS_PATH.exists():
            return None
        import numpy as np

        data = np.load(EMBEDDINGS_PATH)
        self._embeddings_cache = (data["ids"], data["emb"])
        return self._embeddings_cache

    def invalidate_embeddings_cache(self) -> None:
        """Clear the in-memory embeddings cache (call after re-encoding)."""
        self._embeddings_cache = None
