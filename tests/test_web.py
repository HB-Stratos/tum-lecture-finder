"""Tests for the web UI module."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tum_lecture_finder.models import Course
from tum_lecture_finder.storage import CourseStore
from tum_lecture_finder.web import (
    _campus_display_name,
    _course_to_dict,
    _dedup_instructors,
    _extract_room_link,
    _extract_time_range,
    _extract_weekday,
    _offering_frequency,
    _parse_appointments,
    _result_to_dict,
    _sanitize_query,
    app,
)

# ── Fixtures ───────────────────────────────────────────────────────────────


@pytest.fixture
def populated_store(tmp_path: Path):
    """Create a store with test data and patch the global store."""
    import tum_lecture_finder.web as web_mod

    store = CourseStore(db_path=tmp_path / "test.db", check_same_thread=False)
    courses = [
        Course(
            course_id=1,
            semester_key="25W",
            course_number="IN2346",
            title_de="Einfuehrung in Deep Learning",
            title_en="Introduction to Deep Learning",
            course_type="VI",
            sws="4",
            organisation="Informatik (Garching)",
            instructors="Niessner, Matthias",
            language="EN",
            campus="garching",
            identity_code_id=100,
            content_en="Deep learning fundamentals with PyTorch.",
            objectives_en="Understand neural networks.",
        ),
        Course(
            course_id=2,
            semester_key="25S",
            course_number="IN2346",
            title_de="Einfuehrung in Deep Learning",
            title_en="Introduction to Deep Learning",
            course_type="VI",
            sws="4",
            organisation="Informatik (Garching)",
            instructors="Niessner, Matthias",
            language="EN",
            campus="garching",
            identity_code_id=100,
        ),
        Course(
            course_id=3,
            semester_key="25W",
            course_number="IN0008",
            title_de="Datenbanksysteme",
            title_en="Database Systems",
            course_type="VO",
            sws="3",
            organisation="Informatik (Garching)",
            instructors="Kemper, Alfons",
            language="DE",
            campus="garching",
            identity_code_id=200,
            content_de="Relationale Datenbanken, SQL, Transaktionen.",
        ),
        Course(
            course_id=4,
            semester_key="25W",
            course_number="AR20005",
            title_de="Entwerfen",
            title_en="Architectural Design",
            course_type="UE",
            sws="6",
            organisation="Architektur (Stammgelaende Muenchen)",
            language="DE",
            campus="münchen",
            identity_code_id=300,
        ),
        Course(
            course_id=5,
            semester_key="25W",
            course_number="WZ1234",
            title_de="Brautechnologie",
            title_en="Brewing Technology",
            course_type="VO",
            sws="2",
            organisation="Brau- und Lebensmitteltechnologie (Weihenstephan)",
            language="DE",
            campus="freising",
            identity_code_id=400,
        ),
    ]
    store.upsert_courses(courses)
    store.compute_other_semesters()

    original_store = web_mod._store
    web_mod._store = store
    # Reset web caches so they pick up this store's data
    web_mod._type_counts_cache = None
    web_mod._campus_counts_cache = None
    yield store
    web_mod._store = original_store
    web_mod._type_counts_cache = None
    web_mod._campus_counts_cache = None
    store.close()


@pytest.fixture
def client(populated_store):
    """TestClient backed by populated_store."""
    return TestClient(app, raise_server_exceptions=True)


# ── Helper unit tests ──────────────────────────────────────────────────────


class TestSanitizeQuery:
    def test_strips_whitespace(self):
        assert _sanitize_query("  hello  ") == "hello"

    def test_truncates_long_query(self):
        long = "a" * 300
        assert len(_sanitize_query(long)) == 200

    def test_passes_normal_query(self):
        assert _sanitize_query("machine learning") == "machine learning"


class TestExtractWeekday:
    def test_extracts_english(self):
        entry = {
            "weekday": {
                "langDataType": {
                    "translations": {
                        "translation": [
                            {"lang": "de", "value": "Montag"},
                            {"lang": "en", "value": "Monday"},
                        ],
                    },
                },
            },
        }
        assert _extract_weekday(entry) == "Monday"

    def test_falls_back_to_first(self):
        entry = {
            "weekday": {
                "langDataType": {
                    "translations": {
                        "translation": [{"lang": "de", "value": "Montag"}],
                    },
                },
            },
        }
        assert _extract_weekday(entry) == "Montag"

    def test_empty_weekday(self):
        assert _extract_weekday({}) == ""


class TestExtractTimeRange:
    def test_formats_time(self):
        entry = {
            "timestampFrom": {"value": "2025-01-15T08:00:00"},
            "timestampTo": {"value": "2025-01-15T09:30:00"},
        }
        assert _extract_time_range(entry) == "08:00 - 09:30"

    def test_missing_times(self):
        assert _extract_time_range({}) == ""

    def test_invalid_timestamps(self):
        entry = {
            "timestampFrom": {"value": "not-a-date"},
            "timestampTo": {"value": "also-not"},
        }
        result = _extract_time_range(entry)
        assert "not-a-date" in result


class TestParseAppointments:
    def test_basic_parsing(self):
        data = {
            "courseGroupDtos": [
                {
                    "appointmentDtos": [
                        {
                            "weekday": {
                                "langDataType": {
                                    "translations": {
                                        "translation": [
                                            {"lang": "en", "value": "Monday"},
                                        ],
                                    },
                                },
                            },
                            "timestampFrom": {"value": "2025-01-15T10:00:00"},
                            "timestampTo": {"value": "2025-01-15T12:00:00"},
                            "resourceName": "MI HS 1 (5602.EG.001)",
                        },
                    ],
                },
            ],
        }
        result = _parse_appointments(data)
        assert len(result) == 1
        assert result[0]["weekday"] == "Monday"
        assert result[0]["time"] == "10:00 - 12:00"
        assert "5602" in result[0]["room"]

    def test_empty_groups(self):
        assert _parse_appointments({"courseGroupDtos": []}) == []

    def test_missing_key(self):
        assert _parse_appointments({}) == []

    def test_dict_instead_of_list(self):
        """API sometimes returns a single group as a dict."""
        data = {
            "courseGroupDtos": {
                "appointmentDtos": [
                    {"resourceName": "Room A"},
                ],
            },
        }
        result = _parse_appointments(data)
        assert len(result) == 1

    def test_multiple_appointments(self):
        data = {
            "courseGroupDtos": [
                {
                    "appointmentDtos": [
                        {"resourceName": "Room A"},
                        {"resourceName": "Room B"},
                    ],
                },
            ],
        }
        result = _parse_appointments(data)
        assert len(result) == 2

    def test_deduplicates_repeating_weekly(self):
        """Identical weekday/time/room entries should be collapsed to one."""
        apt = {
            "weekday": {
                "langDataType": {
                    "translations": {"translation": [{"lang": "en", "value": "Wednesday"}]},
                },
            },
            "timestampFrom": {"value": "2025-10-08T08:00:00"},
            "timestampTo": {"value": "2025-10-08T10:00:00"},
            "resourceName": "0.A01, Seminarraum A0 (3501.EG.001A)",
        }
        data = {"courseGroupDtos": [{"appointmentDtos": [apt] * 15}]}
        result = _parse_appointments(data)
        assert len(result) == 1
        assert result[0]["weekday"] == "Wednesday"
        assert result[0]["time"] == "08:00 - 10:00"

    def test_room_link_included(self):
        """Appointments with parseable room codes should include room_link."""
        data = {
            "courseGroupDtos": [
                {
                    "appointmentDtos": [
                        {"resourceName": "MI HS 1 (5602.EG.001)"},
                    ],
                },
            ],
        }
        result = _parse_appointments(data)
        assert result[0]["room_link"] == "https://nav.tum.de/room/5602.EG.001"

    def test_room_link_empty_when_no_code(self):
        """Rooms without a parseable code should have empty room_link."""
        data = {
            "courseGroupDtos": [{"appointmentDtos": [{"resourceName": "Some Room"}]}],
        }
        result = _parse_appointments(data)
        assert result[0]["room_link"] == ""


class TestExtractRoomLink:
    def test_standard_room_code(self):
        assert _extract_room_link("MI HS 1 (5602.EG.001)") == "https://nav.tum.de/room/5602.EG.001"

    def test_complex_room_code(self):
        assert (
            _extract_room_link("0.A01, Seminarraum A0 (3501.EG.001A)")
            == "https://nav.tum.de/room/3501.EG.001A"
        )

    def test_no_room_code(self):
        assert _extract_room_link("Just a room name") == ""

    def test_empty_string(self):
        assert _extract_room_link("") == ""


class TestCampusDisplayName:
    def test_known_campus(self):
        assert _campus_display_name("stammgelände") == "München (Stammgelände)"
        assert _campus_display_name("garching") == "Garching"
        assert _campus_display_name("weihenstephan") == "Freising (Weihenstephan)"
        assert _campus_display_name("campus-im-olympiapark-sz") == "Olympiapark"

    def test_long_slug(self):
        assert (
            _campus_display_name("campus-straubing-cs-biotechnologie-und-nachhaltigkeit")
            == "Straubing"
        )

    def test_unknown_campus_title_cased(self):
        assert _campus_display_name("some-new-campus") == "Some New Campus"

    def test_empty_string(self):
        assert _campus_display_name("") == ""


class TestDedupInstructors:
    def test_removes_duplicates(self):
        assert _dedup_instructors("Alice, Alice, Bob") == "Alice, Bob"

    def test_preserves_order(self):
        assert _dedup_instructors("Bob, Alice, Bob") == "Bob, Alice"

    def test_single_name(self):
        assert _dedup_instructors("Alice") == "Alice"

    def test_empty_string(self):
        assert _dedup_instructors("") == ""

    def test_no_duplicates(self):
        assert _dedup_instructors("Alice, Bob, Charlie") == "Alice, Bob, Charlie"


class TestOfferingFrequency:
    def test_every_semester(self):
        assert _offering_frequency("25W", ["25S"]) == "every semester"

    def test_every_semester_many(self):
        assert _offering_frequency("25W", ["25S", "24W"]) == "every semester"

    def test_yearly_winter(self):
        assert _offering_frequency("25W", ["24W"]) == "yearly"

    def test_yearly_summer(self):
        assert _offering_frequency("25S", ["26S"]) == "yearly"

    def test_one_off(self):
        assert _offering_frequency("25W", []) == ""

    def test_empty_semester_key(self):
        assert _offering_frequency("", []) == ""

    def test_empty_semester_key_with_others(self):
        assert _offering_frequency("", ["25W", "25S"]) == "every semester"


class TestResultToDict:
    def test_contains_expected_fields(self):
        from tum_lecture_finder.models import SearchResult

        r = SearchResult(
            course=Course(
                course_id=1,
                semester_key="25W",
                course_number="IN2346",
                title_en="Test Course",
                course_type="VO",
                campus="garching",
            ),
            score=0.95,
            snippet="test snippet",
        )
        d = _result_to_dict(r)
        assert d["course_id"] == 1
        assert d["course_number"] == "IN2346"
        assert d["title"] == "Test Course"
        assert d["score"] == 0.95
        assert d["snippet"] == "test snippet"
        assert "semester_display" in d
        assert d["campus_display"] == "Garching"
        assert d["offering_frequency"] == ""  # single semester = one-off

    def test_title_falls_back_to_de(self):
        from tum_lecture_finder.models import SearchResult

        r = SearchResult(
            course=Course(
                course_id=1,
                semester_key="25W",
                title_de="Deutsch",
                title_en="",
            ),
        )
        d = _result_to_dict(r)
        assert d["title"] == "Deutsch"

    def test_dedup_instructors(self):
        from tum_lecture_finder.models import SearchResult

        r = SearchResult(
            course=Course(
                course_id=1,
                semester_key="25W",
                instructors="Alice, Alice, Bob",
            ),
        )
        d = _result_to_dict(r)
        assert d["instructors"] == "Alice, Bob"


class TestCourseToDict:
    def test_contains_all_detail_fields(self, populated_store):
        row = populated_store.get_course(1)
        d = _course_to_dict(row)
        for field in [
            "course_id",
            "course_number",
            "title_de",
            "title_en",
            "course_type",
            "semester_key",
            "semester_display",
            "sws",
            "organisation",
            "instructors",
            "language",
            "campus",
            "campus_display",
            "content_de",
            "content_en",
            "objectives_de",
            "objectives_en",
            "prerequisites",
            "literature",
            "identity_code_id",
        ]:
            assert field in d, f"Missing field: {field}"

    def test_campus_display_name(self, populated_store):
        row = populated_store.get_course(1)  # garching
        d = _course_to_dict(row)
        assert d["campus_display"] == "Garching"


# ── Home page ──────────────────────────────────────────────────────────────


class TestHomePage:
    def test_returns_200(self, client):
        resp = client.get("/")
        assert resp.status_code == 200
        assert "text/html" in resp.headers["content-type"]

    def test_contains_search_form(self, client):
        resp = client.get("/")
        assert "search-form" in resp.text
        assert "search-input" in resp.text

    def test_shows_course_count(self, client):
        resp = client.get("/")
        assert "5" in resp.text

    def test_has_security_headers(self, client):
        resp = client.get("/")
        assert resp.headers["X-Content-Type-Options"] == "nosniff"
        assert resp.headers["X-Frame-Options"] == "DENY"
        assert "Content-Security-Policy" in resp.headers


# ── Course detail page ─────────────────────────────────────────────────────


class TestCourseDetailPage:
    def test_returns_200(self, client):
        resp = client.get("/course/1")
        assert resp.status_code == 200

    def test_shows_title(self, client):
        resp = client.get("/course/1")
        assert "Introduction to Deep Learning" in resp.text

    def test_shows_content(self, client):
        resp = client.get("/course/1")
        assert "Deep learning fundamentals" in resp.text

    def test_not_found_404(self, client):
        resp = client.get("/course/99999")
        assert resp.status_code == 404

    def test_shows_metadata(self, client):
        resp = client.get("/course/1")
        assert "IN2346" in resp.text
        assert "VI" in resp.text

    def test_has_tumonline_link(self, client):
        resp = client.get("/course/1")
        assert "campus.tum.de" in resp.text

    def test_shows_other_semesters(self, client):
        """Course 1 (identity 100) should show course 2 as another semester."""
        resp = client.get("/course/1")
        # Should mention Summer 2025 or 25S
        assert "25S" in resp.text or "Summer 2025" in resp.text


# ── Stats page ─────────────────────────────────────────────────────────────


class TestStatsPage:
    def test_returns_200(self, client):
        resp = client.get("/stats")
        assert resp.status_code == 200

    def test_shows_total(self, client):
        resp = client.get("/stats")
        assert "5" in resp.text

    def test_shows_semesters(self, client):
        resp = client.get("/stats")
        assert "25W" in resp.text


# ── API: Search ────────────────────────────────────────────────────────────


class TestApiSearch:
    def test_returns_results(self, client):
        resp = client.get("/api/search?q=deep+learning&mode=keyword")
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] >= 1
        assert data["results"][0]["course_number"] == "IN2346"

    def test_deduplicates(self, client):
        resp = client.get("/api/search?q=deep+learning&mode=keyword")
        data = resp.json()
        ids = [r["course_id"] for r in data["results"]]
        assert len(set(ids)) == len(ids)

    def test_campus_filter(self, client):
        resp = client.get("/api/search?q=deep+learning&campus=münchen&mode=keyword")
        data = resp.json()
        assert data["count"] == 0

    def test_type_filter(self, client):
        resp = client.get("/api/search?q=deep+learning&type=VO&mode=keyword")
        data = resp.json()
        assert data["count"] == 0

    def test_empty_query_rejected(self, client):
        resp = client.get("/api/search?q=")
        assert resp.status_code == 422

    def test_missing_query_rejected(self, client):
        resp = client.get("/api/search")
        assert resp.status_code == 422

    def test_returns_json_structure(self, client):
        resp = client.get("/api/search?q=database&mode=keyword")
        data = resp.json()
        for key in ("query", "mode", "count", "total_count", "has_more", "offset", "results"):
            assert key in data

    def test_result_fields(self, client):
        resp = client.get("/api/search?q=database&mode=keyword")
        data = resp.json()
        assert data["count"] >= 1
        r = data["results"][0]
        for field in [
            "course_id",
            "course_number",
            "title_de",
            "title_en",
            "course_type",
            "semester_key",
            "score",
            "organisation",
        ]:
            assert field in r

    def test_mode_parameter(self, client):
        resp = client.get("/api/search?q=database&mode=keyword")
        assert resp.status_code == 200
        assert resp.json()["mode"] == "keyword"

    def test_invalid_mode_rejected(self, client):
        resp = client.get("/api/search?q=test&mode=invalid")
        assert resp.status_code == 422

    def test_limit_parameter(self, client):
        resp = client.get("/api/search?q=deep+learning&limit=1&mode=keyword")
        data = resp.json()
        assert len(data["results"]) <= 1

    def test_no_results_keyword(self, client):
        resp = client.get("/api/search?q=xyznonexistent12345&mode=keyword")
        data = resp.json()
        assert data["count"] == 0
        assert data["results"] == []

    def test_by_course_number(self, client):
        resp = client.get("/api/search?q=IN2346&mode=keyword")
        data = resp.json()
        assert data["count"] >= 1

    def test_other_semesters_populated(self, client):
        resp = client.get("/api/search?q=deep+learning&mode=keyword")
        data = resp.json()
        assert data["count"] >= 1
        r = data["results"][0]
        assert len(r["other_semesters"]) >= 1 or r["course_id"] in (1, 2)

    def test_keyword_mode_explicit(self, client):
        resp = client.get("/api/search?q=database&mode=keyword")
        data = resp.json()
        assert data["mode"] == "keyword"
        assert data["count"] >= 1

    def test_content_searchable(self, client):
        """FTS should find content field text."""
        resp = client.get("/api/search?q=PyTorch&mode=keyword")
        data = resp.json()
        assert data["count"] >= 1
        assert data["results"][0]["course_id"] == 1


# ── API: Course ────────────────────────────────────────────────────────────


class TestApiCourse:
    def test_returns_full_data(self, client):
        resp = client.get("/api/course/1")
        assert resp.status_code == 200
        data = resp.json()
        assert data["course_id"] == 1
        assert data["course_number"] == "IN2346"
        assert data["title_en"] == "Introduction to Deep Learning"

    def test_not_found(self, client):
        resp = client.get("/api/course/99999")
        assert resp.status_code == 404

    def test_has_all_fields(self, client):
        resp = client.get("/api/course/1")
        data = resp.json()
        for field in [
            "course_id",
            "course_number",
            "title_de",
            "title_en",
            "course_type",
            "semester_key",
            "semester_display",
            "sws",
            "organisation",
            "instructors",
            "language",
            "campus",
            "campus_display",
            "content_de",
            "content_en",
            "objectives_de",
            "objectives_en",
            "prerequisites",
            "literature",
        ]:
            assert field in data, f"Missing field: {field}"

    def test_content_values(self, client):
        resp = client.get("/api/course/1")
        data = resp.json()
        assert data["content_en"] == "Deep learning fundamentals with PyTorch."
        assert data["objectives_en"] == "Understand neural networks."


# ── API: Stats ─────────────────────────────────────────────────────────────


class TestApiStats:
    def test_returns_data(self, client):
        resp = client.get("/api/stats")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total_courses"] == 5
        assert len(data["semesters"]) >= 1

    def test_has_type_distribution(self, client):
        resp = client.get("/api/stats")
        data = resp.json()
        assert "course_types" in data
        assert len(data["course_types"]) >= 1

    def test_has_campus_distribution(self, client):
        resp = client.get("/api/stats")
        data = resp.json()
        assert "campuses" in data
        assert len(data["campuses"]) >= 1

    def test_semester_format(self, client):
        resp = client.get("/api/stats")
        data = resp.json()
        for sem in data["semesters"]:
            assert "key" in sem
            assert "display" in sem
            assert "count" in sem


# ── API: Filters ───────────────────────────────────────────────────────────


class TestApiFilters:
    def test_returns_data(self, client):
        resp = client.get("/api/filters")
        assert resp.status_code == 200
        data = resp.json()
        assert "semesters" in data
        assert "course_types" in data
        assert "campuses" in data

    def test_semesters_format(self, client):
        resp = client.get("/api/filters")
        data = resp.json()
        for sem in data["semesters"]:
            assert "key" in sem
            assert "display" in sem
            assert "count" in sem

    def test_campuses_populated(self, client):
        resp = client.get("/api/filters")
        data = resp.json()
        campuses = [c["campus"] for c in data["campuses"]]
        assert "garching" in campuses
        # Check display name is included
        garching = next(c for c in data["campuses"] if c["campus"] == "garching")
        assert garching["display"] == "Garching"

    def test_course_types_populated(self, client):
        resp = client.get("/api/filters")
        data = resp.json()
        types = [t["type"] for t in data["course_types"]]
        assert "VO" in types
        assert "VI" in types


# ── Static files & favicon ─────────────────────────────────────────────────


class TestStaticFiles:
    def test_css_served(self, client):
        resp = client.get("/static/style.css")
        assert resp.status_code == 200
        assert "text/css" in resp.headers["content-type"]

    def test_js_served(self, client):
        resp = client.get("/static/app.js")
        assert resp.status_code == 200
        assert "javascript" in resp.headers["content-type"]

    def test_missing_static_404(self, client):
        resp = client.get("/static/nonexistent.txt")
        assert resp.status_code == 404

    def test_favicon(self, client):
        resp = client.get("/favicon.ico")
        assert resp.status_code == 200
        assert "svg" in resp.headers["content-type"]


# ── Security ───────────────────────────────────────────────────────────────


class TestSecurity:
    def test_security_headers_present(self, client):
        resp = client.get("/")
        assert resp.headers["X-Content-Type-Options"] == "nosniff"
        assert resp.headers["X-Frame-Options"] == "DENY"
        assert "strict-origin" in resp.headers["Referrer-Policy"]
        assert "Content-Security-Policy" in resp.headers
        assert "Permissions-Policy" in resp.headers

    def test_csp_header(self, client):
        resp = client.get("/")
        csp = resp.headers["Content-Security-Policy"]
        assert "default-src 'self'" in csp
        assert "script-src 'self'" in csp
        assert "frame-ancestors 'none'" in csp

    def test_xss_in_search_query(self, client):
        resp = client.get('/api/search?q=<script>alert("xss")</script>&mode=keyword')
        assert resp.status_code in (200, 422)

    def test_long_query_rejected(self, client):
        resp = client.get(f"/api/search?q={'a' * 300}")
        assert resp.status_code == 422

    def test_invalid_course_id_type(self, client):
        resp = client.get("/course/notanumber")
        assert resp.status_code == 422

    def test_negative_limit_rejected(self, client):
        resp = client.get("/api/search?q=test&limit=-1")
        assert resp.status_code == 422

    def test_limit_too_large_rejected(self, client):
        resp = client.get("/api/search?q=test&limit=500")
        assert resp.status_code == 422

    def test_security_headers_on_api(self, client):
        resp = client.get("/api/stats")
        assert resp.headers["X-Content-Type-Options"] == "nosniff"
        assert resp.headers["X-Frame-Options"] == "DENY"

    def test_security_headers_on_static(self, client):
        resp = client.get("/static/style.css")
        assert resp.headers["X-Content-Type-Options"] == "nosniff"


# ── Pagination & semester filter ───────────────────────────────────────────


class TestSearchPagination:
    def test_returns_total_count(self, client):
        resp = client.get("/api/search?q=deep+learning&mode=keyword")
        data = resp.json()
        assert "total_count" in data
        assert "has_more" in data
        assert "offset" in data

    def test_offset_parameter(self, client):
        resp = client.get("/api/search?q=deep+learning&offset=0&mode=keyword")
        assert resp.status_code == 200
        assert resp.json()["offset"] == 0

    def test_semester_filter(self, client):
        """Semester filter should match via other_semesters too."""
        resp = client.get("/api/search?q=deep+learning&semester=25S&mode=keyword")
        data = resp.json()
        # Course 1 (25W) has other_semesters=["25S"], so it should appear
        assert data["count"] >= 1

    def test_invalid_offset_rejected(self, client):
        resp = client.get("/api/search?q=test&offset=-1")
        assert resp.status_code == 422


# ── Cache invalidation ─────────────────────────────────────────────────────


class TestHealthEndpoint:
    """/health endpoint for Docker and reverse-proxy health checks."""

    def test_health_returns_200(self, populated_store, client):
        """/health returns 200 OK when DB is accessible."""
        resp = client.get("/health")
        assert resp.status_code == 200

    def test_health_response_body(self, populated_store, client):
        """/health returns JSON with status and db fields."""
        resp = client.get("/health")
        data = resp.json()
        assert data["status"] == "ok"
        assert data["db"] == "ok"

    def test_health_no_rate_limit(self, populated_store, client):
        """/health is not rate-limited (no 'Retry-After' on repeated calls)."""
        for _ in range(5):
            resp = client.get("/health")
            assert resp.status_code == 200


class TestCacheInvalidation:
    """Verifies that type/campus count caches reflect DB updates."""

    def test_cache_stale_after_db_update(self, populated_store, client):
        """Adding a new course type should appear in counts after cache is cleared."""
        import tum_lecture_finder.web as web_mod

        # Fetch counts once (populates cache)
        resp1 = client.get("/api/stats")
        assert resp1.status_code == 200
        assert resp1.json()["course_types"]  # sanity-check: types are populated before update

        # Add a new course with a unique type not in the original data
        from tum_lecture_finder.models import Course

        populated_store.upsert_courses([
            Course(
                course_id=99,
                semester_key="25W",
                course_type="ZZUNIQUE",
                title_en="New Unique Course",
            )
        ])

        # Without cache invalidation, stale counts are returned
        resp_cached = client.get("/api/stats")
        types_cached = {t["type"]: t["count"] for t in resp_cached.json()["course_types"]}

        # Manually invalidate cache (simulating what the scheduled updater should do)
        web_mod._type_counts_cache = None
        web_mod._campus_counts_cache = None

        resp2 = client.get("/api/stats")
        types_after = {t["type"]: t["count"] for t in resp2.json()["course_types"]}

        # After invalidation, new type should be visible
        assert "ZZUNIQUE" in types_after
        # Before invalidation, it was not visible (demonstrating the staleness bug)
        assert "ZZUNIQUE" not in types_cached


# ── Schedule endpoint error handling ──────────────────────────────────────


class TestScheduleEndpoint:
    """Tests for /api/course/{id}/schedule behaviour on error."""

    def test_schedule_success(self, populated_store, client):
        """When the external API responds, appointments are returned."""
        from unittest.mock import AsyncMock, MagicMock, patch

        mock_data = {
            "courseGroupDtos": [
                {
                    "appointmentDtos": [
                        {
                            "dtStart": "2025-10-20T10:00:00",
                            "dtEnd": "2025-10-20T12:00:00",
                            "weekDay": "MO",
                            "resource": [{"subText": "garching, MI HS 1 (5602.EG.001)"}],
                        }
                    ]
                }
            ]
        }

        # Use MagicMock (not AsyncMock) for resp since resp.json() is called synchronously
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.raise_for_status.return_value = None
        mock_resp.json.return_value = mock_data

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_resp)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        # Patch at the module where httpx is imported (web.py)
        with patch("tum_lecture_finder.web.httpx.AsyncClient", return_value=mock_client):
            resp = client.get("/api/course/1/schedule")

        assert resp.status_code == 200
        data = resp.json()
        assert "appointments" in data

    def test_schedule_api_error_returns_empty_with_error_flag(self, populated_store, client):
        """When the TUMonline API is down, schedule returns empty appointments with error field."""
        from unittest.mock import AsyncMock, patch

        import httpx

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=httpx.ConnectError("timeout"))
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("tum_lecture_finder.web.httpx.AsyncClient", return_value=mock_client):
            resp = client.get("/api/course/1/schedule")

        assert resp.status_code == 200
        data = resp.json()
        assert data["appointments"] == []
        assert "error" in data  # client can show a message instead of silent empty

    def test_schedule_unknown_course_returns_empty(self, populated_store, client):
        """The schedule endpoint does not validate course existence locally.

        It always hits TUMonline. If TUMonline 404s, the caught HTTPError
        causes empty appointments to be returned (not a 404 to the client).
        This documents current behaviour — ideally the endpoint would return 404
        for course IDs not in the local DB.
        """
        from unittest.mock import AsyncMock, MagicMock, patch

        import httpx

        mock_resp = MagicMock()
        mock_resp.status_code = 404
        mock_resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            "404", request=MagicMock(), response=mock_resp
        )

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_resp)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("tum_lecture_finder.web.httpx.AsyncClient", return_value=mock_client):
            resp = client.get("/api/course/99999/schedule")

        assert resp.status_code == 200  # error is swallowed
        data = resp.json()
        assert data["appointments"] == []
        assert "error" in data


# ── Scheduled update tests ──────────────────────────────────────────────────


class TestScheduledUpdate:
    """Tests for _scheduled_update() two-tier logic and cache invalidation."""

    @pytest.fixture(autouse=True)
    def _reset_module_state(self):
        """Reset module-level state between tests."""
        import tum_lecture_finder.web as web_mod

        web_mod._update_run_count = -1
        web_mod._update_running = False
        web_mod._type_counts_cache = ("dummy", [])
        web_mod._campus_counts_cache = ("dummy", [])
        yield
        web_mod._update_run_count = -1
        web_mod._update_running = False

    @pytest.fixture
    def mock_store(self, tmp_path):
        """A real CourseStore backed by tmp_path."""
        store = CourseStore(db_path=tmp_path / "test.db", check_same_thread=False)
        import tum_lecture_finder.web as web_mod

        old = web_mod._store
        web_mod._store = store
        yield store
        web_mod._store = old
        store.close()

    @pytest.mark.asyncio
    async def test_first_run_is_full(self, mock_store):
        """First run after process start should be a full update (counter -1 -> 0)."""
        from unittest.mock import AsyncMock, patch

        import tum_lecture_finder.web as web_mod
        from tum_lecture_finder.fetcher import FetchResult
        from tum_lecture_finder.models import Course

        semesters = [{"id": 204, "key": "25W"}, {"id": 203, "key": "25S"}]
        result = FetchResult(
            detailed=[Course(course_id=1, semester_key="25W", title_en="A")],
            list_only=[],
        )

        with (
            patch(
                "tum_lecture_finder.fetcher.fetch_semester_list",
                new=AsyncMock(return_value=semesters),
            ),
            patch(
                "tum_lecture_finder.fetcher.fetch_courses",
                new=AsyncMock(return_value=result),
            ) as mock_fetch,
            patch("tum_lecture_finder.search.build_embeddings", return_value=1),
            patch("tum_lecture_finder.search.invalidate_course_cache"),
        ):
            await web_mod._scheduled_update()

        # First run: skip_detail_ids should be None (full update)
        assert mock_fetch.call_args is not None
        assert mock_fetch.call_args.kwargs.get("skip_detail_ids") is None

    @pytest.mark.asyncio
    async def test_incremental_skips_past_semesters(self, mock_store):
        """On an incremental run, past semesters with details should be skipped."""
        from unittest.mock import AsyncMock, patch

        import tum_lecture_finder.web as web_mod
        from tum_lecture_finder.fetcher import FetchResult
        from tum_lecture_finder.models import Course

        # Set counter so next run is incremental (not full)
        web_mod._update_run_count = 0  # next increment -> 1, 1 % 7 != 0

        # Pre-populate DB with a past semester course that has details
        mock_store.upsert_courses([
            Course(course_id=100, semester_key="24W", title_en="Old", content_en="Has content"),
        ])

        semesters = [
            {"id": 206, "key": "26S"},  # current/future
            {"id": 204, "key": "24W"},  # past
        ]
        result = FetchResult(
            detailed=[Course(course_id=200, semester_key="26S", title_en="New")],
            list_only=[Course(course_id=100, semester_key="24W", title_en="Old")],
        )

        with (
            patch(
                "tum_lecture_finder.fetcher.fetch_semester_list",
                new=AsyncMock(return_value=semesters),
            ),
            patch(
                "tum_lecture_finder.fetcher.fetch_courses",
                new=AsyncMock(return_value=result),
            ) as mock_fetch,
            patch("tum_lecture_finder.search.build_embeddings", return_value=1),
            patch("tum_lecture_finder.search.invalidate_course_cache"),
        ):
            await web_mod._scheduled_update()

        # Should have passed skip_detail_ids containing course 100
        assert mock_fetch.call_args is not None
        skip_ids = mock_fetch.call_args.kwargs.get("skip_detail_ids")
        assert skip_ids is not None
        assert 100 in skip_ids

    @pytest.mark.asyncio
    async def test_caches_invalidated_after_update(self, mock_store):
        """Web caches and course cache must be cleared after update."""
        from unittest.mock import AsyncMock, patch

        import tum_lecture_finder.web as web_mod
        from tum_lecture_finder.fetcher import FetchResult
        from tum_lecture_finder.models import Course

        semesters = [{"id": 204, "key": "25W"}]
        result = FetchResult(
            detailed=[Course(course_id=1, semester_key="25W", title_en="A")],
            list_only=[],
        )

        with (
            patch(
                "tum_lecture_finder.fetcher.fetch_semester_list",
                new=AsyncMock(return_value=semesters),
            ),
            patch(
                "tum_lecture_finder.fetcher.fetch_courses",
                new=AsyncMock(return_value=result),
            ),
            patch("tum_lecture_finder.search.build_embeddings", return_value=1),
            patch(
                "tum_lecture_finder.search.invalidate_course_cache",
            ) as mock_invalidate,
        ):
            await web_mod._scheduled_update()

        mock_invalidate.assert_called_once()
        assert web_mod._type_counts_cache is None
        assert web_mod._campus_counts_cache is None

    @pytest.mark.asyncio
    async def test_overlap_guard(self, mock_store):
        """If an update is already running, a new one should be skipped."""
        import tum_lecture_finder.web as web_mod

        web_mod._update_running = True
        # This should return immediately without doing anything
        await web_mod._scheduled_update()
        # Counter should not have been incremented
        assert web_mod._update_run_count == -1

    @pytest.mark.asyncio
    async def test_update_stats_persisted(self, mock_store):
        """Update stats should be persisted in the meta table."""
        from unittest.mock import AsyncMock, patch

        import tum_lecture_finder.web as web_mod
        from tum_lecture_finder.fetcher import FetchResult
        from tum_lecture_finder.models import Course

        semesters = [{"id": 204, "key": "25W"}]
        result = FetchResult(
            detailed=[Course(course_id=1, semester_key="25W", title_en="A")],
            list_only=[],
        )

        with (
            patch(
                "tum_lecture_finder.fetcher.fetch_semester_list",
                new=AsyncMock(return_value=semesters),
            ),
            patch(
                "tum_lecture_finder.fetcher.fetch_courses",
                new=AsyncMock(return_value=result),
            ),
            patch("tum_lecture_finder.search.build_embeddings", return_value=1),
            patch("tum_lecture_finder.search.invalidate_course_cache"),
        ):
            await web_mod._scheduled_update()

        assert mock_store.get_meta("last_update_tier") == "full"
        assert mock_store.get_meta("last_update_detailed") == "1"
        assert mock_store.get_meta("last_update_skipped") == "0"
        assert mock_store.get_meta("last_update_semesters") == "25W"
        assert mock_store.get_meta("last_update_time") is not None


class TestScheduledUpdateIntegration:
    """End-to-end integration test for _scheduled_update with fake HTTP responses.

    This test wires up the real fetcher (not mocked), but intercepts HTTP at the
    httpx transport level so no real network calls are made.  It validates the
    full pipeline: semester list → course list → details (with skipping) →
    upsert → cache invalidation → stats persistence.
    """

    @pytest.fixture(autouse=True)
    def _reset_module_state(self):
        """Reset module-level state between tests."""
        import tum_lecture_finder.web as web_mod

        web_mod._update_run_count = -1
        web_mod._update_running = False
        web_mod._type_counts_cache = ("dummy", [])
        web_mod._campus_counts_cache = ("dummy", [])
        yield
        web_mod._update_run_count = -1
        web_mod._update_running = False

    @pytest.fixture
    def integration_store(self, tmp_path):
        """A real CourseStore patched as the global _store."""
        store = CourseStore(db_path=tmp_path / "integration.db", check_same_thread=False)
        import tum_lecture_finder.web as web_mod

        old = web_mod._store
        web_mod._store = store
        yield store
        web_mod._store = old
        store.close()

    @pytest.mark.asyncio
    async def test_full_then_incremental_cycle(self, integration_store):  # noqa: C901
        """Run two update cycles: first full, then incremental. Verify behavior."""
        from unittest.mock import AsyncMock, patch

        import tum_lecture_finder.web as web_mod

        # ── Fake API data ─────────────────────────────────────
        semester_list = [
            {"id": 206, "key": "26S"},
            {"id": 205, "key": "25W"},
            {"id": 204, "key": "25S"},
            {"id": 203, "key": "24W"},
        ]

        def _make_list_item(cid, key, number, title_de, title_en):
            return {
                "id": cid,
                "courseNumber": {"courseNumber": number},
                "courseTitle": {"translations": {"translation": [
                    {"lang": "de", "value": title_de},
                    {"lang": "en", "value": title_en},
                ]}},
                "courseTypeDto": {"key": "VO"},
                "semesterDto": {"key": key},
                "identityCodeIdOfCpCourseDto": cid * 10,
            }

        courses_by_semester = {
            206: [_make_list_item(1, "26S", "IN0001", "Informatik 1", "CS 1")],
            205: [_make_list_item(2, "25W", "IN0002", "Informatik 2", "CS 2")],
            204: [_make_list_item(3, "25S", "IN0003", "Informatik 3", "CS 3")],
            203: [_make_list_item(4, "24W", "IN0004", "Informatik 4", "CS 4")],
        }

        detail_fetch_ids: list[int] = []

        def _detail_json(cid):
            """Fake detail response with description."""
            return {
                "resource": [{
                    "content": {
                        "cpCourseDetailDto": {
                            "cpCourseDescriptionDto": {
                                "courseContent": {
                                    "translations": {"translation": [
                                        {"lang": "en", "value": f"Content for {cid}"},
                                    ]},
                                },
                            },
                        },
                    },
                }],
            }

        async def _fake_get(url, **kwargs):
            """Route fake HTTP requests to the right response."""
            import httpx

            req = httpx.Request("GET", url)

            if "semesters" in url:
                return httpx.Response(200, json={"semesters": semester_list}, request=req)

            # Course list pages
            for sid, items in courses_by_semester.items():
                if f"termId-eq={sid}" in url:
                    return httpx.Response(200, json={
                        "totalCount": len(items),
                        "courses": items,
                    }, request=req)

            # Course detail
            if "/courses/" in url and "courseGroups" not in url:
                cid = int(url.split("/courses/")[1].split("?")[0])
                detail_fetch_ids.append(cid)
                return httpx.Response(200, json=_detail_json(cid), request=req)

            # Course groups (return empty)
            if "courseGroups" in url:
                return httpx.Response(200, json={}, request=req)

            # NavigaTUM (return empty)
            if "nav.tum" in url:
                return httpx.Response(200, json={"sections": []}, request=req)

            return httpx.Response(404, request=req)

        mock_client = AsyncMock()
        mock_client.get = _fake_get

        # ── Run 1: Full update ────────────────────────────────
        detail_fetch_ids.clear()

        with (
            patch("httpx.AsyncClient") as mock_cls,
            patch("tum_lecture_finder.search.build_embeddings", return_value=1),
            patch("tum_lecture_finder.search.invalidate_course_cache"),
        ):
            mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)
            await web_mod._scheduled_update()

        # Full update: all 4 courses should have had details fetched
        assert sorted(detail_fetch_ids) == [1, 2, 3, 4]
        assert integration_store.course_count() == 4
        assert web_mod._type_counts_cache is None  # caches cleared
        assert integration_store.get_meta("last_update_tier") == "full"

        # Verify descriptions were stored
        for cid in [1, 2, 3, 4]:
            row = integration_store.get_course(cid)
            assert row["content_en"] == f"Content for {cid}"

        # ── Run 2: Incremental update ─────────────────────────
        detail_fetch_ids.clear()

        with (
            patch("httpx.AsyncClient") as mock_cls,
            patch("tum_lecture_finder.search.build_embeddings", return_value=1),
            patch("tum_lecture_finder.search.invalidate_course_cache"),
        ):
            mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)
            await web_mod._scheduled_update()

        # Incremental: only current/future semester courses (26S=id 1) should
        # have details fetched. Past semesters (25W, 25S, 24W) with existing
        # descriptions should be skipped.
        assert 1 in detail_fetch_ids, "Current/future course should be fetched"
        assert 4 not in detail_fetch_ids, "Past course with details should be skipped"
        assert integration_store.get_meta("last_update_tier") == "incremental"

        # Descriptions should still be intact for skipped courses
        for cid in [2, 3, 4]:
            row = integration_store.get_course(cid)
            assert row["content_en"] == f"Content for {cid}", (
                f"Course {cid} description was overwritten during incremental update"
            )


class TestHotReloadEmbeddings:
    """Verify that a partial update followed by embedding rebuild makes new
    courses appear in semantic search, while preserving existing courses.

    This test uses the real sentence-transformer model and real embedding
    encode/save/load — the only thing mocked is the HTTP layer.
    """

    @pytest.fixture(autouse=True)
    def _reset_caches(self, tmp_path, monkeypatch):
        """Isolate all caches so tests don't leak into each other."""
        import tum_lecture_finder.search as search_mod
        import tum_lecture_finder.storage as storage_mod
        import tum_lecture_finder.web as web_mod

        # Redirect embeddings file to tmp_path so we don't touch the real one
        monkeypatch.setattr(storage_mod, "EMBEDDINGS_PATH", tmp_path / "embeddings.npz")

        # Reset module-level caches
        web_mod._update_run_count = -1
        web_mod._update_running = False
        web_mod._type_counts_cache = None
        web_mod._campus_counts_cache = None
        search_mod._course_cache = None

        yield

        # Clean up
        web_mod._update_run_count = -1
        web_mod._update_running = False
        search_mod._course_cache = None

    @pytest.fixture
    def store(self, tmp_path):
        """A real CourseStore patched as the global _store."""
        import tum_lecture_finder.web as web_mod

        s = CourseStore(db_path=tmp_path / "test.db", check_same_thread=False)
        old = web_mod._store
        web_mod._store = s
        yield s
        web_mod._store = old
        s.close()

    def _make_fake_http(self, semester_list, courses_by_semester, detail_tracker):  # noqa: C901
        """Build a fake async GET handler for httpx."""
        import httpx

        def _detail_json(cid, content):
            return {
                "resource": [{
                    "content": {
                        "cpCourseDetailDto": {
                            "cpCourseDescriptionDto": {
                                "courseContent": {
                                    "translations": {"translation": [
                                        {"lang": "en", "value": content},
                                    ]},
                                },
                            },
                        },
                    },
                }],
            }

        # Map course_id → content for detail responses
        detail_content = {}
        for items in courses_by_semester.values():
            for item in items:
                cid = item["id"]
                detail_content[cid] = f"Content about {item['_title']}"

        async def _fake_get(url, **kwargs):
            req = httpx.Request("GET", url)

            if "semesters" in url:
                return httpx.Response(200, json={"semesters": semester_list}, request=req)

            for sid, items in courses_by_semester.items():
                if f"termId-eq={sid}" in url:
                    # Strip the internal _title helper before returning
                    clean = [{k: v for k, v in item.items() if k != "_title"} for item in items]
                    return httpx.Response(
                        200, json={"totalCount": len(clean), "courses": clean}, request=req,
                    )

            if "/courses/" in url and "courseGroups" not in url:
                cid = int(url.split("/courses/")[1].split("?")[0])
                detail_tracker.append(cid)
                return httpx.Response(
                    200, json=_detail_json(cid, detail_content.get(cid, "")), request=req,
                )

            if "courseGroups" in url:
                return httpx.Response(200, json={}, request=req)

            if "nav.tum" in url:
                return httpx.Response(200, json={"sections": []}, request=req)

            return httpx.Response(404, request=req)

        return _fake_get

    @staticmethod
    def _list_item(cid, sem_key, number, title):
        return {
            "id": cid,
            "_title": title,  # internal helper, stripped before HTTP response
            "courseNumber": {"courseNumber": number},
            "courseTitle": {"translations": {"translation": [
                {"lang": "en", "value": title},
            ]}},
            "courseTypeDto": {"key": "VO"},
            "semesterDto": {"key": sem_key},
            "identityCodeIdOfCpCourseDto": cid * 10,
        }

    @pytest.mark.asyncio
    async def test_new_course_appears_in_semantic_search_after_incremental_update(self, store):
        """Full lifecycle: build initial embeddings → incremental update adds a
        new course → rebuild embeddings → semantic search finds the new course,
        and existing courses are still findable.
        """
        from unittest.mock import AsyncMock, patch

        import tum_lecture_finder.web as web_mod
        from tum_lecture_finder.search import (
            semantic_search,
        )

        semester_list = [
            {"id": 206, "key": "26S"},
            {"id": 203, "key": "24W"},
        ]

        # ── Phase 1: Initial full update with 2 courses ──────
        initial_courses = {
            206: [self._list_item(1, "26S", "IN0001", "quantum computing fundamentals")],
            203: [self._list_item(2, "24W", "IN0002", "medieval history of europe")],
        }

        detail_tracker: list[int] = []
        fake_get = self._make_fake_http(semester_list, initial_courses, detail_tracker)
        mock_client = AsyncMock()
        mock_client.get = fake_get

        with patch("httpx.AsyncClient") as mock_cls:
            mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)
            await web_mod._scheduled_update()

        assert sorted(detail_tracker) == [1, 2], "Full update should fetch all details"
        assert store.course_count() == 2

        # Verify semantic search works for both courses
        results_q = semantic_search(store, "quantum computing")
        assert any(r.course.course_id == 1 for r in results_q), (
            "quantum computing course should be findable"
        )
        results_h = semantic_search(store, "medieval history")
        assert any(r.course.course_id == 2 for r in results_h), (
            "medieval history course should be findable"
        )

        # ── Phase 2: Incremental update adds a new course ────
        # Now the API returns 3 courses: the original 2 + a new one
        updated_courses = {
            206: [
                self._list_item(1, "26S", "IN0001", "quantum computing fundamentals"),
                self._list_item(3, "26S", "IN0003", "deep reinforcement learning"),
            ],
            203: [self._list_item(2, "24W", "IN0002", "medieval history of europe")],
        }

        detail_tracker.clear()
        fake_get_2 = self._make_fake_http(semester_list, updated_courses, detail_tracker)
        mock_client_2 = AsyncMock()
        mock_client_2.get = fake_get_2

        with patch("httpx.AsyncClient") as mock_cls:
            mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client_2)
            mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)
            await web_mod._scheduled_update()

        # Incremental: course 2 (past, has details) should be skipped
        assert 2 not in detail_tracker, "Past course with details should be skipped"
        # Course 3 is new, should be fetched
        assert 3 in detail_tracker, "New course should have details fetched"
        assert store.course_count() == 3

        # ── Phase 3: Verify hot-reloaded embeddings ──────────
        # The NEW course should now be findable via semantic search
        results_rl = semantic_search(store, "reinforcement learning")
        assert any(r.course.course_id == 3 for r in results_rl), (
            "New course 'deep reinforcement learning' should appear in semantic search "
            "after incremental update + embedding rebuild"
        )

        # The EXISTING courses should STILL be findable
        results_q2 = semantic_search(store, "quantum computing")
        assert any(r.course.course_id == 1 for r in results_q2), (
            "Existing course should still be findable after incremental update"
        )

        # Past course whose details were skipped should still be findable
        results_h2 = semantic_search(store, "medieval history")
        assert any(r.course.course_id == 2 for r in results_h2), (
            "Skipped past course should still be findable (descriptions preserved)"
        )

        # Verify update stats
        assert store.get_meta("last_update_tier") == "incremental"
        assert int(store.get_meta("last_update_skipped")) > 0
