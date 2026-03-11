"""SQLite + FTS5 persistence layer for course data."""

from __future__ import annotations

import sqlite3
from typing import TYPE_CHECKING

from tum_lecture_finder.config import BM25_WEIGHTS, DATA_DIR, DB_PATH

if TYPE_CHECKING:
    from collections.abc import Iterable
    from pathlib import Path

    import numpy as np
    from numpy.typing import NDArray

    from tum_lecture_finder.models import Course

# Embeddings are stored alongside the database as a compact numpy file.
EMBEDDINGS_PATH = DATA_DIR / "embeddings.npz"

# ── SQL statements ─────────────────────────────────────────────────────────
_SCHEMA_VERSION = 5  # bump when schema changes

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
    literature     TEXT    NOT NULL DEFAULT ''
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

CREATE TRIGGER IF NOT EXISTS courses_au AFTER UPDATE ON courses BEGIN
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


def _dict_from_course(c: Course) -> dict[str, object]:
    """Convert a :class:`Course` to a dict suitable for SQL parameter binding."""
    from dataclasses import asdict  # noqa: PLC0415

    return asdict(c)


class CourseStore:
    """SQLite-backed course store with FTS5 full-text index.

    Args:
        db_path: Override the default database path (useful for testing).

    """

    def __init__(self, db_path: Path = DB_PATH) -> None:
        """Initialise the store, creating the schema if necessary."""
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(db_path)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL;")
        self._conn.execute("PRAGMA foreign_keys=ON;")
        self._init_schema()

    # ── schema ─────────────────────────────────────────────────────────
    def _init_schema(self) -> None:
        # Check schema version; recreate tables if outdated
        self._conn.execute(_CREATE_META)
        row = self._conn.execute("SELECT value FROM meta WHERE key = 'schema_version'").fetchone()
        current = int(row[0]) if row else 0

        if current < _SCHEMA_VERSION:
            # Drop old schema and recreate
            self._conn.executescript(
                "DROP TRIGGER IF EXISTS courses_ai;"
                "DROP TRIGGER IF EXISTS courses_au;"
                "DROP TRIGGER IF EXISTS courses_ad;"
                "DROP TABLE IF EXISTS courses_fts;"
                "DROP TABLE IF EXISTS courses;"
            )
            self._conn.execute(
                "INSERT OR REPLACE INTO meta (key, value) VALUES ('schema_version', ?)",
                (str(_SCHEMA_VERSION),),
            )
            self._conn.commit()

        cur = self._conn.cursor()
        cur.executescript(_CREATE_COURSES)
        cur.executescript(_CREATE_FTS)
        cur.executescript(_FTS_TRIGGERS)
        self._conn.commit()

    # ── write ──────────────────────────────────────────────────────────
    def upsert_courses(self, courses: Iterable[Course]) -> int:
        """Insert or update courses. Returns the number of rows affected."""
        rows = [_dict_from_course(c) for c in courses]
        if not rows:
            return 0
        cur = self._conn.cursor()
        cur.executemany(_UPSERT, rows)
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

    def close(self) -> None:
        """Close the database connection."""
        self._conn.close()

    # ── embeddings cache ───────────────────────────────────────────────
    def save_embeddings(
        self,
        course_ids: NDArray[np.int64],
        embeddings: NDArray[np.float32],
    ) -> None:
        """Persist pre-computed embeddings to disk.

        Args:
            course_ids: 1-D array of course ids.
            embeddings: 2-D array of shape ``(n, dim)``.

        """
        import numpy as np  # noqa: PLC0415

        np.savez_compressed(EMBEDDINGS_PATH, ids=course_ids, emb=embeddings)

    @staticmethod
    def load_embeddings() -> tuple[NDArray[np.int64], NDArray[np.float32]] | None:
        """Load cached embeddings from disk.

        Returns:
            ``(course_ids, embeddings)`` or ``None`` if not cached.

        """
        if not EMBEDDINGS_PATH.exists():
            return None
        import numpy as np  # noqa: PLC0415

        data = np.load(EMBEDDINGS_PATH)
        return data["ids"], data["emb"]
