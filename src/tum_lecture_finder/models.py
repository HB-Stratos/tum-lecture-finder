"""Domain data-classes for TUM courses."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Course:
    """A single TUM course with all searchable fields.

    Attributes:
        course_id: TUMonline internal numeric id (primary key).
        semester_key: E.g. ``"25W"`` for winter 2025/26.
        course_number: Official course code, e.g. ``"IN2346"``.
        title_de: German title.
        title_en: English title.
        course_type: Short key like ``"VO"``, ``"SE"``, ``"PR"``.
        sws: Semester weekly hours (Semesterwochenstunden).
        organisation: Responsible chair / department name.
        instructors: Comma-separated instructor names.
        language: Instruction language keys, e.g. ``"DE,EN"``.
        campus: Detected campus from room building codes (e.g. ``"garching"``).
        identity_code_id: TUMonline identity linking the same course across semesters.
        content_de: Course content / description (German).
        content_en: Course content / description (English).
        objectives_de: Learning objectives (German).
        objectives_en: Learning objectives (English).
        prerequisites: Prerequisites / prior knowledge.
        literature: Recommended literature.

    """

    course_id: int
    semester_key: str
    course_number: str = ""
    title_de: str = ""
    title_en: str = ""
    course_type: str = ""
    sws: str = ""
    organisation: str = ""
    instructors: str = ""
    language: str = ""
    campus: str = ""
    identity_code_id: int = 0
    content_de: str = ""
    content_en: str = ""
    objectives_de: str = ""
    objectives_en: str = ""
    prerequisites: str = ""
    literature: str = ""

    # ── derived helpers ─────────────────────────────────────────────────
    @property
    def title(self) -> str:
        """Return the best available title (English preferred)."""
        return self.title_en or self.title_de

    @property
    def searchable_text(self) -> str:
        """Concatenate all human-readable fields for full-text indexing."""
        parts = [
            self.course_number,
            self.title_de,
            self.title_en,
            self.content_de,
            self.content_en,
            self.objectives_de,
            self.objectives_en,
            self.prerequisites,
            self.literature,
            self.organisation,
            self.instructors,
        ]
        return " ".join(p for p in parts if p)

    @property
    def embedding_text(self) -> str:
        """Focused text for semantic embedding (titles + descriptions).

        Includes both German and English when available so the embedding
        captures bilingual queries.
        """
        parts = [
            self.title_en,
            self.title_de,
            self.content_en,
            self.content_de,
            self.objectives_en,
            self.objectives_de,
        ]
        return " ".join(p for p in parts if p)


@dataclass
class SearchResult:
    """A course together with its search relevance score.

    Attributes:
        course: The matched course.
        score: Relevance score (higher = better).
        snippet: Optional highlighted excerpt.

    """

    course: Course
    score: float = 0.0
    snippet: str = ""
    highlights: list[str] = field(default_factory=list)
    other_semesters: list[str] = field(default_factory=list)
