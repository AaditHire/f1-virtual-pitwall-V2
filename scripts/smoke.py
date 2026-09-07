"""Start a real Uvicorn socket, exercise public API flows, then stop the server."""

import json
import os
import socket
import subprocess
import sys
import tempfile
import time

import httpx


def fetch_replay(get, replay):
    year, round, lap = replay
    prefix = f"/api/v1/replay/{year}/{round}"
    available = get(f"{prefix}/laps")
    snapshot = get(f"{prefix}/{lap}")
    assert len(snapshot["drivers"]) == available["participants"]
    assert snapshot["current_lap"] == lap
    first = snapshot["drivers"][0]
    assert get(f"{prefix}/{lap}/drivers/{first['driver']['id']}") == first
    return snapshot


def fetch_analysis(get, selection):
    year, round, lap = selection
    snapshot = get(f"/api/v1/replay/{year}/{round}/{lap}")
    first, second = snapshot["drivers"][:2]
    driver, target = second["driver"]["id"], first["driver"]["id"]
    prefix = f"/api/v1/analysis/{year}/{round}/{lap}"
    aggregate = get(f"{prefix}/drivers/{driver}")
    for part in ("tyres", "traffic"):
        assert get(f"{prefix}/drivers/{driver}/{part}") == aggregate[part]
    undercut = get(f"{prefix}/undercut?attacker={driver}&target={target}")
    overcut = get(f"{prefix}/overcut?driver={driver}&target={target}")
    assert aggregate["lap"] == lap and undercut["kind"] == "undercut"
    assert overcut["kind"] == "overcut"
    return {"driver": aggregate, "undercut": undercut, "overcut": overcut}


def fetch_strategy(get, selection):
    year, round, lap = selection
    grid = get(f"/api/v1/strategy/{year}/{round}/{lap}/all")
    assert len(grid["decisions"]) == grid["active_count"]
    assert grid["decisions"]
    driver = grid["decisions"][0]["driver"]["id"]
    decision = get(f"/api/v1/strategy/{year}/{round}/{lap}/drivers/{driver}")
    actions = get(f"/api/v1/strategy/{year}/{round}/{lap}/drivers/{driver}/actions")
    assert decision["driver"]["id"] == driver
    assert actions["actions"] == sorted(
        actions["actions"],
        key=lambda action: action["action_score"] if action["action_score"] is not None else -1e12,
        reverse=True,
    )
    return {"grid": grid, "driver": decision, "actions": actions}


def main(
    replay: tuple[int, int, int] | None = None,
    replay_only: bool = False,
    analysis=None,
    strategy=None,
):
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

                if analysis:
                    report = {"health": get("/health"), "analysis": fetch_analysis(get, analysis)}
                    print(json.dumps(report, indent=2, ensure_ascii=True), flush=True)
                    return

                if strategy:
                    report = {"health": get("/health"), "strategy": fetch_strategy(get, strategy)}
                    print(json.dumps(report, indent=2, ensure_ascii=True), flush=True)
                    return

                if replay_only:
                    report = {
                        "health": get("/health"),
                        "replay_snapshot": fetch_replay(get, replay),
                    }
                    print(json.dumps(report, indent=2, ensure_ascii=True), flush=True)
                    return

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
                if replay is not None:
                    report["replay_snapshot"] = fetch_replay(get, replay)
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
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--replay", nargs=3, type=int, metavar=("YEAR", "ROUND", "LAP"))
    parser.add_argument(
        "--replay-only", action="store_true", help="Test replay without the homepage providers"
    )
    parser.add_argument(
        "--strategy",
        nargs=3,
        type=int,
        metavar=("YEAR", "ROUND", "LAP"),
        help="Smoke all three Phase 4 routes against a real historical replay",
    )
    parser.add_argument(
        "--analysis",
        nargs=3,
        type=int,
        metavar=("YEAR", "ROUND", "LAP"),
        help="Smoke all five Phase 3 routes against a real historical replay",
    )
    arguments = parser.parse_args()
    if arguments.replay_only and not arguments.replay:
        parser.error("--replay-only requires --replay YEAR ROUND LAP")
    main(
        tuple(arguments.replay) if arguments.replay else None,
        arguments.replay_only,
        tuple(arguments.analysis) if arguments.analysis else None,
        tuple(arguments.strategy) if arguments.strategy else None,
    )
