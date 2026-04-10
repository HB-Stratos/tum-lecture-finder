"""Tests for the CLI commands.

Uses Click's CliRunner with a tmp-path backed CourseStore and mocked network calls.
All tests patch tum_lecture_finder.cli.CourseStore so no production DB is touched.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock, patch

if TYPE_CHECKING:
    from pathlib import Path

import pytest
from click.testing import CliRunner

from tum_lecture_finder.cli import main
from tum_lecture_finder.fetcher import FetchResult
from tum_lecture_finder.models import Course, SearchResult
from tum_lecture_finder.storage import CourseStore

# ── helpers ──────────────────────────────────────────────────────────────────

def _make_course(**kwargs: Any) -> Course:
    defaults: dict[str, Any] = {
        "course_id": 1,
        "semester_key": "25W",
        "course_number": "IN2064",
        "title_de": "Maschinelles Lernen",
        "title_en": "Machine Learning",
        "course_type": "VO",
        "campus": "garching",
        "identity_code_id": 100,
        "content_en": "Supervised and unsupervised learning.",
        "organisation": "Informatics",
    }
    defaults.update(kwargs)
    return Course(**defaults)


def _make_result(course: Course | None = None, score: float = 1.0) -> SearchResult:
    return SearchResult(course=course or _make_course(), score=score)


@pytest.fixture
def tmp_store(tmp_path: Path) -> CourseStore:
    """A temporary CourseStore backed by an on-disk file in tmp_path."""
    s = CourseStore(db_path=tmp_path / "test.db")
    yield s
    s.close()


@pytest.fixture
def populated_store(tmp_path: Path) -> CourseStore:
    """A temporary CourseStore pre-loaded with one course."""
    s = CourseStore(db_path=tmp_path / "test.db")
    s.upsert_courses([_make_course()])
    s.compute_other_semesters()
    yield s
    s.close()


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


# ── update command ────────────────────────────────────────────────────────────

class TestUpdateCommand:
    """tlf update fetches courses and saves them."""

    def _mock_fetch_courses(self) -> FetchResult:
        return FetchResult(detailed=[_make_course()], list_only=[])

    def _mock_semester_list(self) -> list[dict]:
        return [
            {"id": 204, "key": "25W"},
            {"id": 203, "key": "25S"},
            {"id": 202, "key": "24W"},
            {"id": 201, "key": "24S"},
        ]

    def _update_patches(self, tmp_store: CourseStore, courses: list[Course] | None = None):
        """Return the standard set of patches for update command tests."""
        return (
            patch("tum_lecture_finder.cli.CourseStore", return_value=tmp_store),
            patch(
                "tum_lecture_finder.fetcher.fetch_courses",
                new=AsyncMock(return_value=courses or self._mock_fetch_courses()),
            ),
            # build_embeddings and ensure_model_loaded are lazily imported inside
            # the function body from tum_lecture_finder.search; patch at the source.
            patch("tum_lecture_finder.search.build_embeddings", return_value=1),
            patch("tum_lecture_finder.search.ensure_model_loaded"),
            patch("tum_lecture_finder.cli._QuietModelLoad"),
        )

    def test_update_no_args_succeeds(self, runner: CliRunner, tmp_store: CourseStore):
        """update with no args fetches the 2-year window around the current semester."""
        with (
            patch("tum_lecture_finder.cli.CourseStore", return_value=tmp_store),
            patch(
                "tum_lecture_finder.fetcher.fetch_semester_list",
                new=AsyncMock(return_value=self._mock_semester_list()),
            ),
            patch(
                "tum_lecture_finder.fetcher.fetch_courses",
                new=AsyncMock(return_value=self._mock_fetch_courses()),
            ),
            patch("tum_lecture_finder.search.build_embeddings", return_value=1),
            patch("tum_lecture_finder.search.ensure_model_loaded"),
            patch("tum_lecture_finder.cli._QuietModelLoad"),
        ):
            result = runner.invoke(main, ["update"])
        assert result.exit_code == 0, result.output

    def test_update_with_semester_flag(self, runner: CliRunner, tmp_store: CourseStore):
        """update -s 25W resolves semester ID and fetches that semester."""
        with (
            patch("tum_lecture_finder.cli.CourseStore", return_value=tmp_store),
            patch(
                "tum_lecture_finder.fetcher.fetch_semester_list",
                new=AsyncMock(return_value=self._mock_semester_list()),
            ),
            patch(
                "tum_lecture_finder.fetcher.fetch_courses",
                new=AsyncMock(return_value=self._mock_fetch_courses()),
            ),
            patch("tum_lecture_finder.search.build_embeddings", return_value=1),
            patch("tum_lecture_finder.search.ensure_model_loaded"),
            patch("tum_lecture_finder.cli._QuietModelLoad"),
        ):
            result = runner.invoke(main, ["update", "-s", "25W"])
        assert result.exit_code == 0, result.output

    def test_update_with_recent_flag(self, runner: CliRunner, tmp_store: CourseStore):
        """update --recent 2 fetches the two most recent semesters."""
        with (
            patch("tum_lecture_finder.cli.CourseStore", return_value=tmp_store),
            patch(
                "tum_lecture_finder.fetcher.fetch_semester_list",
                new=AsyncMock(return_value=self._mock_semester_list()),
            ),
            patch(
                "tum_lecture_finder.fetcher.fetch_courses",
                new=AsyncMock(return_value=self._mock_fetch_courses()),
            ),
            patch("tum_lecture_finder.search.build_embeddings", return_value=1),
            patch("tum_lecture_finder.search.ensure_model_loaded"),
            patch("tum_lecture_finder.cli._QuietModelLoad"),
        ):
            result = runner.invoke(main, ["update", "--recent", "2"])
        assert result.exit_code == 0, result.output

    def test_update_no_courses_returned(self, runner: CliRunner, tmp_store: CourseStore):
        """update prints a warning when API returns no courses."""
        with (
            patch("tum_lecture_finder.cli.CourseStore", return_value=tmp_store),
            patch(
                "tum_lecture_finder.fetcher.fetch_semester_list",
                new=AsyncMock(return_value=self._mock_semester_list()),
            ),
            patch(
                "tum_lecture_finder.fetcher.fetch_courses",
                new=AsyncMock(return_value=FetchResult(detailed=[], list_only=[])),
            ),
        ):
            result = runner.invoke(main, ["update"])
        assert result.exit_code == 0
        assert "No courses" in result.output

    def test_update_unknown_semester_key_errors(self, runner: CliRunner, tmp_store: CourseStore):
        """update -s UNKNOWN raises BadParameter and exits non-zero."""
        with (
            patch("tum_lecture_finder.cli.CourseStore", return_value=tmp_store),
            patch(
                "tum_lecture_finder.fetcher.fetch_semester_list",
                new=AsyncMock(return_value=self._mock_semester_list()),
            ),
        ):
            result = runner.invoke(main, ["update", "-s", "UNKNOWN"])
        assert result.exit_code != 0


# ── search command ────────────────────────────────────────────────────────────

class TestSearchCommand:
    """tlf search returns formatted results or warns when DB is empty."""

    def test_search_keyword_returns_results(
        self, runner: CliRunner, populated_store: CourseStore
    ):
        """search keyword mode prints a result table with the course code."""
        results = [_make_result()]
        with (
            patch("tum_lecture_finder.cli.CourseStore", return_value=populated_store),
            patch("tum_lecture_finder.search.fulltext_search", return_value=results),
        ):
            result = runner.invoke(main, ["search", "machine learning"])
        assert result.exit_code == 0
        # Rich may wrap long text across lines; check for course code which is short
        assert "IN2064" in result.output

    def test_search_empty_db_exits_with_message(
        self, runner: CliRunner, tmp_store: CourseStore
    ):
        """search on an empty DB exits 1 with a helpful message."""
        with patch("tum_lecture_finder.cli.CourseStore", return_value=tmp_store):
            result = runner.invoke(main, ["search", "anything"])
        assert result.exit_code == 1
        assert "tlf update" in result.output

    def test_search_no_results_exits_zero(
        self, runner: CliRunner, populated_store: CourseStore
    ):
        """search returning no results exits 0 (not an error)."""
        with (
            patch("tum_lecture_finder.cli.CourseStore", return_value=populated_store),
            patch("tum_lecture_finder.search.fulltext_search", return_value=[]),
        ):
            result = runner.invoke(main, ["search", "zzznoresults"])
        assert result.exit_code == 0
        assert "No results" in result.output

    def test_search_with_type_filter(
        self, runner: CliRunner, populated_store: CourseStore
    ):
        """search --type VO passes type to fulltext_search."""
        results = [_make_result()]
        with (
            patch("tum_lecture_finder.cli.CourseStore", return_value=populated_store),
            patch(
                "tum_lecture_finder.search.fulltext_search", return_value=results
            ) as mock_fts,
        ):
            result = runner.invoke(main, ["search", "ml", "--type", "VO"])
        assert result.exit_code == 0
        call_kwargs = mock_fts.call_args.kwargs
        assert call_kwargs["course_type"] == "VO"

    def test_search_with_campus_filter(
        self, runner: CliRunner, populated_store: CourseStore
    ):
        """search --campus garching passes campus to fulltext_search."""
        results = [_make_result()]
        with (
            patch("tum_lecture_finder.cli.CourseStore", return_value=populated_store),
            patch(
                "tum_lecture_finder.search.fulltext_search", return_value=results
            ) as mock_fts,
        ):
            result = runner.invoke(main, ["search", "ml", "--campus", "garching"])
        assert result.exit_code == 0
        call_kwargs = mock_fts.call_args.kwargs
        assert call_kwargs["campus"] == "garching"

    def test_search_with_limit(self, runner: CliRunner, populated_store: CourseStore):
        """search -n 5 passes limit=5 to the search function."""
        results = [_make_result()]
        with (
            patch("tum_lecture_finder.cli.CourseStore", return_value=populated_store),
            patch(
                "tum_lecture_finder.search.fulltext_search", return_value=results
            ) as mock_fts,
        ):
            result = runner.invoke(main, ["search", "ml", "-n", "5"])
        assert result.exit_code == 0
        assert mock_fts.call_args.kwargs["limit"] == 5

    def test_search_mode_semantic_invokes_semantic_search(
        self, runner: CliRunner, populated_store: CourseStore
    ):
        """search --mode semantic calls semantic_search, not fulltext_search."""
        results = [_make_result()]
        with (
            patch("tum_lecture_finder.cli.CourseStore", return_value=populated_store),
            patch("tum_lecture_finder.search.semantic_search", return_value=results),
            patch("tum_lecture_finder.cli._QuietModelLoad") as quiet,
        ):
            quiet.return_value.__enter__ = MagicMock(return_value=None)
            quiet.return_value.__exit__ = MagicMock(return_value=False)
            result = runner.invoke(main, ["search", "ml", "--mode", "semantic"])
        assert result.exit_code == 0

    def test_search_mode_hybrid_invokes_hybrid_search(
        self, runner: CliRunner, populated_store: CourseStore
    ):
        """search --mode hybrid calls hybrid_search."""
        results = [_make_result()]
        with (
            patch("tum_lecture_finder.cli.CourseStore", return_value=populated_store),
            patch("tum_lecture_finder.search.hybrid_search", return_value=results),
            patch("tum_lecture_finder.cli._QuietModelLoad") as quiet,
        ):
            quiet.return_value.__enter__ = MagicMock(return_value=None)
            quiet.return_value.__exit__ = MagicMock(return_value=False)
            result = runner.invoke(main, ["search", "ml", "--mode", "hybrid"])
        assert result.exit_code == 0

    def test_search_result_displays_other_semesters(
        self, runner: CliRunner, populated_store: CourseStore
    ):
        """search output shows 'Also: ...' when course spans multiple semesters."""
        r = _make_result()
        r.other_semesters = ["25S", "24W"]
        with (
            patch("tum_lecture_finder.cli.CourseStore", return_value=populated_store),
            patch("tum_lecture_finder.search.fulltext_search", return_value=[r]),
        ):
            result = runner.invoke(main, ["search", "ml"])
        assert result.exit_code == 0
        assert "Also:" in result.output


# ── info command ──────────────────────────────────────────────────────────────

class TestInfoCommand:
    """tlf info <id> shows course details."""

    def test_info_found(self, runner: CliRunner, populated_store: CourseStore):
        """info with a known ID prints course details."""
        with patch("tum_lecture_finder.cli.CourseStore", return_value=populated_store):
            result = runner.invoke(main, ["info", "1"])
        assert result.exit_code == 0
        assert "IN2064" in result.output or "Machine Learning" in result.output

    def test_info_not_found_exits_1(self, runner: CliRunner, tmp_store: CourseStore):
        """info with an unknown ID exits 1."""
        with patch("tum_lecture_finder.cli.CourseStore", return_value=tmp_store):
            result = runner.invoke(main, ["info", "9999"])
        assert result.exit_code == 1
        assert "not found" in result.output.lower()

    def test_info_shows_content(self, runner: CliRunner, tmp_path: Path):
        """info includes content fields when present."""
        store = CourseStore(db_path=tmp_path / "test.db")
        course = _make_course(
            content_en="Learn supervised learning.",
            objectives_en="Understand gradient descent.",
        )
        store.upsert_courses([course])
        with patch("tum_lecture_finder.cli.CourseStore", return_value=store):
            result = runner.invoke(main, ["info", "1"])
        store.close()
        assert result.exit_code == 0
        assert "supervised learning" in result.output


# ── stats command ─────────────────────────────────────────────────────────────

class TestStatsCommand:
    """tlf stats shows DB statistics."""

    def test_stats_empty_db(self, runner: CliRunner, tmp_store: CourseStore):
        """stats on empty DB shows 0 total."""
        with patch("tum_lecture_finder.cli.CourseStore", return_value=tmp_store):
            result = runner.invoke(main, ["stats"])
        assert result.exit_code == 0
        assert "0" in result.output

    def test_stats_populated_db(self, runner: CliRunner, populated_store: CourseStore):
        """stats on a populated DB shows course count and semester key."""
        with patch("tum_lecture_finder.cli.CourseStore", return_value=populated_store):
            result = runner.invoke(main, ["stats"])
        assert result.exit_code == 0
        assert "1" in result.output  # 1 course
        assert "25W" in result.output  # semester key present


# ── build-index command ───────────────────────────────────────────────────────

class TestBuildIndexCommand:
    """tlf build-index triggers embedding generation."""

    def test_build_index_empty_db_exits_1(self, runner: CliRunner, tmp_store: CourseStore):
        """build-index on empty DB exits 1 with a message."""
        with patch("tum_lecture_finder.cli.CourseStore", return_value=tmp_store):
            result = runner.invoke(main, ["build-index"])
        assert result.exit_code == 1
        assert "tlf update" in result.output

    def test_build_index_populated_db(self, runner: CliRunner, populated_store: CourseStore):
        """build-index on a populated DB calls build_embeddings."""
        with (
            patch("tum_lecture_finder.cli.CourseStore", return_value=populated_store),
            # Lazily imported from tum_lecture_finder.search inside the function body
            patch("tum_lecture_finder.search.build_embeddings", return_value=1) as mock_build,
            patch("tum_lecture_finder.search.ensure_model_loaded"),
            patch("tum_lecture_finder.cli._QuietModelLoad"),
        ):
            result = runner.invoke(main, ["build-index"])
        assert result.exit_code == 0
        mock_build.assert_called_once()


# ── serve command ─────────────────────────────────────────────────────────────

class TestServeCommand:
    """tlf serve starts (or refuses to start) the web server."""

    def test_serve_empty_db_exits_1(self, runner: CliRunner, tmp_store: CourseStore):
        """serve on an empty DB exits 1 and does not start the server."""
        with patch("tum_lecture_finder.cli.CourseStore", return_value=tmp_store):
            result = runner.invoke(main, ["serve"])
        assert result.exit_code == 1
        assert "tlf update" in result.output

    def test_serve_calls_run_server(self, runner: CliRunner, populated_store: CourseStore):
        """serve with courses in the DB calls run_server."""
        with (
            patch("tum_lecture_finder.cli.CourseStore", return_value=populated_store),
            patch("tum_lecture_finder.web.run_server") as mock_run,
        ):
            runner.invoke(main, ["serve"])
        mock_run.assert_called_once()
        call_kwargs = mock_run.call_args.kwargs
        assert call_kwargs["host"] == "127.0.0.1"
        assert call_kwargs["port"] == 8000

    def test_serve_custom_host_and_port(
        self, runner: CliRunner, populated_store: CourseStore
    ):
        """serve -h 0.0.0.0 -p 3000 passes those to run_server."""
        with (
            patch("tum_lecture_finder.cli.CourseStore", return_value=populated_store),
            patch("tum_lecture_finder.web.run_server") as mock_run,
        ):
            runner.invoke(main, ["serve", "-h", "0.0.0.0", "-p", "3000"])  # noqa: S104
        call_kwargs = mock_run.call_args.kwargs
        assert call_kwargs["host"] == "0.0.0.0"  # noqa: S104
        assert call_kwargs["port"] == 3000

    def test_serve_public_bind_prints_warning(
        self, runner: CliRunner, populated_store: CourseStore
    ):
        """serve on 0.0.0.0 prints a security warning."""
        import os
        env = {**os.environ, "TLF_NO_BIND_WARNING": ""}
        env.pop("TLF_NO_BIND_WARNING", None)
        with (
            patch("tum_lecture_finder.cli.CourseStore", return_value=populated_store),
            patch("tum_lecture_finder.web.run_server"),
            patch.dict("os.environ", {"TLF_NO_BIND_WARNING": ""}, clear=False),
        ):
            # Ensure warning env var is NOT set
            import os as _os
            _os.environ.pop("TLF_NO_BIND_WARNING", None)
            result = runner.invoke(main, ["serve", "-h", "0.0.0.0"])  # noqa: S104
        assert "Warning" in result.output or "warning" in result.output.lower()


# ── _semester_sort_key / century-boundary tests ──────────────────────────────


class TestSemesterSortKey:
    """Verify century-aware semester comparison handles 1990s keys correctly."""

    def test_1990s_sort_before_2000s(self):
        """99W (1999) must sort before 00S (2000)."""
        from tum_lecture_finder.config import semester_sort_key

        assert semester_sort_key("99W") < semester_sort_key("00S")

    def test_1990s_sort_before_2020s(self):
        """98S (1998) must sort before 25W (2025)."""
        from tum_lecture_finder.config import semester_sort_key

        assert semester_sort_key("98S") < semester_sort_key("25W")

    def test_99w_is_not_future(self):
        """_semester_is_future must return False for 99W when current is 25W."""
        from tum_lecture_finder.cli import _semester_is_future

        assert not _semester_is_future("99W", "25W")

    def test_98s_is_not_future(self):
        """_semester_is_future must return False for 98S when current is 25W."""
        from tum_lecture_finder.cli import _semester_is_future

        assert not _semester_is_future("98S", "25W")

    def test_26s_is_future(self):
        """_semester_is_future must return True for 26S when current is 25W."""
        from tum_lecture_finder.cli import _semester_is_future

        assert _semester_is_future("26S", "25W")

    def test_probe_old_semesters_labeled_past(self, runner: CliRunner):
        """probe-semesters must not list 1990s keys in the future-semesters line."""
        sems = [
            {"id": 210, "key": "27W"},
            {"id": 205, "key": "25W"},
            {"id": 101, "key": "99W"},
            {"id": 100, "key": "98S"},
        ]
        with patch(
            "tum_lecture_finder.fetcher.fetch_semester_list",
            new=AsyncMock(return_value=sems),
        ):
            result = runner.invoke(main, ["probe-semesters"])
        assert result.exit_code == 0
        def _is_future_available_line(ln: str) -> bool:
            return "future" in ln.lower() and "available" in ln.lower()

        future_line = next(
            (ln for ln in result.output.splitlines() if _is_future_available_line(ln)),
            "",
        )
        assert "99W" not in future_line
        assert "98S" not in future_line


# ── _select_update_window unit tests ─────────────────────────────────────────


class TestSelectUpdateWindow:
    """Unit tests for _select_update_window semester selection logic."""

    def _rich_sems(self) -> list[dict]:
        """10 semesters: 23S … 27W, in API order (newest first)."""
        return [
            {"id": 210, "key": "27W"},
            {"id": 209, "key": "27S"},
            {"id": 208, "key": "26W"},
            {"id": 207, "key": "26S"},
            {"id": 206, "key": "25W"},
            {"id": 205, "key": "25S"},
            {"id": 204, "key": "24W"},
            {"id": 203, "key": "24S"},
            {"id": 202, "key": "23W"},
            {"id": 201, "key": "23S"},
        ]

    def test_current_in_middle_selects_nine(self):
        """When >=4 semesters exist on both sides, selects exactly 9 (4+1+4)."""
        from tum_lecture_finder.cli import _select_update_window

        _ids, keys = _select_update_window(self._rich_sems(), current="25W")

        assert "25W" in keys
        assert len(keys) == 9
        assert keys == sorted(keys)  # returned in ascending order

    def test_current_near_start_clamps_to_available(self):
        """When fewer than 4 older semesters exist, includes all available older ones."""
        from tum_lecture_finder.cli import _select_update_window

        _ids, keys = _select_update_window(self._rich_sems(), current="23S")

        assert "23S" in keys
        # Only 0 semesters before 23S; up to 4 after
        assert keys[0] == "23S"
        assert len(keys) == 5  # 23S + 23W + 24S + 24W + 25S

    def test_current_near_end_clamps_to_available(self):
        """When fewer than 4 newer semesters exist, includes all available newer ones."""
        from tum_lecture_finder.cli import _select_update_window

        _ids, keys = _select_update_window(self._rich_sems(), current="27W")

        assert "27W" in keys
        assert keys[-1] == "27W"
        assert len(keys) == 5  # 25W + 26S + 26W + 27S + 27W

    def test_current_not_in_list_uses_closest_past(self):
        """When current key is absent from the list, falls back to the closest past semester."""
        from tum_lecture_finder.cli import _select_update_window

        sems = [
            {"id": 204, "key": "24W"},
            {"id": 203, "key": "24S"},
            {"id": 202, "key": "23W"},
        ]
        # "25S" is not in the list; closest past is "24W"
        _ids, keys = _select_update_window(sems, current="25S")

        assert "24W" in keys

    def test_ids_match_keys(self):
        """Returned IDs correspond to the returned keys in the same order."""
        from tum_lecture_finder.cli import _select_update_window

        ids, keys = _select_update_window(self._rich_sems(), current="25W")

        id_map = {s["key"]: s["id"] for s in self._rich_sems()}
        assert ids == [id_map[k] for k in keys]


# ── probe-semesters command ────────────────────────────────────────────────


class TestProbeSemestersCommand:
    """tlf probe-semesters lists semesters from TUMonline with status labels."""

    def _mock_semester_list(self) -> list[dict]:
        return [
            {"id": 208, "key": "27W"},  # future
            {"id": 207, "key": "27S"},  # future
            {"id": 206, "key": "26W"},  # future (or current depending on date)
            {"id": 205, "key": "26S"},
            {"id": 204, "key": "25W"},
            {"id": 203, "key": "25S"},
            {"id": 202, "key": "24W"},
        ]

    def test_probe_lists_semesters(self, runner: CliRunner):
        """probe-semesters prints a table of semesters."""
        with patch(
            "tum_lecture_finder.fetcher.fetch_semester_list",
            new=AsyncMock(return_value=self._mock_semester_list()),
        ):
            result = runner.invoke(main, ["probe-semesters"])
        assert result.exit_code == 0
        assert "25W" in result.output

    def test_probe_shows_future_count(self, runner: CliRunner):
        """probe-semesters reports how many future semesters are available."""
        with patch(
            "tum_lecture_finder.fetcher.fetch_semester_list",
            new=AsyncMock(return_value=self._mock_semester_list()),
        ):
            result = runner.invoke(main, ["probe-semesters"])
        assert result.exit_code == 0
        # Should mention future semester count or "No future"
        assert "future" in result.output.lower() or "future" in result.output

    def test_probe_empty_api_response(self, runner: CliRunner):
        """probe-semesters handles empty API response gracefully."""
        with patch(
            "tum_lecture_finder.fetcher.fetch_semester_list",
            new=AsyncMock(return_value=[]),
        ):
            result = runner.invoke(main, ["probe-semesters"])
        assert result.exit_code == 0
        assert "No semesters" in result.output

    def test_probe_semesters_ordered_newest_first(self, runner: CliRunner):
        """probe-semesters table shows newest semester before oldest, regardless of API order."""
        shuffled = [
            {"id": 202, "key": "24W"},
            {"id": 208, "key": "27W"},
            {"id": 204, "key": "25W"},
        ]
        with patch(
            "tum_lecture_finder.fetcher.fetch_semester_list",
            new=AsyncMock(return_value=shuffled),
        ):
            result = runner.invoke(main, ["probe-semesters"])
        assert result.exit_code == 0
        pos_27w = result.output.index("27W")
        pos_25w = result.output.index("25W")
        pos_24w = result.output.index("24W")
        # Newest first: 27W should appear before 25W before 24W in output
        assert pos_27w < pos_25w < pos_24w
