"""Tests for search module."""

from pathlib import Path

from tum_lecture_finder.models import Course, SearchResult
from tum_lecture_finder.search import (
    _dedup_by_identity,
    _escape_fts_query,
    _generate_snippet,
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


def test_escape_fts_query_basic():
    assert _escape_fts_query("machine learning") == "machine* learning*"


def test_escape_fts_query_special_chars():
    assert _escape_fts_query('hello "world"') == "hello* world*"


def test_escape_fts_query_empty():
    assert _escape_fts_query("") == '""'


def test_generate_snippet_title_match():
    c = _make_course(title_en="Machine Learning", content_en="This is about ML.")
    assert _generate_snippet(c, "machine learning") == ""


def test_generate_snippet_description_match():
    c = _make_course(
        title_en="Some Course",
        content_en="This course covers requirements engineering and software design.",
    )
    snippet = _generate_snippet(c, "requirements")
    assert "requirements" in snippet.lower()


def test_generate_snippet_no_match():
    c = _make_course(title_en="Some Course", content_en="Unrelated topic.")
    assert _generate_snippet(c, "quantum") == ""


def test_fulltext_search_integration(tmp_path: Path):
    store = CourseStore(db_path=tmp_path / "test.db")
    store.upsert_courses(
        [
            _make_course(course_id=1, title_en="Machine Learning", course_number="IN2064"),
            _make_course(course_id=2, title_en="Database Systems", course_number="IN0008"),
        ]
    )
    results = fulltext_search(store, "machine learning")
    assert len(results) >= 1
    assert results[0].course.course_id == 1
    store.close()


def test_fulltext_search_by_course_number(tmp_path: Path):
    store = CourseStore(db_path=tmp_path / "test.db")
    store.upsert_courses(
        [
            _make_course(course_id=1, title_en="Machine Learning", course_number="IN2064"),
            _make_course(course_id=2, title_en="Database Systems", course_number="IN0008"),
        ]
    )
    results = fulltext_search(store, "IN2064")
    assert len(results) >= 1
    assert results[0].course.course_id == 1
    store.close()


def test_fulltext_search_campus_filter(tmp_path: Path):
    store = CourseStore(db_path=tmp_path / "test.db")
    store.upsert_courses(
        [
            _make_course(
                course_id=1,
                title_en="Physics Lab",
                organisation="Physik Department (Garching)",
            ),
            _make_course(
                course_id=2,
                title_en="Physics Theory",
                organisation="Stammgelände München",
            ),
        ]
    )
    results = fulltext_search(store, "physics", campus="garching")
    assert len(results) == 1
    assert results[0].course.course_id == 1
    store.close()


def test_fulltext_search_campus_filter_from_building_code(tmp_path: Path):
    store = CourseStore(db_path=tmp_path / "test.db")
    store.upsert_courses(
        [
            _make_course(
                course_id=1,
                title_en="Physics Lab",
                campus="garching",
            ),
            _make_course(
                course_id=2,
                title_en="Physics Theory",
                campus="münchen",
            ),
        ]
    )
    results = fulltext_search(store, "physics", campus="garching")
    assert len(results) == 1
    assert results[0].course.course_id == 1
    store.close()


def test_dedup_by_identity_merges_semesters():
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
            course=_make_course(course_id=3, semester_key="25W", identity_code_id=200),
            score=5.0,
        ),
    ]
    deduped = _dedup_by_identity(results)
    assert len(deduped) == 2
    assert deduped[0].course.course_id == 1
    assert deduped[0].other_semesters == ["25S"]
    assert deduped[1].course.course_id == 3
    assert deduped[1].other_semesters == []


def test_dedup_by_identity_no_identity():
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


def test_dedup_preserves_best_score():
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
    # First one wins (results are assumed pre-sorted by score)
    assert deduped[0].course.course_id == 1


def test_fulltext_search_dedup(tmp_path: Path):
    store = CourseStore(db_path=tmp_path / "test.db")
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
    results = fulltext_search(store, "machine")
    # Only 2 unique identity groups
    assert len(results) == 2
    # The deduped result should have other_semesters populated
    ml_results = [r for r in results if "Learning" in r.course.title_en]
    assert len(ml_results) == 1
    assert len(ml_results[0].other_semesters) == 1
    store.close()
