"""Debug script: simulate the post-download phase of `tlf update`.

Runs the database operations and embedding generation that happen *after*
courses are fetched, using the data already in the local SQLite database.
Nothing is downloaded from the TUM API.

Usage:
    python scripts/debug_post_download.py
"""

from __future__ import annotations

import os
import sys
import warnings
from pathlib import Path

# Make sure the package is importable from the project root
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from rich.console import Console
from rich.progress import BarColumn, Progress, SpinnerColumn, TextColumn

from tum_lecture_finder.storage import CourseStore

console = Console()


class _QuietModelLoad:
    """Suppress noisy HuggingFace / torch output during model loading."""

    def __enter__(self) -> None:
        self._env_keys = [
            "TOKENIZERS_PARALLELISM",
            "HF_HUB_DISABLE_SYMLINKS_WARNING",
            "HF_HUB_DISABLE_PROGRESS_BARS",
            "TRANSFORMERS_VERBOSITY",
            "SAFETENSORS_FAST_GPU",
        ]
        self._prev_env = {k: os.environ.get(k) for k in self._env_keys}
        os.environ["TOKENIZERS_PARALLELISM"] = "false"
        os.environ["HF_HUB_DISABLE_SYMLINKS_WARNING"] = "1"
        os.environ["HF_HUB_DISABLE_PROGRESS_BARS"] = "1"
        os.environ["TRANSFORMERS_VERBOSITY"] = "error"
        os.environ["SAFETENSORS_FAST_GPU"] = "1"

        import logging

        logging.getLogger("sentence_transformers").setLevel(logging.ERROR)
        logging.getLogger("huggingface_hub").setLevel(logging.ERROR)
        logging.getLogger("transformers").setLevel(logging.ERROR)
        logging.getLogger("safetensors").setLevel(logging.ERROR)
        warnings.filterwarnings("ignore", category=UserWarning)
        warnings.filterwarnings("ignore", category=FutureWarning)

        self._real_stderr = sys.stderr
        self._real_stdout = sys.stdout
        self._devnull = Path(os.devnull).open("w")  # noqa: SIM115
        sys.stderr = self._devnull
        sys.stdout = self._devnull

    def __exit__(self, *args: object) -> None:
        sys.stderr = self._real_stderr
        sys.stdout = self._real_stdout
        self._devnull.close()
        for k in self._env_keys:
            prev = self._prev_env[k]
            if prev is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = prev


def main() -> None:
    """Run the debug simulation."""
    store = CourseStore()
    total_courses = store.course_count()

    if total_courses == 0:
        console.print("[red]No courses in database. Run [bold]tlf update[/bold] first.[/red]")
        store.close()
        sys.exit(1)

    console.print(
        f"\n[bold]Debug: simulating post-download phase "
        f"({total_courses} courses already in DB)[/bold]\n"
    )

    # ── DB operations ──────────────────────────────────────────────────────
    console.print("[dim]Saving courses to database…[/dim]")
    # Nothing to upsert – data is already there; just time a no-op update
    # to show the message appears and the timing is reasonable.
    # We'll query all courses to give the same I/O footprint as upsert.
    import time

    t0 = time.perf_counter()
    rows = store.get_all_courses()
    t1 = time.perf_counter()
    console.print(
        f"    [dim]→ read {len(rows)} rows in {t1 - t0:.2f}s (upsert would be ~similar)[/dim]"
    )

    console.print("[dim]Computing semester cross-references…[/dim]")
    t0 = time.perf_counter()
    store.compute_other_semesters()
    t1 = time.perf_counter()
    console.print(f"    [dim]→ cross-reference UPDATE in {t1 - t0:.2f}s[/dim]")

    semesters = {r["semester_key"] for r in rows}
    sem_label = ", ".join(sorted(semesters))
    console.print(
        f"[green]Done.[/green] {total_courses} courses stored"
        f" ({len(semesters)} semesters: [cyan]{sem_label}[/cyan])."
    )

    # ── Embedding generation ───────────────────────────────────────────────
    console.print("[dim]Rebuilding semantic search index…[/dim]")
    console.print("[dim]  → loading model (suppressed output)…[/dim]")

    with _QuietModelLoad():
        from tum_lecture_finder.search import build_embeddings, ensure_model_loaded  # noqa: PLC0415

        t0 = time.perf_counter()
        ensure_model_loaded()
        t1 = time.perf_counter()

    console.print(f"    [dim]→ model loaded in {t1 - t0:.2f}s[/dim]")

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
        TextColumn("[dim]{task.completed}/{task.total}[/dim]"),
        console=console,
    ) as emb_progress:
        emb_task = emb_progress.add_task("Computing embeddings…", total=None)

        def _on_emb(done: int, total: int) -> None:
            emb_progress.update(emb_task, completed=done, total=total)

        t0 = time.perf_counter()
        n = build_embeddings(store, on_progress=_on_emb)
        t1 = time.perf_counter()

    console.print(f"[green]Semantic index ready.[/green]  ({n} courses, {t1 - t0:.1f}s)")

    store.close()
    console.print("\n[bold green]✓ All post-download steps completed successfully.[/bold green]")


if __name__ == "__main__":
    main()
