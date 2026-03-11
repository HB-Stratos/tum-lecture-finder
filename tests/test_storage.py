"""Tests for storage module."""

from pathlib import Path

from tum_lecture_finder.models import Course
from tum_lecture_finder.storage import CourseStore


def _make_course(**kwargs) -> Course:
    defaults = {
        "course_id": 1,
        "semester_key": "25W",
        "course_number": "IN0001",
        "title_de": "Einführung in die Informatik",
        "title_en": "Introduction to Computer Science",
    }
    defaults.update(kwargs)
    return Course(**defaults)


def test_upsert_and_count(tmp_path: Path):
    store = CourseStore(db_path=tmp_path / "test.db")
    c = _make_course()
    store.upsert_courses([c])
    assert store.course_count() == 1
    store.close()


def test_upsert_idempotent(tmp_path: Path):
    store = CourseStore(db_path=tmp_path / "test.db")
    c = _make_course()
    store.upsert_courses([c])
    store.upsert_courses([c])
    assert store.course_count() == 1
    store.close()


def test_get_course(tmp_path: Path):
    store = CourseStore(db_path=tmp_path / "test.db")
    c = _make_course(course_id=42, title_en="Test Course")
    store.upsert_courses([c])
    row = store.get_course(42)
    assert row is not None
    assert row["title_en"] == "Test Course"
    store.close()


def test_fulltext_search(tmp_path: Path):
    store = CourseStore(db_path=tmp_path / "test.db")
    store.upsert_courses(
        [
            _make_course(course_id=1, title_en="Machine Learning Basics"),
            _make_course(course_id=2, title_en="Database Systems"),
            _make_course(course_id=3, title_en="Advanced Machine Learning"),
        ]
    )
    results = store.fulltext_search("machine*")
    assert len(results) == 2
    store.close()


def test_delete_semester(tmp_path: Path):
    store = CourseStore(db_path=tmp_path / "test.db")
    store.upsert_courses([_make_course(course_id=1, semester_key="25W")])
    store.upsert_courses([_make_course(course_id=2, semester_key="25S")])
    assert store.course_count() == 2
    store.delete_semester("25W")
    assert store.course_count() == 1
    store.close()


def test_semester_counts(tmp_path: Path):
    store = CourseStore(db_path=tmp_path / "test.db")
    store.upsert_courses(
        [
            _make_course(course_id=1, semester_key="25W"),
            _make_course(course_id=2, semester_key="25W"),
            _make_course(course_id=3, semester_key="25S"),
        ]
    )
    counts = store.semester_counts()
    assert ("25S", 1) in counts
    assert ("25W", 2) in counts
    store.close()
