"""Start a real Uvicorn socket, exercise public API flows, then stop the server."""

import json
import os
import socket
import subprocess
import sys
import tempfile
import time

import httpx


def main():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    with tempfile.TemporaryFile(mode="w+") as log:
        process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "uvicorn",
                "f1_pitwall.main:app",
                "--host",
                "127.0.0.1",
                "--port",
                str(port),
            ],
            stdout=log,
            stderr=log,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        try:
            with httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=120) as client:
                for _ in range(100):
                    try:
                        if client.get("/health").status_code == 200:
                            break
                    except httpx.ConnectError:
                        pass
                    if process.poll() is not None:
                        raise RuntimeError("Uvicorn exited during startup")
                    time.sleep(0.1)
                else:
                    raise RuntimeError("Uvicorn startup timed out")

                def get(path):
                    response = client.get(path)
                    response.raise_for_status()
                    return response.json()

                latest = get("/api/v1/seasons/latest")
                year = latest["year"]
                report = {
                    "checked_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                    "health": get("/health"),
                    "latest_season": latest,
                    "active_season": get("/api/v1/seasons/active"),
                }
                for category in ("drivers", "constructors", "calendar"):
                    rows = get(f"/api/v1/seasons/{year}/{category}")
                    assert rows, category
                    report[category] = len(rows)
                for endpoint in ("events/current", "events/next", "sessions/next"):
                    report[endpoint] = get(f"/api/v1/{endpoint}?timezone=Asia/Kolkata")
                for category in ("drivers", "constructors"):
                    rows = get(f"/api/v1/standings/{category}/{year}")
                    assert rows, category
                    report[f"{category}_standings"] = len(rows)
                news = get("/api/v1/news?limit=3")
                assert news["articles"]
                report["news"] = news
                home = get("/api/v1/home?timezone=Asia/Kolkata")
                assert not home["errors"], home["errors"]
                assert home["latest_news"] and home["driver_standings_top"]
                report["home"] = home
                report["providers"] = get("/api/v1/providers/status")
                print(json.dumps(report, indent=2, ensure_ascii=True), flush=True)
        except Exception:
            log.seek(0)
            print(log.read(), file=sys.stderr)
            raise
        finally:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)


if __name__ == "__main__":
    main()
