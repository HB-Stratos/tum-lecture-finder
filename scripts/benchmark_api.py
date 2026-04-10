"""Micro-benchmark: isolate server-side API response times.

Starts the server, sends requests directly with keep-alive,
and measures just the HTTP response time (no browser overhead).
"""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))


def main() -> None:
    import httpx

    port = 8766
    base = f"http://127.0.0.1:{port}"

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

    # Wait for server
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        try:
            httpx.get(base, timeout=2.0)
            break
        except httpx.HTTPError:
            time.sleep(0.5)
    else:
        server.terminate()
        raise TimeoutError

    print("Server ready.\n")

    try:
        # Use a persistent session with keep-alive
        with httpx.Client(base_url=base, timeout=60.0) as client:
            # Warmup
            client.get("/api/stats")
            client.get("/api/filters")
            client.get("/api/search", params={"q": "test", "mode": "keyword"})

            endpoints = [
                ("GET /api/stats", "/api/stats", {}),
                ("GET /api/filters", "/api/filters", {}),
                ("GET /api/course/950736917", "/api/course/950736917", {}),
                ("Keyword: 'Mathematik'", "/api/search", {"q": "Mathematik", "mode": "keyword"}),
                (
                    "Keyword: 'machine learning'",
                    "/api/search",
                    {"q": "machine learning", "mode": "keyword"},
                ),
                (
                    "Semantic: 'build robots'",
                    "/api/search",
                    {"q": "build robots", "mode": "semantic"},
                ),
                (
                    "Hybrid: 'deep learning'",
                    "/api/search",
                    {"q": "deep learning", "mode": "hybrid"},
                ),
                (
                    "Keyword: 'Informatik' limit=50",
                    "/api/search",
                    {"q": "Informatik", "mode": "keyword", "limit": 50},
                ),
            ]

            print(f"{'Endpoint':<45} {'Avg':>8} {'Min':>8} {'Max':>8}  (5 runs)")
            print("-" * 80)

            for label, path, params in endpoints:
                times = []
                for _ in range(5):
                    t0 = time.perf_counter()
                    r = client.get(path, params=params)
                    elapsed = time.perf_counter() - t0
                    assert r.status_code == 200, f"{path} returned {r.status_code}"
                    times.append(elapsed * 1000)

                avg = sum(times) / len(times)
                mn = min(times)
                mx = max(times)
                print(f"{label:<45} {avg:>7.1f}ms {mn:>7.1f}ms {mx:>7.1f}ms")

    finally:
        server.terminate()
        server.wait(timeout=5)
        print("\nServer stopped.")


if __name__ == "__main__":
    main()
