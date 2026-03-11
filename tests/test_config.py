"""Tests for config module."""

from tum_lecture_finder.config import current_semester_key, format_semester


def test_current_semester_key_format():
    key = current_semester_key()
    assert len(key) >= 2
    assert key[-1] in ("W", "S")
    assert key[:-1].isdigit()


def test_format_semester_winter():
    assert format_semester("25W") == "Winter 2025/26"


def test_format_semester_summer():
    assert format_semester("25S") == "Summer 2025"
