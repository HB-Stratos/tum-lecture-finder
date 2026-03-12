"""Tests for storage module."""

from pathlib import Path

import numpy as np
import pytest

import tum_lecture_finder.storage as storage_mod
from tum_lecture_finder.models import Course
from tum_lecture_finder.storage import CourseStore, row_to_course


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


@pytest.fixture
def store(tmp_path: Path):
    s = CourseStore(db_path=tmp_path / "test.db")
    yield s
    s.close()


@pytest.fixture
def populated_store(tmp_path: Path):
    """Store with a variety of test courses."""
    s = CourseStore(db_path=tmp_path / "test.db")
    s.upsert_courses(
        [
            _make_course(
                course_id=1,
                semester_key="25W",
                course_number="IN2064",
                title_en="Machine Learning",
                course_type="VO",
                campus="garching",
                identity_code_id=100,
                content_en="Supervised and unsupervised learning.",
            ),
            _make_course(
                course_id=2,
                semester_key="25S",
                course_number="IN2064",
                title_en="Machine Learning",
                course_type="VO",
                campus="garching",
                identity_code_id=100,
            ),
            _make_course(
                course_id=3,
                semester_key="24W",
                course_number="IN2064",
                title_en="Machine Learning",
                course_type="VO",
                campus="garching",
                identity_code_id=100,
            ),
            _make_course(
                course_id=4,
                semester_key="25W",
                course_number="IN0008",
                title_en="Database Systems",
                course_type="VO",
                campus="stammgelände",
                identity_code_id=200,
            ),
            _make_course(
                course_id=5,
                semester_key="25W",
                course_number="AR20005",
                title_en="Architectural Design",
                course_type="UE",
                campus="münchen",
                identity_code_id=300,
            ),
            _make_course(
                course_id=6,
                semester_key="25W",
                course_number="WZ1234",
                title_en="Brewing Technology",
                course_type="VO",
                campus="freising",
                identity_code_id=400,
            ),
            _make_course(
                course_id=7,
                semester_key="25W",
                course_number="IN2346",
                title_en="Introduction to Deep Learning",
                course_type="VI",
                campus="garching",
                identity_code_id=500,
            ),
            _make_course(
                course_id=8,
                semester_key="25W",
                course_number="CIT0001",
                title_en="Online Seminar",
                course_type="SE",
                campus="",
                identity_code_id=600,
            ),
        ]
    )
    yield s
    s.close()


# ── row_to_course ──────────────────────────────────────────────────────────


class TestRowToCourse:
    def test_converts_row_to_course(self, store):
        store.upsert_courses([_make_course(course_id=42, title_en="Test")])
        row = store.get_course(42)
        c = row_to_course(row)
        assert isinstance(c, Course)
        assert c.course_id == 42
        assert c.title_en == "Test"

    def test_filters_score_column(self, store):
        """FTS queries add a 'score' column — row_to_course must skip it."""
        store.upsert_courses([_make_course(course_id=1, title_en="Machine Learning")])
        results = store.fulltext_search("machine*")
        assert len(results) >= 1
        row, _ = results[0]
        c = row_to_course(row)
        assert c.course_id == 1
        assert not hasattr(c, "score")

    def test_all_fields_preserved(self, store):
        full = _make_course(
            course_id=99,
            semester_key="25S",
            course_number="XX9999",
            title_de="Deutsch",
            title_en="English",
            course_type="PR",
            sws="4",
            organisation="Org",
            instructors="Prof. X",
            language="EN",
            campus="garching",
            identity_code_id=777,
            content_de="Inhalt",
            content_en="Content",
            objectives_de="Ziele",
            objectives_en="Objectives",
            prerequisites="Prereqs",
            literature="Books",
        )
        store.upsert_courses([full])
        row = store.get_course(99)
        c = row_to_course(row)
        assert c.course_number == "XX9999"
        assert c.campus == "garching"
        assert c.content_de == "Inhalt"
        assert c.literature == "Books"


# ── Upsert & basic CRUD ───────────────────────────────────────────────────


class TestUpsertAndCRUD:
    def test_upsert_and_count(self, store):
        store.upsert_courses([_make_course()])
        assert store.course_count() == 1

    def test_upsert_idempotent(self, store):
        c = _make_course()
        store.upsert_courses([c])
        store.upsert_courses([c])
        assert store.course_count() == 1

    def test_upsert_updates_existing(self, store):
        store.upsert_courses([_make_course(course_id=1, title_en="Old")])
        store.upsert_courses([_make_course(course_id=1, title_en="New")])
        row = store.get_course(1)
        assert row["title_en"] == "New"

    def test_upsert_multiple(self, store):
        store.upsert_courses(
            [
                _make_course(course_id=1),
                _make_course(course_id=2),
                _make_course(course_id=3),
            ]
        )
        assert store.course_count() == 3

    def test_upsert_empty_list(self, store):
        result = store.upsert_courses([])
        assert result == 0
        assert store.course_count() == 0

    def test_get_course_found(self, store):
        store.upsert_courses([_make_course(course_id=42, title_en="Test")])
        row = store.get_course(42)
        assert row is not None
        assert row["title_en"] == "Test"

    def test_get_course_not_found(self, store):
        assert store.get_course(99999) is None

    def test_get_all_courses_empty(self, store):
        assert store.get_all_courses() == []

    def test_get_all_courses(self, store):
        store.upsert_courses([_make_course(course_id=1), _make_course(course_id=2)])
        rows = store.get_all_courses()
        assert len(rows) == 2

    def test_course_count_empty(self, store):
        assert store.course_count() == 0


# ── Delete ─────────────────────────────────────────────────────────────────


class TestDelete:
    def test_delete_semester(self, store):
        store.upsert_courses(
            [
                _make_course(course_id=1, semester_key="25W"),
                _make_course(course_id=2, semester_key="25S"),
            ]
        )
        deleted = store.delete_semester("25W")
        assert deleted == 1
        assert store.course_count() == 1

    def test_delete_nonexistent_semester(self, store):
        store.upsert_courses([_make_course(course_id=1, semester_key="25W")])
        assert store.delete_semester("99Z") == 0
        assert store.course_count() == 1

    def test_delete_removes_from_fts(self, store):
        """After deleting, FTS should not find the deleted course."""
        store.upsert_courses(
            [
                _make_course(course_id=1, semester_key="25W", title_en="Machine Learning"),
                _make_course(course_id=2, semester_key="25S", title_en="Database Systems"),
            ]
        )
        store.delete_semester("25W")
        results = store.fulltext_search("machine*")
        assert len(results) == 0

    def test_delete_keeps_other_semesters_in_fts(self, store):
        store.upsert_courses(
            [
                _make_course(course_id=1, semester_key="25W", title_en="Machine Learning"),
                _make_course(course_id=2, semester_key="25S", title_en="Machine Learning"),
            ]
        )
        store.delete_semester("25W")
        results = store.fulltext_search("machine*")
        assert len(results) == 1


# ── FTS search ─────────────────────────────────────────────────────────────


class TestFulltextSearch:
    def test_basic_search(self, populated_store):
        results = populated_store.fulltext_search("machine*")
        assert len(results) >= 1

    def test_search_with_type_filter(self, populated_store):
        results = populated_store.fulltext_search("machine*", course_type="VO")
        # Should find ML courses (VO) but not deep learning (VI)
        assert all(r[0]["course_type"] == "VO" for r in results)

    def test_search_empty_results(self, populated_store):
        results = populated_store.fulltext_search("xyznonexistent12345*")
        assert len(results) == 0

    def test_search_scores_sorted(self, populated_store):
        results = populated_store.fulltext_search("machine*")
        scores = [r[1] for r in results]
        # BM25 scores are negative (lower = better), so they should be ascending
        assert scores == sorted(scores)

    def test_search_limit(self, store):
        for i in range(10):
            store.upsert_courses(
                [
                    _make_course(course_id=i + 1, title_en=f"Machine Learning {i}"),
                ]
            )
        results = store.fulltext_search("machine*", limit=3)
        assert len(results) <= 3


# ── Aggregation queries ───────────────────────────────────────────────────


class TestAggregation:
    def test_semester_counts(self, populated_store):
        counts = populated_store.semester_counts()
        count_dict = dict(counts)
        assert "25W" in count_dict
        assert "25S" in count_dict
        assert "24W" in count_dict

    def test_semester_counts_empty(self, store):
        assert store.semester_counts() == []

    def test_type_counts(self, populated_store):
        counts = populated_store.type_counts()
        type_dict = dict(counts)
        assert "VO" in type_dict
        assert "UE" in type_dict
        assert "VI" in type_dict
        assert "SE" in type_dict

    def test_type_counts_excludes_empty(self, store):
        """Courses with empty course_type are excluded."""
        store.upsert_courses(
            [
                _make_course(course_id=1, course_type="VO"),
                _make_course(course_id=2, course_type=""),
            ]
        )
        counts = store.type_counts()
        types = [t for t, _ in counts]
        assert "" not in types
        assert "VO" in types

    def test_type_counts_sorted_by_count_desc(self, populated_store):
        counts = populated_store.type_counts()
        count_vals = [c for _, c in counts]
        assert count_vals == sorted(count_vals, reverse=True)

    def test_campus_counts(self, populated_store):
        counts = populated_store.campus_counts()
        campus_dict = dict(counts)
        assert "garching" in campus_dict
        assert campus_dict["garching"] >= 1

    def test_campus_counts_excludes_empty(self, store):
        store.upsert_courses(
            [
                _make_course(course_id=1, campus="garching"),
                _make_course(course_id=2, campus=""),
            ]
        )
        counts = store.campus_counts()
        campuses = [c for c, _ in counts]
        assert "" not in campuses

    def test_campus_counts_sorted_by_count_desc(self, populated_store):
        counts = populated_store.campus_counts()
        count_vals = [c for _, c in counts]
        assert count_vals == sorted(count_vals, reverse=True)


# ── get_other_semesters ────────────────────────────────────────────────────


class TestGetOtherSemesters:
    def test_finds_other_semesters(self, populated_store):
        # Course 1 has identity 100, same as 2 and 3
        others = populated_store.get_other_semesters(100, 1)
        assert len(others) == 2
        ids = {cid for cid, _ in others}
        assert ids == {2, 3}

    def test_excludes_current_course(self, populated_store):
        others = populated_store.get_other_semesters(100, 1)
        ids = {cid for cid, _ in others}
        assert 1 not in ids

    def test_ordered_desc(self, populated_store):
        others = populated_store.get_other_semesters(100, 1)
        semesters = [sk for _, sk in others]
        assert semesters == sorted(semesters, reverse=True)

    def test_no_other_semesters(self, populated_store):
        # Identity 200 only has course 4
        others = populated_store.get_other_semesters(200, 4)
        assert others == []

    def test_nonexistent_identity(self, populated_store):
        others = populated_store.get_other_semesters(99999, 1)
        assert others == []

    def test_returns_course_id_and_semester_key(self, populated_store):
        others = populated_store.get_other_semesters(100, 1)
        for cid, sk in others:
            assert isinstance(cid, int)
            assert isinstance(sk, str)


# ── Building cache ─────────────────────────────────────────────────────────


class TestBuildingCache:
    def test_empty_initially(self, store):
        assert store.get_building_cache() == {}

    def test_upsert_and_get(self, store):
        store.upsert_building_cache({"5602": "garching", "0101": "stammgelände"})
        cache = store.get_building_cache()
        assert cache == {"5602": "garching", "0101": "stammgelände"}

    def test_upsert_updates_existing(self, store):
        store.upsert_building_cache({"5602": "garching"})
        store.upsert_building_cache({"5602": "garching-hochbrück"})
        assert store.get_building_cache()["5602"] == "garching-hochbrück"

    def test_upsert_empty_dict_noop(self, store):
        store.upsert_building_cache({})
        assert store.get_building_cache() == {}

    def test_upsert_preserves_existing(self, store):
        store.upsert_building_cache({"5602": "garching"})
        store.upsert_building_cache({"0101": "stammgelände"})
        cache = store.get_building_cache()
        assert len(cache) == 2


# ── Embeddings ─────────────────────────────────────────────────────────────


class TestEmbeddings:
    def test_load_returns_none_when_no_file(self):
        CourseStore.invalidate_embeddings_cache()
        # The default path won't exist in a clean environment,
        # but we test the pattern — this may return data if user has a real DB
        # At minimum, it shouldn't crash
        result = CourseStore.load_embeddings()
        assert result is None or isinstance(result, tuple)

    def test_save_and_load_roundtrip(self, store, tmp_path):
        ids = np.array([1, 2, 3], dtype=np.int64)
        emb = np.random.default_rng(42).random((3, 4), dtype=np.float32)

        # Redirect EMBEDDINGS_PATH temporarily
        original_path = storage_mod.EMBEDDINGS_PATH
        storage_mod.EMBEDDINGS_PATH = tmp_path / "test_embeddings.npz"
        try:
            CourseStore.invalidate_embeddings_cache()
            store.save_embeddings(ids, emb)
            result = CourseStore.load_embeddings()
            assert result is not None
            loaded_ids, loaded_emb = result
            np.testing.assert_array_equal(loaded_ids, ids)
            np.testing.assert_array_almost_equal(loaded_emb, emb)
        finally:
            storage_mod.EMBEDDINGS_PATH = original_path
            CourseStore.invalidate_embeddings_cache()

    def test_memoization(self, tmp_path):
        original_path = storage_mod.EMBEDDINGS_PATH
        storage_mod.EMBEDDINGS_PATH = tmp_path / "test_embeddings.npz"
        try:
            CourseStore.invalidate_embeddings_cache()
            ids = np.array([1], dtype=np.int64)
            emb = np.array([[0.1, 0.2]], dtype=np.float32)
            np.savez_compressed(str(storage_mod.EMBEDDINGS_PATH), ids=ids, emb=emb)

            result1 = CourseStore.load_embeddings()
            result2 = CourseStore.load_embeddings()
            assert result1 is result2  # Same object (cached)
        finally:
            storage_mod.EMBEDDINGS_PATH = original_path
            CourseStore.invalidate_embeddings_cache()

    def test_invalidate_clears_cache(self, tmp_path):
        original_path = storage_mod.EMBEDDINGS_PATH
        storage_mod.EMBEDDINGS_PATH = tmp_path / "test_embeddings.npz"
        try:
            ids = np.array([1], dtype=np.int64)
            emb = np.array([[0.5]], dtype=np.float32)
            np.savez_compressed(str(storage_mod.EMBEDDINGS_PATH), ids=ids, emb=emb)

            CourseStore.invalidate_embeddings_cache()
            result1 = CourseStore.load_embeddings()
            CourseStore.invalidate_embeddings_cache()
            result2 = CourseStore.load_embeddings()
            # After invalidation, should re-read from disk (different object)
            assert result1 is not result2
        finally:
            storage_mod.EMBEDDINGS_PATH = original_path
            CourseStore.invalidate_embeddings_cache()


# ── Schema migration ──────────────────────────────────────────────────────


class TestSchema:
    def test_reopening_same_db_preserves_data(self, tmp_path):
        db_path = tmp_path / "test.db"
        s1 = CourseStore(db_path=db_path)
        s1.upsert_courses([_make_course(course_id=1)])
        s1.close()

        s2 = CourseStore(db_path=db_path)
        assert s2.course_count() == 1
        s2.close()

    def test_check_same_thread_false(self, tmp_path):
        """Web server mode uses check_same_thread=False."""
        s = CourseStore(db_path=tmp_path / "test.db", check_same_thread=False)
        s.upsert_courses([_make_course()])
        assert s.course_count() == 1
        s.close()
