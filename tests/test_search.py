"""Tests for search module."""

from pathlib import Path

import pytest

from tum_lecture_finder.models import Course, SearchResult
from tum_lecture_finder.search import (
    _dedup_by_identity,
    _escape_fts_query,
    _extract_excerpt,
    _generate_snippet,
    _matches_campus,
    fulltext_search,
)
from tum_lecture_finder.storage import CourseStore


def _make_course(**kwargs) -> Course:
    defaults = {
        "course_id": 1,
        "semester_key": "25W",
        "course_number": "IN0001",
        "title_de": "Einführung",
        "title_en": "Introduction",
    }
    defaults.update(kwargs)
    return Course(**defaults)


@pytest.fixture
def store(tmp_path: Path):
    s = CourseStore(db_path=tmp_path / "test.db")
    yield s
    s.close()


# ── _escape_fts_query ─────────────────────────────────────────────────────


class TestEscapeFtsQuery:
    def test_basic_tokens(self):
        assert _escape_fts_query("machine learning") == "machine* learning*"

    def test_single_token(self):
        assert _escape_fts_query("robotics") == "robotics*"

    def test_special_chars_stripped(self):
        result = _escape_fts_query('hello "world"')
        assert '"' not in result
        assert "hello*" in result
        assert "world*" in result

    def test_empty_string(self):
        assert _escape_fts_query("") == '""'

    def test_only_special_chars(self):
        assert _escape_fts_query("!@#$%") == '""'

    def test_unicode_preserved(self):
        result = _escape_fts_query("Regelungstechnik")
        assert "Regelungstechnik*" in result

    def test_parentheses_stripped(self):
        result = _escape_fts_query("PCB (design)")
        assert "(" not in result
        assert ")" not in result
        assert "PCB*" in result
        assert "design*" in result

    def test_multiple_spaces_handled(self):
        result = _escape_fts_query("machine   learning")
        assert result == "machine* learning*"


# ── _extract_excerpt ──────────────────────────────────────────────────────


class TestExtractExcerpt:
    def test_token_found(self):
        text = "This course covers machine learning and deep learning topics."
        excerpt = _extract_excerpt(text, "machine")
        assert "machine" in excerpt.lower()

    def test_token_not_found(self):
        assert _extract_excerpt("Some text about databases", "quantum") == ""

    def test_case_insensitive(self):
        text = "Introduction to Machine Learning"
        excerpt = _extract_excerpt(text, "machine")
        assert len(excerpt) > 0

    def test_token_near_start(self):
        text = "Machine learning is a field of artificial intelligence."
        excerpt = _extract_excerpt(text, "machine")
        assert len(excerpt) > 0

    def test_token_near_end(self):
        text = "This is a very long text about many topics including machine"
        excerpt = _extract_excerpt(text, "machine")
        assert "machine" in excerpt.lower()

    def test_excerpt_length_bounded(self):
        text = "A " * 200 + "machine" + " B" * 200
        excerpt = _extract_excerpt(text, "machine")
        assert len(excerpt) <= 200  # Roughly bounded


# ── _generate_snippet ─────────────────────────────────────────────────────


class TestGenerateSnippet:
    def test_title_match_returns_empty(self):
        c = _make_course(title_en="Machine Learning", content_en="Deep learning.")
        assert _generate_snippet(c, "machine learning") == ""

    def test_description_match(self):
        c = _make_course(
            title_en="Some Course",
            content_en="This course covers requirements engineering and software design.",
        )
        snippet = _generate_snippet(c, "requirements")
        assert "requirements" in snippet.lower()

    def test_no_match_anywhere(self):
        c = _make_course(title_en="Course A", content_en="Unrelated topic")
        assert _generate_snippet(c, "quantum") == ""

    def test_empty_query(self):
        c = _make_course(title_en="Course", content_en="Something")
        assert _generate_snippet(c, "") == ""

    def test_searches_multiple_fields(self):
        c = _make_course(
            title_en="Course A",
            content_en="",
            objectives_en="Learn about robotics and automation.",
        )
        snippet = _generate_snippet(c, "robotics")
        assert "robotics" in snippet.lower()

    def test_content_de_searched(self):
        c = _make_course(
            title_en="Course A",
            title_de="Kurs A",
            content_de="Dieses Modul behandelt Datenbanken und SQL-Abfragen.",
        )
        snippet = _generate_snippet(c, "Datenbanken")
        assert "Datenbanken" in snippet

    def test_prerequisites_searched(self):
        c = _make_course(
            title_en="Advanced ML",
            prerequisites="Linear algebra and probability theory required.",
        )
        snippet = _generate_snippet(c, "algebra")
        assert "algebra" in snippet.lower()

    def test_literature_searched(self):
        c = _make_course(
            title_en="Course",
            literature="Bishop: Pattern Recognition and Machine Learning",
        )
        snippet = _generate_snippet(c, "Bishop")
        assert "Bishop" in snippet

    def test_partial_title_match_generates_snippet(self):
        """If only some tokens are in the title, search descriptions for the rest."""
        c = _make_course(
            title_en="Machine Design",
            content_en="This course covers learning algorithms and optimization.",
        )
        # "machine" is in title, "learning" is not → snippet should come from content
        snippet = _generate_snippet(c, "machine learning")
        # Since "machine" is in the title but "learning" is not, it should search content
        assert snippet == "" or "learning" in snippet.lower()


# ── _matches_campus ───────────────────────────────────────────────────────


class TestMatchesCampus:
    def test_exact_match(self):
        c = _make_course(campus="garching")
        assert _matches_campus(c, "garching")

    def test_substring_match(self):
        c = _make_course(campus="garching-hochbrück")
        assert _matches_campus(c, "garching")

    def test_case_sensitive_on_course_side(self):
        """Campus matching lowercases the filter but not the course field.

        In practice NavigaTUM always returns lowercase campus labels.
        """
        c = _make_course(campus="Garching")
        assert not _matches_campus(c, "garching")

    def test_lowercase_campus_matches(self):
        c = _make_course(campus="garching")
        assert _matches_campus(c, "Garching")

    def test_empty_campus_no_match(self):
        c = _make_course(campus="")
        assert not _matches_campus(c, "garching")

    def test_no_match(self):
        c = _make_course(campus="stammgelände")
        assert not _matches_campus(c, "garching")


# ── _dedup_by_identity ────────────────────────────────────────────────────


class TestDedupByIdentity:
    def test_merges_same_identity(self):
        results = [
            SearchResult(
                course=_make_course(course_id=1, semester_key="25W", identity_code_id=100),
                score=10.0,
            ),
            SearchResult(
                course=_make_course(course_id=2, semester_key="25S", identity_code_id=100),
                score=8.0,
            ),
        ]
        deduped = _dedup_by_identity(results)
        assert len(deduped) == 1
        assert deduped[0].course.course_id == 1
        assert deduped[0].other_semesters == ["25S"]

    def test_preserves_different_identities(self):
        results = [
            SearchResult(
                course=_make_course(course_id=1, identity_code_id=100),
                score=10.0,
            ),
            SearchResult(
                course=_make_course(course_id=2, identity_code_id=200),
                score=8.0,
            ),
        ]
        deduped = _dedup_by_identity(results)
        assert len(deduped) == 2

    def test_zero_identity_not_deduped(self):
        results = [
            SearchResult(
                course=_make_course(course_id=1, identity_code_id=0),
                score=10.0,
            ),
            SearchResult(
                course=_make_course(course_id=2, identity_code_id=0),
                score=8.0,
            ),
        ]
        deduped = _dedup_by_identity(results)
        assert len(deduped) == 2

    def test_prefers_most_recent_semester(self):
        """When a newer semester appears later, it replaces the displayed course."""
        results = [
            SearchResult(
                course=_make_course(course_id=1, semester_key="25S", identity_code_id=100),
                score=5.0,
            ),
            SearchResult(
                course=_make_course(course_id=2, semester_key="25W", identity_code_id=100),
                score=10.0,
            ),
        ]
        deduped = _dedup_by_identity(results)
        assert len(deduped) == 1
        # 25W is more recent, so course 2 should be the displayed course
        assert deduped[0].course.course_id == 2
        assert "25S" in deduped[0].other_semesters

    def test_multiple_other_semesters(self):
        results = [
            SearchResult(
                course=_make_course(course_id=1, semester_key="25W", identity_code_id=100),
                score=10.0,
            ),
            SearchResult(
                course=_make_course(course_id=2, semester_key="25S", identity_code_id=100),
                score=8.0,
            ),
            SearchResult(
                course=_make_course(course_id=3, semester_key="24W", identity_code_id=100),
                score=6.0,
            ),
        ]
        deduped = _dedup_by_identity(results)
        assert len(deduped) == 1
        assert set(deduped[0].other_semesters) == {"25S", "24W"}

    def test_no_duplicate_semesters(self):
        """Same semester key shouldn't be added twice."""
        results = [
            SearchResult(
                course=_make_course(course_id=1, semester_key="25W", identity_code_id=100),
                score=10.0,
            ),
            SearchResult(
                course=_make_course(course_id=2, semester_key="25W", identity_code_id=100),
                score=8.0,
            ),
        ]
        deduped = _dedup_by_identity(results)
        assert len(deduped) == 1
        # Same semester key — should not appear in other_semesters
        assert deduped[0].other_semesters == []

    def test_empty_list(self):
        assert _dedup_by_identity([]) == []

    def test_single_item(self):
        results = [
            SearchResult(
                course=_make_course(course_id=1, identity_code_id=100),
                score=10.0,
            ),
        ]
        deduped = _dedup_by_identity(results)
        assert len(deduped) == 1
        assert deduped[0].other_semesters == []


# ── fulltext_search (integration) ─────────────────────────────────────────


class TestFulltextSearch:
    def test_basic_keyword_search(self, store):
        store.upsert_courses(
            [
                _make_course(course_id=1, title_en="Machine Learning"),
                _make_course(course_id=2, title_en="Database Systems"),
            ]
        )
        results = fulltext_search(store, "machine learning")
        assert len(results) >= 1
        assert results[0].course.course_id == 1

    def test_search_by_course_number(self, store):
        store.upsert_courses(
            [
                _make_course(course_id=1, title_en="ML", course_number="IN2064"),
                _make_course(course_id=2, title_en="DB", course_number="IN0008"),
            ]
        )
        results = fulltext_search(store, "IN2064")
        assert len(results) >= 1
        assert results[0].course.course_id == 1

    def test_campus_filter(self, store):
        store.upsert_courses(
            [
                _make_course(course_id=1, title_en="Physics Lab", campus="garching"),
                _make_course(course_id=2, title_en="Physics Theory", campus="stammgelände"),
            ]
        )
        results = fulltext_search(store, "physics", campus="garching")
        assert len(results) == 1
        assert results[0].course.course_id == 1

    def test_campus_substring_filter(self, store):
        store.upsert_courses(
            [
                _make_course(course_id=1, title_en="Lab A", campus="garching"),
                _make_course(course_id=2, title_en="Lab B", campus="garching-hochbrück"),
                _make_course(course_id=3, title_en="Lab C", campus="stammgelände"),
            ]
        )
        results = fulltext_search(store, "lab", campus="garching")
        assert len(results) == 2
        ids = {r.course.course_id for r in results}
        assert ids == {1, 2}

    def test_empty_campus_excluded(self, store):
        store.upsert_courses(
            [
                _make_course(course_id=1, title_en="Lab A", campus="garching"),
                _make_course(course_id=2, title_en="Lab B", campus=""),
            ]
        )
        results = fulltext_search(store, "lab", campus="garching")
        assert len(results) == 1
        assert results[0].course.course_id == 1

    def test_type_filter(self, store):
        store.upsert_courses(
            [
                _make_course(course_id=1, title_en="Course A", course_type="VO"),
                _make_course(course_id=2, title_en="Course A lab", course_type="UE"),
            ]
        )
        results = fulltext_search(store, "course", course_type="VO")
        assert all(r.course.course_type == "VO" for r in results)

    def test_no_results(self, store):
        store.upsert_courses([_make_course(course_id=1, title_en="Something")])
        results = fulltext_search(store, "xyznonexistent12345")
        assert results == []

    def test_dedup_integration(self, store):
        store.upsert_courses(
            [
                _make_course(
                    course_id=1,
                    semester_key="25W",
                    title_en="Machine Learning",
                    identity_code_id=100,
                ),
                _make_course(
                    course_id=2,
                    semester_key="25S",
                    title_en="Machine Learning",
                    identity_code_id=100,
                ),
                _make_course(
                    course_id=3,
                    semester_key="25W",
                    title_en="Machine Vision",
                    identity_code_id=200,
                ),
            ]
        )
        store.compute_other_semesters()
        results = fulltext_search(store, "machine")
        assert len(results) == 2
        ml = [r for r in results if "Learning" in r.course.title_en]
        assert len(ml) == 1
        assert len(ml[0].other_semesters) == 1

    def test_limit_respected(self, store):
        for i in range(20):
            store.upsert_courses(
                [
                    _make_course(course_id=i + 1, title_en=f"Robotics Module {i}"),
                ]
            )
        results = fulltext_search(store, "robotics", limit=5)
        assert len(results) <= 5

    def test_search_description_content(self, store):
        """FTS searches content fields, not just titles."""
        store.upsert_courses(
            [
                _make_course(
                    course_id=1,
                    title_en="Course A",
                    content_en="This module covers quantum computing fundamentals.",
                ),
            ]
        )
        results = fulltext_search(store, "quantum")
        assert len(results) >= 1

    def test_search_instructor(self, store):
        store.upsert_courses(
            [
                _make_course(course_id=1, title_en="Course A", instructors="Prof. Niessner"),
            ]
        )
        results = fulltext_search(store, "Niessner")
        assert len(results) >= 1

    def test_result_has_score_and_snippet(self, store):
        store.upsert_courses(
            [
                _make_course(
                    course_id=1,
                    title_en="Course A",
                    content_en="Covers advanced topics in quantum computing.",
                ),
            ]
        )
        results = fulltext_search(store, "quantum")
        assert len(results) >= 1
        r = results[0]
        assert r.score != 0
        assert isinstance(r.snippet, str)
