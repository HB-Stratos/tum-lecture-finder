"""Tests for domain model classes."""

from tum_lecture_finder.models import Course, SearchResult


def _make_course(**kwargs) -> Course:
    defaults = {"course_id": 1, "semester_key": "25W"}
    defaults.update(kwargs)
    return Course(**defaults)


class TestCourseTitle:
    """Course.title returns the best available title."""

    def test_english_preferred_when_both_present(self):
        c = _make_course(title_en="Machine Learning", title_de="Maschinelles Lernen")
        assert c.title == "Machine Learning"

    def test_falls_back_to_german_when_no_english(self):
        c = _make_course(title_de="Maschinelles Lernen")
        assert c.title == "Maschinelles Lernen"

    def test_empty_string_when_neither_present(self):
        c = _make_course()
        assert c.title == ""

    def test_english_empty_string_falls_back_to_german(self):
        c = _make_course(title_en="", title_de="Maschinelles Lernen")
        assert c.title == "Maschinelles Lernen"


class TestCourseSearchableText:
    """Course.searchable_text concatenates all human-readable fields."""

    def test_includes_all_populated_fields(self):
        c = _make_course(
            course_number="IN2064",
            title_de="Maschinelles Lernen",
            title_en="Machine Learning",
            content_de="Inhalt DE",
            content_en="Content EN",
            objectives_de="Ziele DE",
            objectives_en="Objectives EN",
            prerequisites="Math",
            literature="Bishop 2006",
            organisation="Informatics",
            instructors="Prof. Müller",
        )
        text = c.searchable_text
        assert "IN2064" in text
        assert "Maschinelles Lernen" in text
        assert "Machine Learning" in text
        assert "Inhalt DE" in text
        assert "Content EN" in text
        assert "Ziele DE" in text
        assert "Objectives EN" in text
        assert "Math" in text
        assert "Bishop 2006" in text
        assert "Informatics" in text
        assert "Prof. Müller" in text

    def test_empty_fields_excluded(self):
        c = _make_course(title_en="Machine Learning")
        text = c.searchable_text
        assert text == "Machine Learning"

    def test_all_empty_returns_empty_string(self):
        c = _make_course()
        assert c.searchable_text == ""

    def test_fields_separated_by_spaces(self):
        c = _make_course(title_en="A", title_de="B", course_number="C")
        # All three should appear and be space-separated (order: number, de, en)
        parts = c.searchable_text.split()
        assert "A" in parts
        assert "B" in parts
        assert "C" in parts


class TestCourseEmbeddingText:
    """Course.embedding_text uses titles and descriptions only."""

    def test_includes_titles_and_descriptions(self):
        c = _make_course(
            title_en="Machine Learning",
            title_de="Maschinelles Lernen",
            content_en="Content EN",
            content_de="Inhalt DE",
            objectives_en="Objectives EN",
            objectives_de="Ziele DE",
        )
        text = c.embedding_text
        assert "Machine Learning" in text
        assert "Maschinelles Lernen" in text
        assert "Content EN" in text
        assert "Inhalt DE" in text
        assert "Objectives EN" in text
        assert "Ziele DE" in text

    def test_excludes_instructors_and_literature(self):
        c = _make_course(
            title_en="ML",
            instructors="Prof. Smith",
            literature="Bishop 2006",
            organisation="Informatics",
            course_number="IN2064",
        )
        text = c.embedding_text
        assert "Prof. Smith" not in text
        assert "Bishop 2006" not in text
        assert "Informatics" not in text
        assert "IN2064" not in text

    def test_empty_fields_excluded(self):
        c = _make_course(title_en="ML")
        assert c.embedding_text == "ML"

    def test_all_empty_returns_empty_string(self):
        c = _make_course()
        assert c.embedding_text == ""


class TestSearchResult:
    """SearchResult dataclass construction and defaults."""

    def test_defaults(self):
        c = _make_course(title_en="ML")
        r = SearchResult(course=c)
        assert r.score == 0.0
        assert r.snippet == ""
        assert r.highlights == []
        assert r.other_semesters == []

    def test_other_semesters_are_independent_per_instance(self):
        """Default factory must not share the list between instances."""
        c = _make_course()
        r1 = SearchResult(course=c)
        r2 = SearchResult(course=c)
        r1.other_semesters.append("25W")
        assert r2.other_semesters == []

    def test_highlights_are_independent_per_instance(self):
        c = _make_course()
        r1 = SearchResult(course=c)
        r2 = SearchResult(course=c)
        r1.highlights.append("foo")
        assert r2.highlights == []

    def test_explicit_values_stored(self):
        c = _make_course(title_en="ML")
        r = SearchResult(course=c, score=0.85, snippet="…machine learning…", highlights=["ml"])
        assert r.score == 0.85
        assert r.snippet == "…machine learning…"
        assert r.highlights == ["ml"]
        assert r.course is c
