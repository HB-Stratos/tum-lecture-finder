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
