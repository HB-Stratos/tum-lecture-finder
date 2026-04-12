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
    def test_load_returns_none_when_no_file(self, tmp_path):
        store = CourseStore(db_path=tmp_path / "test.db")
        store.invalidate_embeddings_cache()
        # EMBEDDINGS_PATH won't exist in tmp_path; result must be None
        original_path = storage_mod.EMBEDDINGS_PATH
        storage_mod.EMBEDDINGS_PATH = tmp_path / "no_embeddings.npz"
        try:
            result = store.load_embeddings()
            assert result is None
        finally:
            storage_mod.EMBEDDINGS_PATH = original_path
            store.close()

    def test_save_and_load_roundtrip(self, store, tmp_path):
        ids = np.array([1, 2, 3], dtype=np.int64)
        emb = np.random.default_rng(42).random((3, 4), dtype=np.float32)

        # Redirect EMBEDDINGS_PATH temporarily
        original_path = storage_mod.EMBEDDINGS_PATH
        storage_mod.EMBEDDINGS_PATH = tmp_path / "test_embeddings.npz"
        try:
            store.invalidate_embeddings_cache()
            store.save_embeddings(ids, emb)
            result = store.load_embeddings()
            assert result is not None
            loaded_ids, loaded_emb = result
            np.testing.assert_array_equal(loaded_ids, ids)
            np.testing.assert_array_almost_equal(loaded_emb, emb)
        finally:
            storage_mod.EMBEDDINGS_PATH = original_path
            store.invalidate_embeddings_cache()

    def test_memoization(self, tmp_path):
        store = CourseStore(db_path=tmp_path / "test.db")
        original_path = storage_mod.EMBEDDINGS_PATH
        storage_mod.EMBEDDINGS_PATH = tmp_path / "test_embeddings.npz"
        try:
            store.invalidate_embeddings_cache()
            ids = np.array([1], dtype=np.int64)
            emb = np.array([[0.1, 0.2]], dtype=np.float32)
            np.savez_compressed(str(storage_mod.EMBEDDINGS_PATH), ids=ids, emb=emb)

            result1 = store.load_embeddings()
            result2 = store.load_embeddings()
            assert result1 is result2  # Same object (cached)
        finally:
            storage_mod.EMBEDDINGS_PATH = original_path
            store.invalidate_embeddings_cache()
            store.close()

    def test_invalidate_clears_cache(self, tmp_path):
        store = CourseStore(db_path=tmp_path / "test.db")
        original_path = storage_mod.EMBEDDINGS_PATH
        storage_mod.EMBEDDINGS_PATH = tmp_path / "test_embeddings.npz"
        try:
            ids = np.array([1], dtype=np.int64)
            emb = np.array([[0.5]], dtype=np.float32)
            np.savez_compressed(str(storage_mod.EMBEDDINGS_PATH), ids=ids, emb=emb)

            store.invalidate_embeddings_cache()
            result1 = store.load_embeddings()
            store.invalidate_embeddings_cache()
            result2 = store.load_embeddings()
            # After invalidation, should re-read from disk (different object)
            assert result1 is not result2
        finally:
            storage_mod.EMBEDDINGS_PATH = original_path
            store.invalidate_embeddings_cache()
            store.close()


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

    def test_schema_migration_drops_and_recreates(self, tmp_path, monkeypatch):
        """When schema version bumps, the DB is wiped and rebuilt cleanly."""
        import tum_lecture_finder.storage as storage_mod

        db_path = tmp_path / "test.db"

        # Create a DB at the current schema version
        s1 = CourseStore(db_path=db_path)
        s1.upsert_courses([_make_course(course_id=1)])
        assert s1.course_count() == 1
        s1.close()

        # Simulate a schema version bump by patching the version constant
        old_version = storage_mod._SCHEMA_VERSION
        monkeypatch.setattr(storage_mod, "_SCHEMA_VERSION", old_version + 1)

        # Opening the DB should migrate (drop + recreate) — courses are gone
        s2 = CourseStore(db_path=db_path)
        assert s2.course_count() == 0  # data wiped after migration
        s2.close()

        # Restore so other tests aren't affected (monkeypatch handles this, but be explicit)


# ── compute_other_semesters ──────────────────────────────────────────────────


class TestComputeOtherSemesters:
    """compute_other_semesters() links same-course entries across semesters."""

    def test_links_same_identity_code_across_semesters(self, tmp_path):
        """Courses with the same identity_code_id are linked."""
        store = CourseStore(db_path=tmp_path / "test.db")
        store.upsert_courses([
            _make_course(course_id=1, semester_key="25W", identity_code_id=100),
            _make_course(course_id=2, semester_key="25S", identity_code_id=100),
            _make_course(course_id=3, semester_key="24W", identity_code_id=100),
        ])
        store.compute_other_semesters()

        # Each course should see the other two semesters in other_semesters
        row = store.get_course(1)
        assert row is not None
        other = row["other_semesters"] or ""
        assert "25S" in other or "24W" in other

        store.close()

    def test_no_links_without_identity_code(self, tmp_path):
        """Courses with identity_code_id=0 are not cross-linked."""
        store = CourseStore(db_path=tmp_path / "test.db")
        store.upsert_courses([
            _make_course(course_id=1, semester_key="25W", identity_code_id=0),
            _make_course(course_id=2, semester_key="25S", identity_code_id=0),
        ])
        store.compute_other_semesters()

        row = store.get_course(1)
        other = row["other_semesters"] if row else ""
        assert not other  # no cross-reference

        store.close()

    def test_different_identity_codes_not_linked(self, tmp_path):
        """Courses with different identity codes remain independent."""
        store = CourseStore(db_path=tmp_path / "test.db")
        store.upsert_courses([
            _make_course(course_id=1, semester_key="25W", identity_code_id=100),
            _make_course(course_id=2, semester_key="25S", identity_code_id=200),
        ])
        store.compute_other_semesters()

        row1 = store.get_course(1)
        other1 = row1["other_semesters"] if row1 else ""
        assert not other1  # identity 100 has only one entry

        store.close()

    def test_idempotent(self, tmp_path):
        """Calling compute_other_semesters twice gives the same result."""
        store = CourseStore(db_path=tmp_path / "test.db")
        store.upsert_courses([
            _make_course(course_id=1, semester_key="25W", identity_code_id=100),
            _make_course(course_id=2, semester_key="25S", identity_code_id=100),
        ])
        store.compute_other_semesters()
        row_after_first = store.get_course(1)
        other_first = row_after_first["other_semesters"] if row_after_first else ""

        store.compute_other_semesters()
        row_after_second = store.get_course(1)
        other_second = row_after_second["other_semesters"] if row_after_second else ""

        assert other_first == other_second
        store.close()


class TestAtomicEmbeddingsWrite:
    """Verify save_embeddings uses atomic write-then-rename."""

    def test_save_embeddings_atomic_rename(self, store, tmp_path, monkeypatch):
        """save_embeddings writes to a temp file then renames, not direct write."""
        emb_path = tmp_path / "embeddings.npz"
        monkeypatch.setattr(storage_mod, "EMBEDDINGS_PATH", emb_path)

        ids = np.array([1, 2], dtype=np.int64)
        emb = np.random.default_rng(42).random((2, 4)).astype(np.float32)
        store.save_embeddings(ids, emb)

        assert emb_path.exists()
        # Temp file should have been cleaned up by the rename
        assert not emb_path.with_name(emb_path.stem + "_tmp.npz").exists()

        # Verify data round-trips correctly
        data = np.load(emb_path)
        np.testing.assert_array_equal(data["ids"], ids)
        np.testing.assert_array_almost_equal(data["emb"], emb)

    def test_save_embeddings_preserves_old_on_failure(self, store, tmp_path, monkeypatch):
        """If the write fails, the old embeddings file is preserved."""
        emb_path = tmp_path / "embeddings.npz"
        monkeypatch.setattr(storage_mod, "EMBEDDINGS_PATH", emb_path)

        # Write initial embeddings
        ids_old = np.array([1], dtype=np.int64)
        emb_old = np.ones((1, 4), dtype=np.float32)
        store.save_embeddings(ids_old, emb_old)

        # Monkeypatch np.savez_compressed to raise mid-write
        def _failing_save(*_args, **_kwargs):
            msg = "disk full"
            raise OSError(msg)

        monkeypatch.setattr("numpy.savez_compressed", _failing_save)

        with pytest.raises(OSError, match="disk full"):
            store.save_embeddings(
                np.array([99], dtype=np.int64),
                np.zeros((1, 4), dtype=np.float32),
            )

        # Old file should still be intact
        data = np.load(emb_path)
        np.testing.assert_array_equal(data["ids"], ids_old)


class TestGetCourseIdsWithDetails:
    """Tests for CourseStore.get_course_ids_with_details()."""

    def test_returns_ids_with_descriptions(self, store):
        """Only courses with at least one non-empty description field are returned."""
        store.upsert_courses([
            _make_course(course_id=1, semester_key="25W", content_en="Has content"),
            _make_course(course_id=2, semester_key="25W"),  # no descriptions
            _make_course(course_id=3, semester_key="25W", objectives_de="Has objectives"),
        ])
        result = store.get_course_ids_with_details()
        assert result == {1, 3}

    def test_filters_by_semester(self, store):
        """When semester_keys is given, only matching semesters are queried."""
        store.upsert_courses([
            _make_course(course_id=1, semester_key="25W", content_en="A"),
            _make_course(course_id=2, semester_key="25S", content_en="B"),
            _make_course(course_id=3, semester_key="24W", content_en="C"),
        ])
        result = store.get_course_ids_with_details(semester_keys=["25W", "24W"])
        assert result == {1, 3}

    def test_empty_db_returns_empty_set(self, store):
        assert store.get_course_ids_with_details() == set()

    def test_no_semester_filter_returns_all(self, store):
        """Without semester_keys, all semesters are included."""
        store.upsert_courses([
            _make_course(course_id=1, semester_key="25W", content_de="A"),
            _make_course(course_id=2, semester_key="24W", content_en="B"),
        ])
        result = store.get_course_ids_with_details()
        assert result == {1, 2}


class TestSmartUpsert:
    """Tests for smart upsert: empty detail fields don't clobber existing data."""

    def test_preserves_descriptions_by_default(self, store):
        """Re-upserting with empty descriptions must not overwrite existing ones."""
        store.upsert_courses([
            _make_course(
                course_id=1,
                semester_key="25W",
                title_en="Old Title",
                content_en="Important content",
                objectives_de="Learning goals",
                campus="garching",
            ),
        ])
        # Second upsert with updated title but empty detail fields
        store.upsert_courses([
            _make_course(
                course_id=1,
                semester_key="25W",
                title_en="New Title",
            ),
        ])
        row = store.get_course(1)
        assert row["title_en"] == "New Title"
        assert row["content_en"] == "Important content"
        assert row["objectives_de"] == "Learning goals"
        assert row["campus"] == "garching"

    def test_overwrites_with_real_values(self, store):
        """Non-empty new values must replace old ones."""
        store.upsert_courses([
            _make_course(course_id=1, semester_key="25W", content_en="Old"),
        ])
        store.upsert_courses([
            _make_course(course_id=1, semester_key="25W", content_en="New"),
        ])
        row = store.get_course(1)
        assert row["content_en"] == "New"

    def test_force_overwrite_clobbers_descriptions(self, store):
        """force_overwrite=True allows clearing detail fields."""
        store.upsert_courses([
            _make_course(
                course_id=1,
                semester_key="25W",
                content_en="Important",
                campus="garching",
            ),
        ])
        store.upsert_courses(
            [_make_course(course_id=1, semester_key="25W")],
            force_overwrite=True,
        )
        row = store.get_course(1)
        assert row["content_en"] == ""
        assert row["campus"] == ""

    def test_new_course_inserts_empty_strings(self, store):
        """A brand-new course stores empty detail fields as-is (no conflict path)."""
        store.upsert_courses([
            _make_course(course_id=99, semester_key="26S", title_en="New Course"),
        ])
        row = store.get_course(99)
        assert row is not None
        assert row["title_en"] == "New Course"
        assert row["content_en"] == ""
        assert row["objectives_en"] == ""

    def test_always_overwrites_list_fields(self, store):
        """List-level fields like instructors and organisation are always updated."""
        store.upsert_courses([
            _make_course(
                course_id=1,
                semester_key="25W",
                instructors="Prof A",
                organisation="Chair A",
            ),
        ])
        store.upsert_courses([
            _make_course(
                course_id=1,
                semester_key="25W",
                instructors="Prof B",
                organisation="Chair B",
            ),
        ])
        row = store.get_course(1)
        assert row["instructors"] == "Prof B"
        assert row["organisation"] == "Chair B"

    def test_preserves_all_seven_detail_fields(self, store):
        """All 7 detail fields are preserved when re-upserted with empty values."""
        store.upsert_courses([
            _make_course(
                course_id=1,
                semester_key="25W",
                campus="garching",
                content_de="Inhalt",
                content_en="Content",
                objectives_de="Ziele",
                objectives_en="Objectives",
                prerequisites="Prereqs",
                literature="Lit",
            ),
        ])
        # Re-upsert with all detail fields empty (default)
        store.upsert_courses([
            _make_course(course_id=1, semester_key="25W"),
        ])
        row = store.get_course(1)
        assert row["campus"] == "garching"
        assert row["content_de"] == "Inhalt"
        assert row["content_en"] == "Content"
        assert row["objectives_de"] == "Ziele"
        assert row["objectives_en"] == "Objectives"
        assert row["prerequisites"] == "Prereqs"
        assert row["literature"] == "Lit"


class TestSetGetMeta:
    """Tests for CourseStore.set_meta() and get_meta()."""

    def test_round_trip(self, store):
        store.set_meta("test_key", "test_value")
        assert store.get_meta("test_key") == "test_value"

    def test_get_missing_returns_none(self, store):
        assert store.get_meta("nonexistent") is None

    def test_overwrite(self, store):
        store.set_meta("key", "old")
        store.set_meta("key", "new")
        assert store.get_meta("key") == "new"

    def test_get_with_default(self, store):
        assert store.get_meta("missing", default="fallback") == "fallback"


class TestUpsertCommitParam:
    """Tests for the commit parameter on upsert methods."""

    def test_upsert_courses_no_commit(self, store):
        """With commit=False, data is not visible after rollback."""
        store.upsert_courses(
            [_make_course(course_id=1, semester_key="25W")],
            commit=False,
        )
        store._conn.rollback()
        assert store.get_course(1) is None

    def test_upsert_courses_default_commits(self, store):
        """Default commit=True makes data visible immediately."""
        store.upsert_courses([_make_course(course_id=1, semester_key="25W")])
        assert store.get_course(1) is not None


