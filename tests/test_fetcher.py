"""Tests for fetcher module (unit tests, no HTTP)."""

from tum_lecture_finder.fetcher import _campus_from_rooms, _lang_value, _parse_course_list_item


def test_lang_value_de():
    obj = {
        "translations": {
            "translation": [
                {"lang": "de", "value": "Hallo"},
                {"lang": "en", "value": "Hello"},
            ]
        },
        "value": "default",
    }
    assert _lang_value(obj, "de") == "Hallo"


def test_lang_value_missing():
    assert _lang_value(None) == ""
    assert _lang_value({}) == ""


def test_parse_course_list_item_extracts_semester():
    item = {
        "id": 123,
        "semesterDto": {"key": "25W"},
        "courseTitle": {"translations": {"translation": [{"lang": "de", "value": "Kurs"}]}},
    }
    course = _parse_course_list_item(item)
    assert course.course_id == 123
    assert course.semester_key == "25W"
    assert course.title_de == "Kurs"


def test_campus_from_rooms_garching():
    data = {
        "courseGroupDtos": [
            {
                "appointmentDtos": [
                    {
                        "resourceName": "MI HS 1 (5602.EG.001)",
                    }
                ],
            }
        ],
    }
    assert _campus_from_rooms(data) == "garching"


def test_campus_from_rooms_munich():
    data = {
        "courseGroupDtos": [
            {
                "appointmentDtos": [
                    {
                        "resourceName": "Hörsaal (0503.02.370)",
                    }
                ],
            }
        ],
    }
    assert _campus_from_rooms(data) == "münchen"


def test_campus_from_rooms_freising():
    data = {
        "courseGroupDtos": [
            {
                "appointmentDtos": [
                    {
                        "resourceName": "Hörsaal 22 (WZWH22) (4277.EG.129)",
                    }
                ],
            }
        ],
    }
    assert _campus_from_rooms(data) == "freising"


def test_campus_from_rooms_empty():
    assert _campus_from_rooms({"courseGroupDtos": []}) == ""
    assert _campus_from_rooms({"courseGroupDtos": [{"appointmentDtos": []}]}) == ""


def test_campus_from_rooms_online_only():
    data = {
        "courseGroupDtos": [
            {
                "appointmentDtos": [
                    {
                        "resourceName": "Online: Videokonferenz",
                    }
                ],
            }
        ],
    }
    assert _campus_from_rooms(data) == ""
