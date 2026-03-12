"""Click CLI entry-point for TUM Lecture Finder."""

from __future__ import annotations

import asyncio
import logging
import os
import sys
import warnings
from pathlib import Path

import click
from rich.console import Console
from rich.panel import Panel
from rich.progress import (
    BarColumn,
    MofNCompleteColumn,
    Progress,
    SpinnerColumn,
    TaskID,
    TextColumn,
    TimeElapsedColumn,
)
from rich.rule import Rule
from rich.table import Table

from tum_lecture_finder.config import (
    DEFAULT_RECENT_SEMESTERS,
    format_semester,
)
from tum_lecture_finder.storage import CourseStore

console = Console()


def _make_progress() -> Progress:
    """Create a standard Rich progress bar with consistent styling.

    Returns:
        A Progress instance.

    """
    return Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
        MofNCompleteColumn(),
        TimeElapsedColumn(),
        console=console,
    )


@click.group()
@click.version_option(package_name="tum-lecture-finder")
def main() -> None:
    """TUM Lecture Finder - search TU Munich's course catalog.

    A CLI tool for full-text and semantic search across all academic courses
    offered at TU Munich.  Data is fetched from the public TUMonline API
    and stored in a local SQLite database.

    \b
    Quick start:
      tlf update                    # download recent semesters
      tlf update -s 25S -s 25W     # download specific semesters
      tlf search "robotics"
      tlf info <course-id>
      tlf stats
    """


# ── update ─────────────────────────────────────────────────────────────────


@main.command()
@click.option(
    "--concurrency",
    "-c",
    default=20,
    show_default=True,
    help="Max parallel HTTP requests when fetching course descriptions.",
)
@click.option(
    "--semester",
    "-s",
    "semesters",
    multiple=True,
    help=(
        "Semester key(s) to fetch (e.g. 25S, 25W).  "
        "May be repeated: -s 25S -s 25W.  "
        "Overrides --recent."
    ),
)
@click.option(
    "--recent",
    "-r",
    "recent_n",
    type=int,
    default=None,
    help=(
        f"Number of most-recent semesters to fetch (default: {DEFAULT_RECENT_SEMESTERS}).  "
        "Ignored when --semester is given."
    ),
)
@click.option(
    "--verbose",
    "-v",
    is_flag=True,
    default=False,
    help="Show detailed progress (per-semester counts, timing).",
)
def update(  # noqa: PLR0915
    concurrency: int,
    semesters: tuple[str, ...],
    recent_n: int | None,
    *,
    verbose: bool,
) -> None:
    """Fetch / refresh course data from the TUMonline API.

    Downloads course metadata and full descriptions (content, objectives,
    prerequisites, literature) for every listed course across multiple
    semesters.

    \b
    By default the most recent semesters are fetched.  Use --semester to pick
    specific semesters, or --recent N to control how many.

    \b
    Examples:
      tlf update                    # fetch recent semesters
      tlf update -s 25S -s 25W     # fetch two specific semesters
      tlf update --recent 6        # fetch last 6 semesters
      tlf update -c 10             # with 10 parallel requests
      tlf update -v                # verbose output
    """
    from tum_lecture_finder.fetcher import (  # noqa: PLC0415
        fetch_courses,
        fetch_semester_list,
        resolve_semester_ids,
    )

    store = CourseStore()

    # Load cached building-code → campus mappings
    building_cache = store.get_building_cache()

    # Resolve which semesters to fetch
    semester_ids: list[int] | None = None
    if semesters:
        console.print("[bold]Resolving semester ids...[/bold]")
        all_sems = asyncio.run(fetch_semester_list())
        semester_ids = resolve_semester_ids(all_sems, list(semesters))
        sem_labels = ", ".join(semesters)
        console.print(f"Fetching semesters: [cyan]{sem_labels}[/cyan]")
    elif recent_n is not None:
        console.print(f"[bold]Fetching last {recent_n} semesters...[/bold]")
        all_sems = asyncio.run(fetch_semester_list())
        semester_ids = [s["id"] for s in all_sems[:recent_n]]
        sem_labels = ", ".join(s["key"] for s in all_sems[:recent_n])
        console.print(f"Semesters: [cyan]{sem_labels}[/cyan]")
    else:
        console.print(
            f"\n[bold]Fetching last {DEFAULT_RECENT_SEMESTERS} semesters from TUMonline...[/bold]"
        )

    with _make_progress() as progress:
        list_task: TaskID = progress.add_task("Course list", total=100)
        detail_task: TaskID = progress.add_task(
            "Descriptions",
            total=None,
            visible=False,
        )

        _lt, _dt = list_task, detail_task

        def _on_list(fetched: int, total: int, _t: TaskID = _lt) -> None:
            progress.update(_t, completed=fetched, total=total)

        def _on_detail(fetched: int, total: int, _t: TaskID = _dt) -> None:
            if not progress.tasks[_t].visible:
                progress.update(_t, visible=True, total=total)
            progress.update(_t, completed=fetched, total=total)

        def _on_semester(sem_key: str) -> None:
            if verbose:
                progress.console.print(f"  [dim]Fetched semester [cyan]{sem_key}[/cyan][/dim]")

        courses = asyncio.run(
            fetch_courses(
                semester_ids=semester_ids,
                concurrency=concurrency,
                building_cache=building_cache,
                on_list_progress=_on_list,
                on_detail_progress=_on_detail,
                on_semester=_on_semester,
            )
        )

    if courses:
        semesters_found = {c.semester_key for c in courses}
        sem_label = ", ".join(sorted(semesters_found))

        console.print("[dim]Saving courses to database...[/dim]")
        count = store.upsert_courses(courses)
        console.print("[dim]Computing semester cross-references...[/dim]")
        store.compute_other_semesters()
        store.upsert_building_cache(building_cache)
        console.print(
            f"[green]Done.[/green] {count} courses stored"
            f" ({len(semesters_found)} semesters: [cyan]{sem_label}[/cyan])."
        )

        # Rebuild semantic search embeddings — load model quietly, then
        # encode with a visible Rich progress bar.
        console.print("[dim]Rebuilding semantic search index...[/dim]")
        with _QuietModelLoad(banner=False):
            from tum_lecture_finder.search import (  # noqa: PLC0415
                build_embeddings,
                ensure_model_loaded,
            )

            ensure_model_loaded()

        with _make_progress() as emb_progress:
            emb_task = emb_progress.add_task("Embeddings", total=None)

            def _on_emb(done: int, total: int) -> None:
                emb_progress.update(emb_task, completed=done, total=total)

            build_embeddings(store, on_progress=_on_emb)

        console.print("[green]Semantic index ready.[/green]")
    else:
        console.print("\n[yellow]No courses returned by the API.[/yellow]")

    store.close()


# ── search ─────────────────────────────────────────────────────────────────


@main.command()
@click.argument("query")
@click.option(
    "--type",
    "-t",
    "course_type",
    default=None,
    help=(
        "Filter by course type abbreviation.  "
        "Common types: VO (lecture), SE (seminar), PR (lab/project), "
        "UE (exercise), VI (integrated course)."
    ),
)
@click.option(
    "--campus",
    default=None,
    help=(
        "Filter by campus (substring match against campus labels "
        "resolved from building codes via NavigaTUM).  "
        "Examples: garching, stammgelände, weihenstephan, heilbronn, straubing."
    ),
)
@click.option(
    "--limit",
    "-n",
    default=20,
    show_default=True,
    help="Maximum number of results to return.",
)
@click.option(
    "--mode",
    "-m",
    type=click.Choice(["keyword", "semantic", "hybrid"], case_sensitive=False),
    default="keyword",
    show_default=True,
    help=(
        "Search strategy.  "
        "'keyword' uses SQLite full-text search (fast, exact keyword matching).  "
        "'semantic' uses a neural language model for meaning-based matching "
        "(handles synonyms, typos, related concepts; slower on first run "
        "while the model downloads).  "
        "'hybrid' combines both approaches for best results (slow)."
    ),
)
def search(
    query: str,
    course_type: str | None,
    campus: str | None,
    limit: int,
    mode: str,
) -> None:
    """Search courses by keyword.

    Searches across titles, descriptions, objectives, prerequisites,
    and literature of all locally stored courses.

    \b
    Examples:
      tlf search "machine learning"
      tlf search "Regelungstechnik" --mode keyword
      tlf search "PCB design" --campus garching --type PR
    """
    from tum_lecture_finder.search import (  # noqa: PLC0415
        fulltext_search,
        hybrid_search,
        semantic_search,
    )

    store = CourseStore()

    if store.course_count() == 0:
        console.print("[yellow]No courses stored. Run [bold]tlf update[/bold] first.[/yellow]")
        store.close()
        sys.exit(1)

    if mode == "keyword":
        results = fulltext_search(
            store,
            query,
            course_type=course_type,
            campus=campus,
            limit=limit,
        )
    elif mode == "semantic":
        with _QuietModelLoad():
            results = semantic_search(
                store,
                query,
                course_type=course_type,
                campus=campus,
                limit=limit,
            )
    else:
        with _QuietModelLoad():
            results = hybrid_search(
                store,
                query,
                course_type=course_type,
                campus=campus,
                limit=limit,
            )

    store.close()

    if not results:
        console.print("[yellow]No results found.[/yellow]")
        sys.exit(0)

    _print_results(results)


class _QuietModelLoad:
    """Context manager that suppresses noisy HuggingFace / torch / safetensors output.

    Args:
        banner: If ``True`` (default), print a status message on entry.

    """

    def __init__(self, *, banner: bool = True) -> None:
        self._banner = banner

    def __enter__(self) -> None:
        if self._banner:
            console.print(
                "[dim]Preparing language model (cached locally)...[/dim]", highlight=False
            )

        # Save previous env state
        self._env_keys = [
            "TOKENIZERS_PARALLELISM",
            "HF_HUB_DISABLE_SYMLINKS_WARNING",
            "HF_HUB_DISABLE_PROGRESS_BARS",
            "TRANSFORMERS_VERBOSITY",
            "SAFETENSORS_FAST_GPU",
        ]
        self._prev_env = {k: os.environ.get(k) for k in self._env_keys}

        # Silence HuggingFace ecosystem
        os.environ["TOKENIZERS_PARALLELISM"] = "false"
        os.environ["HF_HUB_DISABLE_SYMLINKS_WARNING"] = "1"
        os.environ["HF_HUB_DISABLE_PROGRESS_BARS"] = "1"
        os.environ["TRANSFORMERS_VERBOSITY"] = "error"
        os.environ["SAFETENSORS_FAST_GPU"] = "1"

        logging.getLogger("sentence_transformers").setLevel(logging.ERROR)
        logging.getLogger("huggingface_hub").setLevel(logging.ERROR)
        logging.getLogger("transformers").setLevel(logging.ERROR)
        logging.getLogger("safetensors").setLevel(logging.ERROR)
        warnings.filterwarnings("ignore", category=UserWarning)
        warnings.filterwarnings("ignore", category=FutureWarning)

        # Redirect stderr AND stdout to suppress safetensors load report,
        # tqdm bars, and HF Hub warnings.  Rich Console keeps a reference to
        # the original sys.stdout so its output still works.
        self._real_stderr = sys.stderr
        self._real_stdout = sys.stdout
        self._devnull = Path(os.devnull).open("w")
        sys.stderr = self._devnull
        sys.stdout = self._devnull

    def __exit__(self, *args: object) -> None:
        # Restore stdout and stderr
        sys.stderr = self._real_stderr
        sys.stdout = self._real_stdout
        self._devnull.close()

        # Restore env vars
        for k in self._env_keys:
            prev = self._prev_env[k]
            if prev is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = prev


def _print_results(results: list) -> None:
    """Render search results as a rich table.

    Args:
        results: List of :class:`SearchResult` objects.

    """
    table = Table(title="Search Results", show_lines=True, expand=True)
    table.add_column("#", style="dim", width=3)
    table.add_column("ID", style="dim", width=10)
    table.add_column("Code", style="cyan", width=12)
    table.add_column("Type", width=4)
    table.add_column("Sem", width=5)
    table.add_column("Title", ratio=3)
    table.add_column("Organisation", ratio=2)
    table.add_column("Score", justify="right", width=6)

    for i, r in enumerate(results, 1):
        c = r.course
        title = c.title_en or c.title_de

        # If the search matched description rather than title, show a snippet
        display_title = title
        if r.snippet:
            display_title = f"{title}\n[dim italic]...{r.snippet}...[/dim italic]"
        if r.other_semesters:
            also = ", ".join(sorted(r.other_semesters))
            display_title += f"\n[dim]Also: {also}[/dim]"

        table.add_row(
            str(i),
            str(c.course_id),
            c.course_number,
            c.course_type,
            c.semester_key,
            display_title,
            c.organisation,
            f"{r.score:.2f}",
        )

    console.print(table)


# ── info ───────────────────────────────────────────────────────────────────


@main.command()
@click.argument("course_id", type=int)
def info(course_id: int) -> None:
    """Show detailed info for a course by its TUMonline ID.

    The numeric ID is shown in the 'ID' column of search results.

    \b
    Example:
      tlf info 950841236
    """
    store = CourseStore()
    row = store.get_course(course_id)
    store.close()

    if not row:
        console.print(f"[red]Course {course_id} not found in local database.[/red]")
        sys.exit(1)

    from tum_lecture_finder.storage import row_to_course  # noqa: PLC0415

    c = row_to_course(row)

    # Title header
    title = c.title_de
    if c.title_en and c.title_en != c.title_de:
        title += f"\n[italic]{c.title_en}[/italic]"
    console.print(Panel(title, style="cyan", expand=False))

    info_table = Table(show_header=False, box=None, pad_edge=False)
    info_table.add_column("Key", style="bold", width=16)
    info_table.add_column("Value")
    info_table.add_row("Course ID", str(c.course_id))
    info_table.add_row("Code", c.course_number)
    info_table.add_row("Semester", format_semester(c.semester_key))
    info_table.add_row("Type", c.course_type)
    info_table.add_row("SWS", c.sws)
    info_table.add_row("Language", c.language)
    info_table.add_row("Campus", c.campus.title() if c.campus else "")
    info_table.add_row("Organisation", c.organisation)
    info_table.add_row("Instructors", c.instructors)
    if c.identity_code_id:
        info_table.add_row("Identity Code", str(c.identity_code_id))
    console.print(info_table)

    # Description sections
    sections: list[tuple[str, str]] = [
        ("Content (DE)", c.content_de),
        ("Content (EN)", c.content_en),
        ("Objectives (DE)", c.objectives_de),
        ("Objectives (EN)", c.objectives_en),
        ("Prerequisites", c.prerequisites),
        ("Literature", c.literature),
    ]
    for heading, text in sections:
        if text:
            console.print(Rule(heading, style="dim"))
            console.print(text)
    console.print()


# ── stats ──────────────────────────────────────────────────────────────────


@main.command()
def stats() -> None:
    """Show database statistics (courses per semester)."""
    store = CourseStore()
    total = store.course_count()
    counts = store.semester_counts()
    store.close()

    console.print(f"[bold]Total courses in database:[/bold] {total}\n")
    if counts:
        table = Table(title="Courses per Semester", show_lines=False)
        table.add_column("Semester", style="cyan")
        table.add_column("Key", style="dim")
        table.add_column("Courses", justify="right")
        for key, cnt in counts:
            table.add_row(format_semester(key), key, str(cnt))
        console.print(table)


# ── build-index ────────────────────────────────────────────────────────────


@main.command("build-index")
def build_index() -> None:
    """Pre-compute semantic search embeddings (speeds up --mode semantic).

    Encodes all course texts with a sentence-transformer model and caches
    the embeddings to disk.  This needs to be run only once after each
    ``tlf update``.  Without it, semantic search falls back to encoding
    on the fly (much slower).
    """
    store = CourseStore()
    total = store.course_count()

    if total == 0:
        console.print("[yellow]No courses stored. Run [bold]tlf update[/bold] first.[/yellow]")
        store.close()
        sys.exit(1)

    console.print(f"[bold]Encoding {total} courses...[/bold]")

    with _QuietModelLoad():
        from tum_lecture_finder.search import (  # noqa: PLC0415
            build_embeddings,
            ensure_model_loaded,
        )

        ensure_model_loaded()

    with _make_progress() as emb_progress:
        emb_task = emb_progress.add_task("Embeddings", total=None)

        def _on_emb(done: int, total: int) -> None:
            emb_progress.update(emb_task, completed=done, total=total)

        n = build_embeddings(store, on_progress=_on_emb)

    console.print(f"[green]Done.[/green] Cached embeddings for {n} courses.")
    store.close()


# ── serve ──────────────────────────────────────────────────────────────────


@main.command()
@click.option("--host", "-h", default="127.0.0.1", show_default=True, help="Bind address.")
@click.option("--port", "-p", default=8000, show_default=True, help="Listen port.")
def serve(host: str, port: int) -> None:
    """Start the web UI server.

    \b
    Launches a local web server that provides a browser-based search
    interface for the course database.

    \b
    Examples:
      tlf serve                   # http://127.0.0.1:8000
      tlf serve -p 3000           # custom port
      tlf serve -h 0.0.0.0       # listen on all interfaces
    """
    store = CourseStore()
    total = store.course_count()
    store.close()

    if total == 0:
        console.print("[yellow]No courses stored. Run [bold]tlf update[/bold] first.[/yellow]")
        sys.exit(1)

    console.print("[bold]Starting web server...[/bold]")
    console.print(f"  [cyan]http://{host}:{port}[/cyan]")
    console.print(f"  {total} courses available")
    console.print("[dim]Press Ctrl+C to stop.[/dim]\n")

    from tum_lecture_finder.web import run_server  # noqa: PLC0415

    run_server(host=host, port=port)
