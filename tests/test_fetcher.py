"""Tests for fetcher module (unit tests + mocked HTTP tests)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import click
import httpx
import pytest

from tum_lecture_finder.fetcher import (
    _extract_building_codes,
    _get_with_retry,
    _lang_value,
    _merge_detail,
    _parse_campus_from_subtext,
    _parse_course_list_item,
    fetch_semester_list,
    resolve_semester_ids,
)
from tum_lecture_finder.models import Course

# ── _lang_value ────────────────────────────────────────────────────────────


class TestLangValue:
    def test_extract_de(self):
        obj = {
            "translations": {
                "translation": [
                    {"lang": "de", "value": "Hallo"},
                    {"lang": "en", "value": "Hello"},
                ],
            },
            "value": "default",
        }
        assert _lang_value(obj, "de") == "Hallo"

    def test_extract_en(self):
        obj = {
            "translations": {
                "translation": [
                    {"lang": "de", "value": "Hallo"},
                    {"lang": "en", "value": "Hello"},
                ],
            },
        }
        assert _lang_value(obj, "en") == "Hello"

    def test_fallback_to_value(self):
        obj = {"value": "fallback", "translations": {"translation": []}}
        assert _lang_value(obj, "de") == "fallback"

    def test_none_returns_empty(self):
        assert _lang_value(None) == ""

    def test_empty_dict_returns_empty(self):
        assert _lang_value({}) == ""

    def test_missing_translations_key(self):
        obj = {"value": "val"}
        assert _lang_value(obj, "de") == "val"

    def test_empty_translation_value(self):
        obj = {
            "translations": {
                "translation": [{"lang": "de", "value": ""}],
            },
            "value": "fallback",
        }
        # Empty string value → should fallback
        assert _lang_value(obj, "de") == "fallback"

    def test_default_lang_is_de(self):
        obj = {
            "translations": {
                "translation": [
                    {"lang": "de", "value": "Deutsch"},
                    {"lang": "en", "value": "English"},
                ],
            },
        }
        assert _lang_value(obj) == "Deutsch"


# ── _parse_course_list_item ────────────────────────────────────────────────


class TestParseCourseListItem:
    def _minimal_item(self, **overrides):
        item = {
            "id": 123,
            "semesterDto": {"key": "25W"},
            "courseTitle": {
                "translations": {
                    "translation": [
                        {"lang": "de", "value": "Kurs"},
                        {"lang": "en", "value": "Course"},
                    ],
                },
            },
        }
        item.update(overrides)
        return item

    def test_basic_fields(self):
        course = _parse_course_list_item(self._minimal_item())
        assert course.course_id == 123
        assert course.semester_key == "25W"
        assert course.title_de == "Kurs"
        assert course.title_en == "Course"

    def test_extracts_semester_from_dto(self):
        item = self._minimal_item(semesterDto={"key": "25S"})
        assert _parse_course_list_item(item).semester_key == "25S"

    def test_extracts_instructors(self):
        item = self._minimal_item(
            lectureships=[
                {
                    "identityLibDto": {
                        "firstName": "John",
                        "lastName": "Doe",
                    },
                },
                {
                    "identityLibDto": {
                        "firstName": "Jane",
                        "lastName": "Smith",
                    },
                },
            ],
        )
        course = _parse_course_list_item(item)
        assert "John Doe" in course.instructors
        assert "Jane Smith" in course.instructors

    def test_extracts_course_type(self):
        item = self._minimal_item(courseTypeDto={"key": "VO"})
        assert _parse_course_list_item(item).course_type == "VO"

    def test_extracts_sws(self):
        item = self._minimal_item(
            courseNormConfigs=[{"key": "SST", "value": "4"}],
        )
        assert _parse_course_list_item(item).sws == "4"

    def test_extracts_organisation(self):
        item = self._minimal_item(
            organisationResponsibleDto={
                "name": {
                    "translations": {
                        "translation": [{"lang": "de", "value": "Informatik"}],
                    },
                },
            },
        )
        assert _parse_course_list_item(item).organisation == "Informatik"

    def test_extracts_language(self):
        item = self._minimal_item(
            courseLanguageDtos=[
                {"languageDto": {"key": "DE"}},
                {"languageDto": {"key": "EN"}},
            ],
        )
        course = _parse_course_list_item(item)
        assert "DE" in course.language
        assert "EN" in course.language

    def test_extracts_identity_code_id(self):
        item = self._minimal_item(identityCodeId=42)
        assert _parse_course_list_item(item).identity_code_id == 42

    def test_extracts_course_number(self):
        item = self._minimal_item(courseNumber={"courseNumber": "IN2064"})
        assert _parse_course_list_item(item).course_number == "IN2064"

    def test_missing_optional_fields(self):
        """Minimal item with missing optional fields should not crash."""
        item = {"id": 1, "courseTitle": {"value": "Test"}}
        course = _parse_course_list_item(item)
        assert course.course_id == 1
        assert course.semester_key == ""
        assert course.instructors == ""
        assert course.sws == ""

    def test_null_identity_code_id(self):
        item = self._minimal_item(identityCodeId=None)
        assert _parse_course_list_item(item).identity_code_id == 0

    def test_sws_ignores_non_sst_configs(self):
        item = self._minimal_item(
            courseNormConfigs=[
                {"key": "OTHER", "value": "99"},
                {"key": "SST", "value": "3"},
            ],
        )
        assert _parse_course_list_item(item).sws == "3"


# ── _merge_detail ──────────────────────────────────────────────────────────


class TestMergeDetail:
    def _make_detail(self, **desc_fields):
        desc = {}
        for key, value in desc_fields.items():
            desc[key] = {
                "translations": {
                    "translation": [
                        {"lang": "de", "value": f"{value}_de"},
                        {"lang": "en", "value": f"{value}_en"},
                    ],
                },
            }
        return {
            "resource": [
                {
                    "content": {
                        "cpCourseDetailDto": {
                            "cpCourseDescriptionDto": desc,
                        },
                    },
                },
            ],
        }

    def test_merges_content(self):
        course = Course(course_id=1, semester_key="25W")
        detail = self._make_detail(courseContent="Content")
        _merge_detail(course, detail)
        assert course.content_de == "Content_de"
        assert course.content_en == "Content_en"

    def test_merges_objectives(self):
        course = Course(course_id=1, semester_key="25W")
        detail = self._make_detail(courseObjective="Objectives")
        _merge_detail(course, detail)
        assert course.objectives_de == "Objectives_de"
        assert course.objectives_en == "Objectives_en"

    def test_merges_prerequisites(self):
        course = Course(course_id=1, semester_key="25W")
        detail = self._make_detail(previousKnowledge="Prereqs")
        _merge_detail(course, detail)
        assert course.prerequisites == "Prereqs_de"

    def test_merges_literature(self):
        course = Course(course_id=1, semester_key="25W")
        detail = {
            "resource": [
                {
                    "content": {
                        "cpCourseDetailDto": {
                            "cpCourseDescriptionDto": {
                                "additionalInformation": {
                                    "recommendedLiterature": {
                                        "translations": {
                                            "translation": [
                                                {"lang": "de", "value": "Books"},
                                            ],
                                        },
                                    },
                                },
                            },
                        },
                    },
                },
            ],
        }
        _merge_detail(course, detail)
        assert course.literature == "Books"

    def test_empty_resource_list(self):
        course = Course(course_id=1, semester_key="25W")
        _merge_detail(course, {"resource": []})
        assert course.content_de == ""

    def test_missing_resource_key(self):
        course = Course(course_id=1, semester_key="25W")
        _merge_detail(course, {})
        assert course.content_de == ""

    def test_fallback_org_from_detail(self):
        course = Course(course_id=1, semester_key="25W", organisation="")
        detail = {
            "resource": [
                {
                    "content": {
                        "cpCourseDetailDto": {
                            "cpCourseDto": {
                                "organisationResponsibleDto": {
                                    "name": {
                                        "translations": {
                                            "translation": [
                                                {"lang": "de", "value": "Fakultät"},
                                            ],
                                        },
                                    },
                                },
                            },
                            "cpCourseDescriptionDto": {},
                        },
                    },
                },
            ],
        }
        _merge_detail(course, detail)
        assert course.organisation == "Fakultät"

    def test_preserves_existing_org(self):
        course = Course(course_id=1, semester_key="25W", organisation="Existing")
        detail = {
            "resource": [
                {
                    "content": {
                        "cpCourseDetailDto": {
                            "cpCourseDto": {
                                "organisationResponsibleDto": {
                                    "name": {
                                        "translations": {
                                            "translation": [
                                                {"lang": "de", "value": "Other"},
                                            ],
                                        },
                                    },
                                },
                            },
                            "cpCourseDescriptionDto": {},
                        },
                    },
                },
            ],
        }
        _merge_detail(course, detail)
        assert course.organisation == "Existing"


# ── _extract_building_codes ────────────────────────────────────────────────


class TestExtractBuildingCodes:
    def test_single_building(self):
        data = {
            "courseGroupDtos": [
                {
                    "appointmentDtos": [
                        {"resourceName": "MI HS 1 (5602.EG.001)"},
                    ],
                },
            ],
        }
        assert _extract_building_codes(data) == ["5602"]

    def test_multiple_buildings_deduped_sorted(self):
        data = {
            "courseGroupDtos": [
                {
                    "appointmentDtos": [
                        {"resourceName": "MI HS 1 (5602.EG.001)"},
                        {"resourceName": "Hörsaal (0503.02.370)"},
                        {"resourceName": "MI HS 2 (5602.EG.002)"},
                    ],
                },
            ],
        }
        assert _extract_building_codes(data) == ["0503", "5602"]

    def test_empty_groups(self):
        assert _extract_building_codes({"courseGroupDtos": []}) == []

    def test_empty_appointments(self):
        assert _extract_building_codes({"courseGroupDtos": [{"appointmentDtos": []}]}) == []

    def test_online_only(self):
        data = {
            "courseGroupDtos": [
                {
                    "appointmentDtos": [
                        {"resourceName": "Online: Videokonferenz"},
                    ],
                },
            ],
        }
        assert _extract_building_codes(data) == []

    def test_no_resource_name(self):
        data = {
            "courseGroupDtos": [
                {
                    "appointmentDtos": [{}],
                },
            ],
        }
        assert _extract_building_codes(data) == []

    def test_multiple_groups(self):
        data = {
            "courseGroupDtos": [
                {
                    "appointmentDtos": [
                        {"resourceName": "Room A (1234.01.001)"},
                    ],
                },
                {
                    "appointmentDtos": [
                        {"resourceName": "Room B (5678.02.002)"},
                    ],
                },
            ],
        }
        assert _extract_building_codes(data) == ["1234", "5678"]


# ── _parse_campus_from_subtext ─────────────────────────────────────────────


class TestParseCampusFromSubtext:
    def test_garching(self):
        assert _parse_campus_from_subtext("garching, Mathe/Info (MI)") == "garching"

    def test_stammgelaende(self):
        assert _parse_campus_from_subtext("stammgelände, U-Trakt (N1)") == "stammgelände"

    def test_heilbronn(self):
        assert (
            _parse_campus_from_subtext(
                "campus-heilbronn, Bildungscampus D, Gebäude 2, Vorlesungssäle",
            )
            == "campus-heilbronn"
        )

    def test_straubing(self):
        assert (
            _parse_campus_from_subtext(
                "campus-straubing-cs-biotechnologie-und-nachhaltigkeit, Schulgasse 16 (SG 16)",
            )
            == "campus-straubing-cs-biotechnologie-und-nachhaltigkeit"
        )

    def test_garching_hochbrueck(self):
        assert (
            _parse_campus_from_subtext("garching-hochbrück, Business Campus 1")
            == "garching-hochbrück"
        )

    def test_garmisch_uppercase_fallback(self):
        """Non-standard locations with uppercase first letter use fallback parsing."""
        assert (
            _parse_campus_from_subtext("Garmisch-Partenkirchen (A, Bahnhofstr. 37)")
            == "garmisch-partenkirchen"
        )

    def test_empty_string(self):
        assert _parse_campus_from_subtext("") == ""

    def test_no_comma_format(self):
        """Subtext without comma uses fallback."""
        result = _parse_campus_from_subtext("someplace")
        assert result == "someplace"

    def test_uppercase_first_no_paren(self):
        result = _parse_campus_from_subtext("München, Hauptgebäude")
        # 'München' starts uppercase → fallback lowercases entire string
        assert result == "münchen, hauptgebäude"


# ── resolve_semester_ids ───────────────────────────────────────────────────


class TestResolveSemesterIds:
    def test_resolves_valid_keys(self):
        semesters = [
            {"id": 204, "key": "25S"},
            {"id": 203, "key": "25W"},
            {"id": 202, "key": "24W"},
        ]
        ids = resolve_semester_ids(semesters, ["25S", "25W"])
        assert ids == [204, 203]

    def test_case_insensitive(self):
        semesters = [{"id": 204, "key": "25S"}]
        ids = resolve_semester_ids(semesters, ["25s"])
        assert ids == [204]

    def test_invalid_key_raises(self):
        semesters = [{"id": 204, "key": "25S"}]
        with pytest.raises(click.BadParameter):
            resolve_semester_ids(semesters, ["99Z"])

    def test_future_semester_not_in_list_raises(self):
        """A semester key the API hasn't published yet raises BadParameter."""
        semesters = [{"id": 204, "key": "25W"}, {"id": 203, "key": "25S"}]
        with pytest.raises(click.BadParameter, match="Unknown semester"):
            resolve_semester_ids(semesters, ["29W"])


# ── HTTP layer tests ────────────────────────────────────────────────────────
# These tests mock httpx so no real network calls are made.

def _mock_response(json_data: dict, status_code: int = 200) -> MagicMock:
    """Build a mock httpx Response that behaves like the real thing."""
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = status_code
    resp.json.return_value = json_data
    if status_code >= 400:
        resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            f"HTTP {status_code}",
            request=MagicMock(),
            response=resp,
        )
    else:
        resp.raise_for_status.return_value = None
    return resp


class TestGetWithRetry:
    """_get_with_retry retries on transient errors and succeeds or raises."""

    @pytest.mark.anyio
    async def test_succeeds_on_first_attempt(self):
        """Happy path: first request succeeds, no retry."""
        client = AsyncMock()
        client.get = AsyncMock(return_value=_mock_response({"ok": True}))
        resp = await _get_with_retry(client, "http://example.com/test")
        assert resp.json() == {"ok": True}
        assert client.get.call_count == 1

    @pytest.mark.anyio
    async def test_retries_on_429(self):
        """429 response triggers a retry; second attempt succeeds."""
        rate_limit = _mock_response({}, 429)
        success = _mock_response({"ok": True})
        client = AsyncMock()
        client.get = AsyncMock(side_effect=[rate_limit, success])

        with patch("asyncio.sleep", new=AsyncMock()):
            resp = await _get_with_retry(client, "http://example.com/test")

        assert client.get.call_count == 2
        assert resp.json() == {"ok": True}

    @pytest.mark.anyio
    async def test_retries_on_503(self):
        """503 response also triggers a retry."""
        unavailable = _mock_response({}, 503)
        success = _mock_response({"ok": True})
        client = AsyncMock()
        client.get = AsyncMock(side_effect=[unavailable, success])

        with patch("asyncio.sleep", new=AsyncMock()):
            await _get_with_retry(client, "http://example.com/test")

        assert client.get.call_count == 2

    @pytest.mark.anyio
    async def test_retries_on_connection_error(self):
        """Network error triggers retry; eventual success is returned."""
        client = AsyncMock()
        client.get = AsyncMock(
            side_effect=[
                httpx.ConnectError("timeout"),
                _mock_response({"ok": True}),
            ]
        )
        with patch("asyncio.sleep", new=AsyncMock()):
            resp = await _get_with_retry(client, "http://example.com/test")
        assert resp.json() == {"ok": True}

    @pytest.mark.anyio
    async def test_raises_after_all_retries_exhausted(self):
        """All retries fail → HTTPError is raised."""
        rate_limit = _mock_response({}, 429)
        client = AsyncMock()
        client.get = AsyncMock(return_value=rate_limit)

        with (
            patch("asyncio.sleep", new=AsyncMock()),
            pytest.raises(httpx.HTTPStatusError),
        ):
            await _get_with_retry(client, "http://example.com/test", retries=3)

        assert client.get.call_count == 3

    @pytest.mark.anyio
    async def test_all_http_errors_are_retried(self):
        """_get_with_retry retries all HTTP errors, including 400 (current behaviour).

        Note: this is a design issue — 400 Bad Request is not a transient error
        and ideally would not be retried. This test documents current behaviour.
        """
        client = AsyncMock()
        client.get = AsyncMock(return_value=_mock_response({}, 400))
        with (
            patch("asyncio.sleep", new=AsyncMock()),
            pytest.raises(httpx.HTTPStatusError),
        ):
            await _get_with_retry(client, "http://example.com/test", retries=3)
        # Currently retries all HTTP errors including 400
        assert client.get.call_count == 3


class TestFetchSemesterList:
    """fetch_semester_list() returns parsed semester dicts from the API."""

    @pytest.mark.anyio
    async def test_returns_semester_list(self):
        """Normal response yields the semesters list."""
        semesters_json = [
            {"id": 204, "key": "25W"},
            {"id": 203, "key": "25S"},
        ]
        mock_resp = _mock_response({"semesters": semesters_json})
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_resp)

        with patch("httpx.AsyncClient") as mock_cls:
            mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)
            result = await fetch_semester_list()

        assert result == semesters_json

    @pytest.mark.anyio
    async def test_bare_list_response_raises_attribute_error(self):
        """API returning a bare list (no 'semesters' key) causes AttributeError.

        This tests a known bug: _fetch_available_semesters calls data.get(...)
        which fails if the API returns a JSON array instead of an object.
        The TUMonline API always wraps in {"semesters": [...]}, so this is
        unlikely in practice, but the code should handle it gracefully.
        """
        mock_resp = MagicMock(spec=httpx.Response)
        mock_resp.status_code = 200
        mock_resp.raise_for_status.return_value = None
        mock_resp.json.return_value = [{"id": 204, "key": "25W"}]  # bare list

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_resp)

        with patch("httpx.AsyncClient") as mock_cls:
            mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)
            with pytest.raises(AttributeError):
                await fetch_semester_list()

    @pytest.mark.anyio
    async def test_empty_semesters(self):
        """Empty API response returns an empty list."""
        mock_resp = _mock_response({"semesters": []})
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_resp)

        with patch("httpx.AsyncClient") as mock_cls:
            mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)
            result = await fetch_semester_list()

        assert result == []


class TestFetchCourseListPagination:
    """fetch_courses() fetches all pages and calls progress callbacks."""

    def _minimal_course_item(self, course_id: int = 1) -> dict:
        return {
            "id": course_id,
            "semesterDto": {"key": "25W"},
            "courseTitle": {
                "translations": {
                    "translation": [
                        {"lang": "de", "value": "Kurs"},
                        {"lang": "en", "value": "Course"},
                    ]
                }
            },
        }

    @pytest.mark.anyio
    async def test_fetches_single_page(self):
        """Single page of results is parsed into Course objects."""
        from tum_lecture_finder.fetcher import fetch_courses

        courses_json = [self._minimal_course_item(i) for i in range(3)]
        list_resp = _mock_response({"totalCount": 3, "courses": courses_json})
        # Detail response: empty (no content)
        detail_resp = _mock_response({"resource": []})
        # Building resolution: NavigaTUM returns empty result
        nav_resp = _mock_response({"results": []})
        # Available semesters for auto-detection
        sem_resp = _mock_response({"semesters": [{"id": 204, "key": "25W"}]})

        call_count = 0

        async def _mock_get(url: str, **kwargs) -> MagicMock:
            nonlocal call_count
            call_count += 1
            if "semesters" in url:
                return sem_resp
            if "courseGroups" in url or "nav.tum" in url:
                return nav_resp
            if "$skip=0" in url or "courses?" in url:
                return list_resp
            return detail_resp

        mock_client = AsyncMock()
        mock_client.get = _mock_get

        with patch("httpx.AsyncClient") as mock_cls:
            mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)
            result = await fetch_courses(semester_ids=[204])

        assert len(result.detailed) == 3
        assert all(c.semester_key == "25W" for c in result.detailed)

    @pytest.mark.anyio
    async def test_progress_callbacks_called(self):
        """on_list_progress and on_detail_progress callbacks are invoked."""
        from tum_lecture_finder.fetcher import fetch_courses

        courses_json = [self._minimal_course_item(i) for i in range(2)]
        list_resp = _mock_response({"totalCount": 2, "courses": courses_json})
        detail_resp = _mock_response({"resource": []})
        nav_resp = _mock_response({"results": []})

        async def _mock_get(url: str, **kwargs) -> MagicMock:
            if "courseGroups" in url or "nav.tum" in url:
                return nav_resp
            return list_resp if "$skip" in url else detail_resp

        mock_client = AsyncMock()
        mock_client.get = _mock_get

        list_calls: list[tuple[int, int]] = []
        detail_calls: list[tuple[int, int]] = []

        with patch("httpx.AsyncClient") as mock_cls:
            mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)
            await fetch_courses(
                semester_ids=[204],
                on_list_progress=lambda f, t: list_calls.append((f, t)),
                on_detail_progress=lambda f, t: detail_calls.append((f, t)),
            )

        assert len(list_calls) > 0
        # Final call should have fetched == total
        assert list_calls[-1][0] == list_calls[-1][1]


# ── _fetch_details skip logic ──────────────────────────────────────────────


class TestFetchDetailsSkipIds:
    """Tests for the skip_ids parameter on _fetch_details."""

    @pytest.mark.asyncio
    async def test_skipped_courses_not_fetched(self):
        """Courses in skip_ids should not trigger any HTTP calls."""
        from tum_lecture_finder.fetcher import _fetch_details

        c1 = Course(course_id=1, semester_key="25W", title_en="A")
        c2 = Course(course_id=2, semester_key="25W", title_en="B")
        c3 = Course(course_id=3, semester_key="25W", title_en="C")

        detail_calls = []

        async def _mock_detail_raw(client, course_id):
            detail_calls.append(course_id)
            return {"resource": [{"content": {"cpCourseDetailDto": {}}}]}

        async def _mock_groups_raw(client, course_id):
            return {}

        with (
            patch(
                "tum_lecture_finder.fetcher._fetch_course_detail_raw",
                side_effect=_mock_detail_raw,
            ),
            patch(
                "tum_lecture_finder.fetcher._fetch_course_groups_raw",
                side_effect=_mock_groups_raw,
            ),
        ):
            client = MagicMock()
            buildings, detailed, skipped = await _fetch_details(
                client, [c1, c2, c3], concurrency=5, on_detail_progress=None,
                skip_ids={2, 3},
            )

        # Only course 1 should have been fetched
        assert detail_calls == [1]
        assert [c.course_id for c in detailed] == [1]
        assert sorted(c.course_id for c in skipped) == [2, 3]

    @pytest.mark.asyncio
    async def test_no_skip_ids_fetches_all(self):
        """Without skip_ids, all courses are fetched."""
        from tum_lecture_finder.fetcher import _fetch_details

        c1 = Course(course_id=1, semester_key="25W", title_en="A")
        c2 = Course(course_id=2, semester_key="25W", title_en="B")

        detail_calls = []

        async def _mock_detail_raw(client, course_id):
            detail_calls.append(course_id)

        async def _mock_groups_raw(client, course_id):
            return None

        with (
            patch(
                "tum_lecture_finder.fetcher._fetch_course_detail_raw",
                side_effect=_mock_detail_raw,
            ),
            patch(
                "tum_lecture_finder.fetcher._fetch_course_groups_raw",
                side_effect=_mock_groups_raw,
            ),
        ):
            client = MagicMock()
            buildings, detailed, skipped = await _fetch_details(
                client, [c1, c2], concurrency=5, on_detail_progress=None,
            )

        assert sorted(detail_calls) == [1, 2]
        assert len(detailed) == 2
        assert len(skipped) == 0


class TestFetchResult:
    """Tests for the FetchResult named tuple returned by fetch_courses."""

    @pytest.mark.asyncio
    async def test_fetch_courses_returns_fetch_result(self):
        """fetch_courses should return a FetchResult with detailed and list_only."""
        from tum_lecture_finder.fetcher import FetchResult, fetch_courses

        mock_client = AsyncMock()
        # Mock list response: one course
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "totalCount": 1,
            "courses": [{
                "id": 1,
                "courseNumber": {"courseNumber": "IN0001"},
                "courseTitle": {"translations": {"translation": [
                    {"lang": "de", "value": "Test DE"},
                    {"lang": "en", "value": "Test EN"},
                ]}},
                "courseTypeDto": {"key": "VO"},
                "semesterDto": {"key": "25W"},
                "identityCodeIdOfCpCourseDto": 100,
            }],
        }

        with patch("tum_lecture_finder.fetcher.httpx.AsyncClient") as mock_cls:
            mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)
            mock_client.get = AsyncMock(return_value=mock_resp)

            with (
                patch(
                    "tum_lecture_finder.fetcher._fetch_course_detail_raw",
                    new_callable=AsyncMock,
                    return_value=None,
                ),
                patch(
                    "tum_lecture_finder.fetcher._fetch_course_groups_raw",
                    new_callable=AsyncMock,
                    return_value=None,
                ),
                patch(
                    "tum_lecture_finder.fetcher._assign_campuses",
                    new_callable=AsyncMock,
                ),
            ):
                result = await fetch_courses(semester_ids=[204])

        assert isinstance(result, FetchResult)
        assert len(result.detailed) == 1
        assert len(result.list_only) == 0
