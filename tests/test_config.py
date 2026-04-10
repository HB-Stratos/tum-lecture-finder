"""Tests for config module."""

import datetime
from unittest.mock import patch

import pytest

from tum_lecture_finder.config import (
    current_semester_key,
    format_semester,
    is_current_or_future_semester,
    semester_sort_key,
)

# ── current_semester_key ───────────────────────────────────────────────────


class TestCurrentSemesterKey:
    """Tests for current_semester_key()."""

    def _mock_date(self, year: int, month: int, day: int):
        """Return a patch that freezes ``datetime.now()`` to the given date."""
        fake_date = datetime.date(year, month, day)

        class FakeDateTime(datetime.datetime):
            @classmethod
            def now(cls, tz=None):
                return datetime.datetime(
                    fake_date.year,
                    fake_date.month,
                    fake_date.day,
                    tzinfo=tz,
                )

        return patch("datetime.datetime", FakeDateTime)

    def test_format_is_valid(self):
        key = current_semester_key()
        assert len(key) >= 2
        assert key[-1] in ("W", "S")
        assert key[:-1].isdigit()

    def test_october_is_winter(self):
        with self._mock_date(2025, 10, 1):
            assert current_semester_key() == "25W"

    def test_november_is_winter(self):
        with self._mock_date(2025, 11, 15):
            assert current_semester_key() == "25W"

    def test_december_is_winter(self):
        with self._mock_date(2025, 12, 31):
            assert current_semester_key() == "25W"

    def test_april_is_summer(self):
        with self._mock_date(2025, 4, 1):
            assert current_semester_key() == "25S"

    def test_july_is_summer(self):
        with self._mock_date(2025, 7, 15):
            assert current_semester_key() == "25S"

    def test_september_is_summer(self):
        with self._mock_date(2025, 9, 30):
            assert current_semester_key() == "25S"

    def test_january_is_previous_winter(self):
        with self._mock_date(2026, 1, 15):
            assert current_semester_key() == "25W"

    def test_february_is_previous_winter(self):
        with self._mock_date(2026, 2, 14):
            assert current_semester_key() == "25W"

    def test_march_is_previous_winter(self):
        with self._mock_date(2026, 3, 31):
            assert current_semester_key() == "25W"

    def test_year_wrap_around(self):
        """Jan 2001 → winter of 2000 → '0W'."""
        with self._mock_date(2001, 1, 1):
            assert current_semester_key() == "0W"

    def test_year_2099_winter(self):
        with self._mock_date(2099, 11, 1):
            assert current_semester_key() == "99W"


# ── format_semester ────────────────────────────────────────────────────────


class TestFormatSemester:
    """Tests for format_semester()."""

    def test_winter_basic(self):
        assert format_semester("25W") == "Winter 2025/26"

    def test_summer_basic(self):
        assert format_semester("25S") == "Summer 2025"

    def test_winter_year_00(self):
        assert format_semester("0W") == "Winter 2000/01"

    def test_summer_year_00(self):
        assert format_semester("0S") == "Summer 2000"

    def test_winter_year_99(self):
        assert format_semester("99W") == "Winter 1999/00"

    def test_lowercase_semester_type(self):
        """format_semester accepts lowercase type indicator."""
        assert format_semester("25w") == "Winter 2025/26"

    def test_invalid_key_raises(self):
        with pytest.raises((ValueError, IndexError)):
            format_semester("")


class TestSemesterSortKey:
    """Tests for semester_sort_key()."""

    def test_summer_before_winter_same_year(self):
        assert semester_sort_key("25S") < semester_sort_key("25W")

    def test_winter_before_next_summer(self):
        assert semester_sort_key("25W") < semester_sort_key("26S")

    def test_century_boundary(self):
        """99W (1999) sorts before 00S (2000)."""
        assert semester_sort_key("99W") < semester_sort_key("0S")

    def test_equal_keys(self):
        assert semester_sort_key("25W") == semester_sort_key("25W")

    def test_1990s_before_2020s(self):
        assert semester_sort_key("90S") < semester_sort_key("25S")


class TestIsCurrentOrFutureSemester:
    """Tests for is_current_or_future_semester()."""

    def test_current_semester_is_current(self):
        assert is_current_or_future_semester("25W", current="25W") is True

    def test_future_semester(self):
        assert is_current_or_future_semester("26S", current="25W") is True

    def test_past_semester(self):
        assert is_current_or_future_semester("25S", current="25W") is False

    def test_far_past(self):
        assert is_current_or_future_semester("23W", current="25W") is False

    def test_far_future(self):
        assert is_current_or_future_semester("30S", current="25W") is True
