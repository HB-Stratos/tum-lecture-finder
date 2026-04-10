"""Profile the TLF web server by simulating real user interactions.

Starts the server in a subprocess, then uses Playwright to navigate
the site as a real user would, measuring response times for each action.

Usage:
    python scripts/profile_server.py
"""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))


def wait_for_server(url: str, timeout: float = 60.0) -> None:
    """Poll until the server responds."""
    import httpx  # noqa: PLC0415

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            r = httpx.get(url, timeout=2.0)
            if r.status_code == 200:
                return
        except httpx.HTTPError:
            pass
        time.sleep(0.5)
    msg = f"Server did not start within {timeout}s"
    raise TimeoutError(msg)


def profile() -> None:
    """Run the profiling suite."""
    from playwright.sync_api import sync_playwright  # noqa: PLC0415

    port = 8765
    base_url = f"http://127.0.0.1:{port}"

    # Start the server
    print(f"Starting server on port {port}...")
    server = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "tum_lecture_finder.web:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    results: list[dict[str, object]] = []

    def measure(label: str) -> dict[str, object]:
        """Create a timing context."""
        return {"label": label, "start": time.perf_counter()}

    def finish(m: dict[str, object], *, extra: str = "") -> None:
        elapsed = time.perf_counter() - m["start"]
        m["elapsed_ms"] = round(elapsed * 1000, 1)
        m["extra"] = extra
        results.append(m)
        print(f"  {m['label']}: {m['elapsed_ms']}ms {extra}")

    try:
        wait_for_server(base_url)
        print("Server ready.\n")

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context()
            page = context.new_page()

            # 1. Home page load
            print("=== Page Loads ===")
            m = measure("Home page (/)")
            page.goto(base_url)
            page.wait_for_load_state("networkidle")
            finish(m)

            # 2. Stats page load
            m = measure("Stats page (/stats)")
            page.goto(f"{base_url}/stats")
            page.wait_for_load_state("networkidle")
            finish(m)

            # 3. Search interactions
            print("\n=== Search (keyword mode) ===")
            page.goto(base_url)
            page.wait_for_load_state("networkidle")

            # Set mode to keyword
            mode_select = page.locator("#filter-mode")
            if mode_select.count() > 0:
                mode_select.select_option("keyword")

            search_input = page.locator("#search-input, input[name='q'], input[type='search']")
            search_input.fill("machine learning")
            m = measure("Search: 'machine learning' (keyword)")
            page.keyboard.press("Enter")
            page.wait_for_selector(".result-card, .no-results, .results-count", timeout=30000)
            finish(m)

            # Count results
            result_cards = page.locator(".result-card")
            count = result_cards.count()
            print(f"    Results rendered: {count}")

            # 4. Semantic search
            print("\n=== Search (semantic mode) ===")
            if mode_select.count() > 0:
                mode_select.select_option("semantic")
            search_input.fill("courses about building robots")
            m = measure("Search: 'building robots' (semantic)")
            page.keyboard.press("Enter")
            page.wait_for_selector(".result-card, .no-results, .results-count", timeout=60000)
            finish(m)
            count = page.locator(".result-card").count()
            print(f"    Results rendered: {count}")

            # 5. Hybrid search
            print("\n=== Search (hybrid mode) ===")
            if mode_select.count() > 0:
                mode_select.select_option("hybrid")
            search_input.fill("PCB design")
            m = measure("Search: 'PCB design' (hybrid)")
            page.keyboard.press("Enter")
            page.wait_for_selector(".result-card, .no-results, .results-count", timeout=60000)
            finish(m)
            count = page.locator(".result-card").count()
            print(f"    Results rendered: {count}")

            # 6. Click on first result to go to course detail
            print("\n=== Course Detail ===")
            if count > 0:
                first_card = page.locator(".result-card").first
                href = first_card.get_attribute("href")
                m = measure(f"Course detail ({href})")
                first_card.click()
                page.wait_for_load_state("networkidle")
                finish(m)

                # Wait for schedule AJAX
                time.sleep(1)
                schedule_rows = page.locator(".schedule-row, .appointment-row, tr").count()
                print(f"    Schedule rows rendered: {schedule_rows}")

            # 7. Load more pagination
            print("\n=== Pagination ===")
            page.goto(base_url)
            page.wait_for_load_state("networkidle")
            if mode_select.count() > 0:
                mode_select.select_option("keyword")
            search_input = page.locator("#search-input, input[name='q'], input[type='search']")
            search_input.fill("Informatik")
            page.keyboard.press("Enter")
            page.wait_for_selector(".result-card, .no-results, .results-count", timeout=30000)
            initial_count = page.locator(".result-card").count()

            load_more = page.locator(
                "#load-more, button:has-text('Load more'), button:has-text('more')"
            )
            if load_more.count() > 0 and load_more.is_visible():
                m = measure("Load more results")
                load_more.click()
                page.wait_for_timeout(3000)
                new_count = page.locator(".result-card").count()
                finish(m, extra=f"({initial_count} -> {new_count} results)")

            # 8. Direct API profiling (bypassing browser rendering)
            print("\n=== Direct API Timing ===")
            import httpx  # noqa: PLC0415

            queries = [
                ("keyword", "Regelungstechnik"),
                ("keyword", "machine learning"),
                ("semantic", "how to build a robot"),
                ("hybrid", "deep learning neural networks"),
                ("keyword", "Mathematik"),
            ]
            for mode, q in queries:
                m = measure(f"API /api/search?mode={mode}&q={q}")
                r = httpx.get(
                    f"{base_url}/api/search",
                    params={"q": q, "mode": mode, "limit": 20},
                    timeout=60.0,
                )
                data = r.json()
                finish(m, extra=f"({data.get('count', '?')} results, {len(r.content)} bytes)")

            # API: course detail
            m = measure("API /api/course/950736917")
            r = httpx.get(f"{base_url}/api/course/950736917", timeout=10.0)
            finish(m, extra=f"({len(r.content)} bytes)")

            # API: stats
            m = measure("API /api/stats")
            r = httpx.get(f"{base_url}/api/stats", timeout=10.0)
            finish(m, extra=f"({len(r.content)} bytes)")

            # API: filters
            m = measure("API /api/filters")
            r = httpx.get(f"{base_url}/api/filters", timeout=10.0)
            finish(m, extra=f"({len(r.content)} bytes)")

            browser.close()

        # Print summary
        print("\n" + "=" * 60)
        print("PROFILE SUMMARY")
        print("=" * 60)
        print(f"{'Operation':<50} {'Time':>8}")
        print("-" * 60)
        for r in results:
            label = str(r["label"])[:50]
            ms = r["elapsed_ms"]
            extra = r.get("extra", "")
            print(f"{label:<50} {ms:>7.1f}ms  {extra}")

    finally:
        server.terminate()
        server.wait(timeout=5)
        print("\nServer stopped.")


if __name__ == "__main__":
    profile()
