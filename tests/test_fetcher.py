"""Tests for fetcher module (unit tests, no HTTP)."""

import click
import pytest

from tum_lecture_finder.fetcher import (
    _extract_building_codes,
    _lang_value,
    _merge_detail,
    _parse_campus_from_subtext,
    _parse_course_list_item,
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
