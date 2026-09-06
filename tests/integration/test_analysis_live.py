"""Actual archived races, chronological holdouts and a raw-prefix leakage check."""

import asyncio
import importlib.util
from pathlib import Path

import fastf1
import httpx
import pytest
import pytest_asyncio

from f1_pitwall.core.config import Settings
from f1_pitwall.providers.fastf1 import fetch_stream, normalize_archive, seconds
from f1_pitwall.services.analysis import analyze_driver
from f1_pitwall.services.analysis_context import AnalysisContext
from f1_pitwall.services.hub import Hub
from f1_pitwall.services.pace import analyze_tyres
from f1_pitwall.services.pair_analysis import calculate_overcut, calculate_undercut
from f1_pitwall.services.pit_analysis import estimate_pit_loss

pytestmark = [pytest.mark.network, pytest.mark.asyncio(loop_scope="module")]


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def analysis_hub():
    async with httpx.AsyncClient(follow_redirects=True) as client:
        yield Hub(client, Settings.from_env())


@pytest.mark.parametrize("selection", [(2023, 1), (2024, 1), (2023, 14)])
async def test_historical_holdouts_and_baselines(analysis_hub, selection):
    race = await analysis_hub.replay.load_race(*selection)
    spec = importlib.util.spec_from_file_location(
        "evaluation", Path(__file__).parents[2] / "scripts/evaluate_analysis.py"
    )
    evaluation = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(evaluation)
    report = await asyncio.to_thread(evaluation.evaluate, race)
    summary = report["summary"]
    assert summary["tyre_n"] >= 20
    assert summary["tyre_mae_seconds"] < 2  # Broad sanity, not an improvement claim.
    assert summary["pit_loss_n"] >= 10 and summary["pit_loss_mae_seconds"] < 5
    assert summary["rejoin_range_n"] >= 10 and summary["rejoin_range_miss_mean"] < 3
    if summary["rejoin_exact_n"]:
        assert summary["rejoin_mae_positions"] < 3
    assert report["pairs"] and report["traffic_examples"]
    context = AnalysisContext(race, 20)
    pit = estimate_pit_loss(context)
    assert pit.sample_count >= 3
    assert all(s["evidence_available_at"] <= context.cutoff for s in pit.components["samples"])
    young = AnalysisContext(race, 3)
    assert all(analyze_tyres(young, d).degradation_sec_per_lap is None for d in young.drivers)


async def test_real_numeric_pairs_and_clear_congested_examples(analysis_hub):
    race = await analysis_hub.replay.load_race(2023, 14)
    context = AnalysisContext(race, 19)
    loss = estimate_pit_loss(context)
    driver = next(d.driver.id for d in context.state.drivers if d.driver.code == "LEC")
    target = next(d.driver.id for d in context.state.drivers if d.driver.code == "SAI")
    for function in (calculate_undercut, calculate_overcut):
        result = function(context, driver, target, loss)
        assert result.estimated_margin is not None
        assert result.confidence == "LOW" and result.conditions_required
    context = AnalysisContext(race, 20)
    outputs = [analyze_driver(context, d).rejoin for d in context.drivers]
    assert any(r.traffic in {"HEAVY_TRAFFIC", "MODERATE_TRAFFIC"} for r in outputs)
    clear_context = AnalysisContext(race, 30)
    lawson = next(d.driver.id for d in clear_context.state.drivers if d.driver.code == "LAW")
    assert analyze_driver(clear_context, lawson).rejoin.traffic == "CLEAR_AIR"


def raw_prefix(race, cutoff):
    settings = Settings.from_env()
    session = fastf1.get_session(race.event.year, race.event.round, "R")
    topics = (
        "driver_list",
        "session_status",
        "timing_data",
        "timing_app_data",
        "track_status",
        "lap_count",
        "race_control_messages",
    )
    streams = {
        topic: [
            (t, record)
            for t, record in fetch_stream(session.api_path, topic, settings)
            if seconds(t) <= cutoff
        ]
        for topic in topics
    }
    return normalize_archive(race.event, streams)


async def test_real_raw_archive_prefix_has_identical_analysis(analysis_hub):
    race = await analysis_hub.replay.load_race(2024, 1)
    current = AnalysisContext(race, 25)
    prefix = await asyncio.to_thread(raw_prefix, race, current.cutoff)
    truncated = AnalysisContext(prefix, 25)
    for identity in current.drivers:
        assert analyze_driver(current, identity) == analyze_driver(truncated, identity)
    first, second = [d.driver.id for d in current.state.drivers[:2]]
    for function in (calculate_undercut, calculate_overcut):
        assert function(current, second, first, estimate_pit_loss(current)) == function(
            truncated, second, first, estimate_pit_loss(truncated)
        )
