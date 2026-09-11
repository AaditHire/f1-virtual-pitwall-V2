# ruff: noqa: E501
"""Phase 11A leakage-safe pre-race prediction feasibility study.

This script is deliberately isolated from production services.  Target-race race streams are
used only to construct labels; every feature is either known before the race start or is a
chronological aggregate of strictly earlier events.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import time
from collections import Counter, defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from statistics import mean, median
from typing import Any

import httpx
import numpy as np
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import GradientBoostingRegressor
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression, Ridge
from sklearn.metrics import accuracy_score, log_loss, mean_absolute_error
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler

from f1_pitwall.core.config import Settings
from f1_pitwall.domain.models import QualifyingResult, RaceResult
from f1_pitwall.domain.replay import HistoricalRace
from f1_pitwall.providers.http import ProviderHTTP
from f1_pitwall.providers.jolpica import Jolpica

ARCHIVE_GLOB = "analysis-history-*.json"
METADATA_CACHE = Path(".cache/phase11a-prerace-metadata.json")
DATASET_CACHE = Path(".cache/phase11a-prerace-dataset.json")
JSON_OUTPUT = Path("docs/phase11a-pre-race-research.json")
REPORT_OUTPUT = Path("docs/phase11a-pre-race-research.md")

TRAIN_YEARS = {2021, 2022, 2023}
DEVELOPMENT_YEARS = {2024}
HOLDOUT_YEARS = {2025}

NUMERIC_FEATURES = (
    "grid",
    "qualifying_position",
    "qualifying_gap_seconds",
    "qualifying_phase",
    "grid_penalty_delta",
    "season_starts",
    "season_mean_finish",
    "season_points_per_start",
    "driver_prior_starts",
    "driver_prior_mean_finish",
    "driver_recent3_finish",
    "driver_prior_mean_net_gain",
    "driver_prior_dnf_rate",
    "driver_prior_stop_mean",
    "driver_prior_first_stop_ratio",
    "team_prior_starts",
    "team_prior_mean_finish",
    "team_prior_mean_net_gain",
    "team_prior_dnf_rate",
    "team_prior_stop_mean",
    "circuit_prior_races",
    "circuit_prior_stop_mean",
    "circuit_prior_first_stop_ratio",
)
CATEGORICAL_FEATURES = ("driver_id", "constructor_id", "circuit_id")
MODEL_FEATURES = NUMERIC_FEATURES + CATEGORICAL_FEATURES

RACE_DERIVED_FORBIDDEN_FEATURES = {
    "finish_position",
    "classified",
    "dnf",
    "pit_stop_count",
    "first_stop_lap",
    "first_stop_ratio",
    "strategy_family",
    "compound_sequence",
    "race_laps",
    "points",
}


@dataclass(frozen=True)
class EventArchive:
    race: HistoricalRace
    path: Path

    @property
    def key(self) -> str:
        return f"{self.race.event.year}-{self.race.event.round}"


def parse_lap_time(value: str | None) -> float | None:
    if not value:
        return None
    try:
        parts = [float(part) for part in value.split(":")]
    except ValueError:
        return None
    seconds = sum(part * 60**index for index, part in enumerate(reversed(parts)))
    return seconds if math.isfinite(seconds) and seconds > 0 else None


def load_archives(cache_dir: Path) -> list[EventArchive]:
    archives = []
    for path in cache_dir.glob(ARCHIVE_GLOB):
        race = HistoricalRace.model_validate_json(path.read_text(encoding="utf-8"))
        if race.event.year in TRAIN_YEARS | DEVELOPMENT_YEARS | HOLDOUT_YEARS:
            archives.append(EventArchive(race, path))
    return sorted(archives, key=lambda item: (item.race.event.race_date, item.race.event.round))


async def fetch_metadata(archives: list[EventArchive], path: Path) -> dict[str, dict[str, Any]]:
    cached: dict[str, dict[str, Any]] = {}
    if path.exists():
        cached = json.loads(path.read_text(encoding="utf-8"))
    missing = [archive for archive in archives if archive.key not in cached]
    if not missing:
        return cached

    settings = Settings.from_env()
    async with httpx.AsyncClient(follow_redirects=True) as client:
        provider = Jolpica(ProviderHTTP("jolpica-phase11a", client, settings))
        for index, archive in enumerate(missing, 1):
            year, round_number = archive.race.event.year, archive.race.event.round
            qualifying = await provider.qualifying(year, round_number)
            results = await provider.results(year, round_number)
            cached[archive.key] = {
                "qualifying": [row.model_dump(mode="json") for row in qualifying],
                "results": [row.model_dump(mode="json") for row in results],
            }
            print(f"metadata {index}/{len(missing)}: {archive.key}", flush=True)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(cached, indent=2), encoding="utf-8")
    return cached


def identity_key(code: str | None, last_name: str) -> str:
    return (code or last_name).casefold().replace(" ", "").replace("-", "")


def event_stop_labels(race: HistoricalRace) -> dict[str, dict[str, Any]]:
    laps_by_driver: dict[str, list] = defaultdict(list)
    for lap in race.laps:
        laps_by_driver[lap.driver_id].append(lap)
    stops_by_driver: dict[str, list] = defaultdict(list)
    for stop in race.pit_stops:
        stops_by_driver[stop.driver_id].append(stop)
    compounds_by_driver: dict[str, dict[int, tuple[float, str]]] = defaultdict(dict)
    for stint in race.stints:
        if not stint.compound:
            continue
        current = compounds_by_driver[stint.driver_id].get(stint.number)
        if current is None or stint.observed_at < current[0]:
            compounds_by_driver[stint.driver_id][stint.number] = (
                stint.observed_at,
                stint.compound.upper(),
            )

    output = {}
    for participant in race.participants:
        driver_id = participant.driver.id
        stops = sorted(stops_by_driver[driver_id], key=lambda item: item.entered_at)
        first_stop_lap = None
        if stops:
            completed = [
                lap.number
                for lap in laps_by_driver[driver_id]
                if lap.completed_at < stops[0].entered_at
            ]
            first_stop_lap = max(completed, default=0) + 1
        compound_sequence = [
            value[1] for _, value in sorted(compounds_by_driver[driver_id].items())
        ]
        output[identity_key(participant.driver.code, participant.driver.last_name)] = {
            "pit_stop_count": len(stops),
            "first_stop_lap": first_stop_lap,
            "compound_sequence": compound_sequence,
        }
    return output


def qualifying_features(rows: list[QualifyingResult]) -> dict[str, dict[str, Any]]:
    staged = []
    for row in rows:
        phase = 3 if row.q3 else 2 if row.q2 else 1 if row.q1 else 0
        value = parse_lap_time(row.q3 or row.q2 or row.q1)
        staged.append((row, phase, value))
    fastest = {
        phase: min(
            value for _, row_phase, value in staged if row_phase == phase and value is not None
        )
        for phase in {phase for _, phase, _ in staged}
        if any(row_phase == phase and value is not None for _, row_phase, value in staged)
    }
    return {
        identity_key(row.driver.code, row.driver.last_name): {
            "qualifying_position": row.position,
            "qualifying_phase": phase,
            "qualifying_gap_seconds": value - fastest[phase]
            if value is not None and phase in fastest
            else None,
        }
        for row, phase, value in staged
    }


def is_classified(status: str | None) -> bool:
    value = (status or "").casefold()
    return value in {"finished", "lapped"} or value.startswith("+")


def strategy_family(stop_count: int, first_stop_ratio: float | None) -> str | None:
    if stop_count < 1 or first_stop_ratio is None:
        return None
    if stop_count == 1:
        return "ONE_STOP_LONG" if first_stop_ratio >= 0.40 else "ONE_STOP_OTHER"
    return "MULTI_STOP_SHORT" if first_stop_ratio <= 0.30 else "MULTI_STOP_OTHER"


def build_label_rows(
    archives: list[EventArchive], metadata: dict[str, dict[str, Any]]
) -> list[dict[str, Any]]:
    rows = []
    for archive in archives:
        race = archive.race
        supplied = metadata[archive.key]
        qualifying = qualifying_features(
            [QualifyingResult.model_validate(row) for row in supplied["qualifying"]]
        )
        results = [RaceResult.model_validate(row) for row in supplied["results"]]
        pit_labels = event_stop_labels(race)
        race_laps = max((row.laps or 0 for row in results), default=0)
        for result in results:
            key = identity_key(result.driver.code, result.driver.last_name)
            pits = pit_labels.get(key)
            if result.grid is None or result.position is None or pits is None or race_laps <= 0:
                continue
            first_stop_lap = pits["first_stop_lap"]
            first_stop_ratio = first_stop_lap / race_laps if first_stop_lap else None
            q = qualifying.get(key, {})
            rows.append(
                {
                    "event_key": archive.key,
                    "event_date": race.event.race_date.isoformat(),
                    "year": race.event.year,
                    "round": race.event.round,
                    "circuit_id": race.event.circuit.id,
                    "driver_id": result.driver.id,
                    "driver_code": result.driver.code,
                    "constructor_id": result.constructor.id,
                    "grid": result.grid if result.grid > 0 else len(results),
                    "qualifying_position": q.get("qualifying_position"),
                    "qualifying_phase": q.get("qualifying_phase"),
                    "qualifying_gap_seconds": q.get("qualifying_gap_seconds"),
                    "grid_penalty_delta": (
                        result.grid - q["qualifying_position"]
                        if result.grid > 0 and q.get("qualifying_position") is not None
                        else 0
                    ),
                    "finish_position": result.position,
                    "classified": is_classified(result.status),
                    "dnf": not is_classified(result.status),
                    "status": result.status,
                    "points": float(result.points or 0),
                    "race_laps": race_laps,
                    "pit_stop_count": pits["pit_stop_count"],
                    "first_stop_lap": first_stop_lap,
                    "first_stop_ratio": first_stop_ratio,
                    "strategy_family": strategy_family(pits["pit_stop_count"], first_stop_ratio),
                    "compound_sequence": pits["compound_sequence"],
                }
            )
    return sorted(rows, key=lambda row: (row["event_date"], row["finish_position"]))


def _average(rows: list[dict[str, Any]], key: str) -> float | None:
    values = [float(row[key]) for row in rows if row.get(key) is not None]
    return mean(values) if values else None


def _median(rows: list[dict[str, Any]], key: str) -> float | None:
    values = [float(row[key]) for row in rows if row.get(key) is not None]
    return median(values) if values else None


def add_chronological_features(label_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Attach only strictly-prior-event aggregates to each target row."""
    driver_history: dict[str, list[dict[str, Any]]] = defaultdict(list)
    team_history: dict[str, list[dict[str, Any]]] = defaultdict(list)
    circuit_history: dict[str, list[dict[str, Any]]] = defaultdict(list)
    season_history: dict[tuple[int, str], list[dict[str, Any]]] = defaultdict(list)
    output = []
    by_event: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in label_rows:
        by_event[row["event_key"]].append(row)

    events = sorted(by_event.values(), key=lambda rows: rows[0]["event_date"])
    for event_rows in events:
        for original in event_rows:
            row = dict(original)
            driver = driver_history[row["driver_id"]]
            team = team_history[row["constructor_id"]]
            circuit = circuit_history[row["circuit_id"]]
            season = season_history[(row["year"], row["driver_id"])]
            row.update(
                {
                    "season_starts": len(season),
                    "season_mean_finish": _average(season, "finish_position"),
                    "season_points_per_start": _average(season, "points"),
                    "driver_prior_starts": len(driver),
                    "driver_prior_mean_finish": _average(driver, "finish_position"),
                    "driver_recent3_finish": _average(driver[-3:], "finish_position"),
                    "driver_prior_mean_net_gain": _average(driver, "net_gain"),
                    "driver_prior_dnf_rate": _average(driver, "dnf"),
                    "driver_prior_stop_mean": _average(driver, "pit_stop_count"),
                    "driver_prior_first_stop_ratio": _median(driver, "first_stop_ratio"),
                    "team_prior_starts": len(team),
                    "team_prior_mean_finish": _average(team, "finish_position"),
                    "team_prior_mean_net_gain": _average(team, "net_gain"),
                    "team_prior_dnf_rate": _average(team, "dnf"),
                    "team_prior_stop_mean": _average(team, "pit_stop_count"),
                    "circuit_prior_races": len({item["event_key"] for item in circuit}),
                    "circuit_prior_stop_mean": _average(circuit, "pit_stop_count"),
                    "circuit_prior_first_stop_ratio": _median(circuit, "first_stop_ratio"),
                }
            )
            output.append(row)
        # Labels become history only after every row for this event has been featurized.
        for original in event_rows:
            historical = dict(original)
            historical["net_gain"] = original["grid"] - original["finish_position"]
            driver_history[original["driver_id"]].append(historical)
            team_history[original["constructor_id"]].append(historical)
            circuit_history[original["circuit_id"]].append(historical)
            season_history[(original["year"], original["driver_id"])].append(historical)
    return output


def leakage_audit(rows: list[dict[str, Any]]) -> dict[str, Any]:
    forbidden = sorted(set(MODEL_FEATURES) & RACE_DERIVED_FORBIDDEN_FEATURES)
    missing = sorted(set(MODEL_FEATURES) - set(rows[0])) if rows else list(MODEL_FEATURES)
    chronological_violations = 0
    by_driver: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_driver[row["driver_id"]].append(row)
    for history in by_driver.values():
        history.sort(key=lambda item: item["event_date"])
        for index, row in enumerate(history):
            if row["driver_prior_starts"] > index:
                chronological_violations += 1
    return {
        "passed": not forbidden and not missing and chronological_violations == 0,
        "forbidden_model_features": forbidden,
        "missing_model_features": missing,
        "chronological_violations": chronological_violations,
        "boundary": "target-race information available before scheduled race start",
    }


def records_matrix(rows: list[dict[str, Any]]) -> list[list[Any]]:
    return [[row.get(feature) for feature in MODEL_FEATURES] for row in rows]


def preprocessor(*, dense: bool = False) -> ColumnTransformer:
    numeric = make_pipeline(SimpleImputer(strategy="median"), StandardScaler())
    categorical = make_pipeline(
        SimpleImputer(strategy="most_frequent"),
        OneHotEncoder(handle_unknown="ignore", sparse_output=not dense),
    )
    return ColumnTransformer(
        [
            ("numeric", numeric, list(range(len(NUMERIC_FEATURES)))),
            (
                "categorical",
                categorical,
                list(range(len(NUMERIC_FEATURES), len(MODEL_FEATURES))),
            ),
        ]
    )


def split_rows(rows: list[dict[str, Any]]) -> tuple[list, list, list]:
    return (
        [row for row in rows if row["year"] in TRAIN_YEARS],
        [row for row in rows if row["year"] in DEVELOPMENT_YEARS],
        [row for row in rows if row["year"] in HOLDOUT_YEARS],
    )


def assign_event_ranks(rows: list[dict[str, Any]], scores: Iterable[float]) -> list[int]:
    grouped: dict[str, list[tuple[int, float, int]]] = defaultdict(list)
    for index, (row, score) in enumerate(zip(rows, scores, strict=True)):
        grouped[row["event_key"]].append((index, float(score), row["grid"]))
    output = [0] * len(rows)
    for entries in grouped.values():
        for rank, (index, _, _) in enumerate(
            sorted(entries, key=lambda item: (item[1], item[2])), 1
        ):
            output[index] = rank
    return output


def pairwise_accuracy(rows: list[dict[str, Any]], predicted: list[int]) -> float:
    correct = total = 0
    by_event: dict[str, list[int]] = defaultdict(list)
    for index, row in enumerate(rows):
        by_event[row["event_key"]].append(index)
    for indices in by_event.values():
        for offset, left in enumerate(indices):
            for right in indices[offset + 1 :]:
                actual = rows[left]["finish_position"] < rows[right]["finish_position"]
                expected = predicted[left] < predicted[right]
                correct += actual == expected
                total += 1
    return correct / total if total else float("nan")


def rank_correlation(rows: list[dict[str, Any]], predicted: list[int]) -> float:
    values = []
    by_event: dict[str, list[int]] = defaultdict(list)
    for index, row in enumerate(rows):
        by_event[row["event_key"]].append(index)
    for indices in by_event.values():
        actual = np.asarray([rows[index]["finish_position"] for index in indices], dtype=float)
        estimate = np.asarray([predicted[index] for index in indices], dtype=float)
        if len(indices) > 1 and np.std(actual) and np.std(estimate):
            values.append(float(np.corrcoef(actual, estimate)[0, 1]))
    return mean(values) if values else float("nan")


def rank_metrics(rows: list[dict[str, Any]], predicted: list[int]) -> dict[str, float]:
    actual = [row["finish_position"] for row in rows]
    error = np.abs(np.asarray(actual) - np.asarray(predicted))
    return {
        "mae_positions": float(np.mean(error)),
        "median_absolute_error_positions": float(np.median(error)),
        "mean_event_rank_correlation": rank_correlation(rows, predicted),
        "pairwise_accuracy": pairwise_accuracy(rows, predicted),
    }


def fit_rank_model(train: list, dev: list) -> tuple[dict[str, Any], Any]:
    candidates = []
    for alpha in (0.1, 1.0, 10.0, 100.0):
        model = make_pipeline(preprocessor(), Ridge(alpha=alpha))
        model.fit(records_matrix(train), [row["finish_position"] for row in train])
        predicted = assign_event_ranks(dev, model.predict(records_matrix(dev)))
        candidates.append(
            (rank_metrics(dev, predicted)["mae_positions"], f"ridge_{alpha:g}", model)
        )
    for depth in (2, 3):
        model = make_pipeline(
            preprocessor(dense=True),
            GradientBoostingRegressor(
                random_state=11, n_estimators=100, max_depth=depth, learning_rate=0.04
            ),
        )
        model.fit(records_matrix(train), [row["finish_position"] for row in train])
        predicted = assign_event_ranks(dev, model.predict(records_matrix(dev)))
        candidates.append(
            (rank_metrics(dev, predicted)["mae_positions"], f"gbr_depth_{depth}", model)
        )
    selected = min(candidates, key=lambda item: item[0])
    return {
        "selected": selected[1],
        "development_candidates": {name: score for score, name, _ in candidates},
    }, selected[2]


def classification_metrics(y_true, probabilities, classes) -> dict[str, float]:
    truth = np.asarray(y_true)
    probabilities = np.asarray(probabilities)
    predicted = np.asarray(classes)[np.argmax(probabilities, axis=1)]
    encoded = np.column_stack([(truth == value).astype(float) for value in classes])
    brier = float(np.mean(np.sum((probabilities - encoded) ** 2, axis=1)))
    confidence = probabilities.max(axis=1)
    correct = (predicted == truth).astype(float)
    ece = 0.0
    for lower, upper in zip(np.linspace(0, 1, 6)[:-1], np.linspace(0, 1, 6)[1:], strict=True):
        selected = (confidence >= lower) & (
            confidence <= upper if upper == 1 else confidence < upper
        )
        if selected.any():
            ece += selected.mean() * abs(
                float(correct[selected].mean() - confidence[selected].mean())
            )
    return {
        "log_loss": float(log_loss(truth, probabilities, labels=list(classes))),
        "multiclass_brier": brier,
        "accuracy": float(accuracy_score(truth, predicted)),
        "top_label_ece_5_bin": float(ece),
    }


def smoothed_group_probabilities(
    train: list[dict[str, Any]], test: list[dict[str, Any]], target: str, classes: list[str], group
) -> np.ndarray:
    global_counts = Counter(row[target] for row in train)
    group_counts: dict[Any, Counter] = defaultdict(Counter)
    for row in train:
        group_counts[group(row)][row[target]] += 1
    output = []
    for row in test:
        counts = group_counts.get(group(row), global_counts)
        total = sum(counts.values()) + len(classes)
        output.append([(counts[value] + 1) / total for value in classes])
    return np.asarray(output)


def fit_classifier(train, dev, target: str) -> tuple[dict[str, Any], Any]:
    candidates = []
    for c_value in (0.1, 1.0, 10.0):
        model = make_pipeline(
            preprocessor(),
            LogisticRegression(C=c_value, max_iter=2000, class_weight="balanced"),
        )
        model.fit(records_matrix(train), [row[target] for row in train])
        probabilities = model.predict_proba(records_matrix(dev))
        metric = classification_metrics(
            [row[target] for row in dev], probabilities, model.classes_
        )["log_loss"]
        candidates.append((metric, f"logistic_c_{c_value:g}", model))
    selected = min(candidates, key=lambda item: item[0])
    return {
        "selected": selected[1],
        "development_candidates": {name: score for score, name, _ in candidates},
    }, selected[2]


def evaluate_finish_rank(train, dev, holdout) -> dict[str, Any]:
    selection, model = fit_rank_model(train, dev)
    dev_prediction = assign_event_ranks(dev, model.predict(records_matrix(dev)))
    dev_baseline = assign_event_ranks(dev, [row["grid"] for row in dev])
    dev_recent = assign_event_ranks(
        dev, [row["driver_recent3_finish"] or row["grid"] for row in dev]
    )
    dev_season = assign_event_ranks(
        dev,
        [
            row["season_mean_finish"] or row["driver_prior_mean_finish"] or row["grid"]
            for row in dev
        ],
    )
    # Freeze selection, then use all pre-holdout information for the final fit.
    train_dev = train + dev
    name = selection["selected"]
    if name.startswith("ridge"):
        alpha = float(name.rsplit("_", 1)[1])
        final_model = make_pipeline(preprocessor(), Ridge(alpha=alpha))
    else:
        depth = int(name.rsplit("_", 1)[1])
        final_model = make_pipeline(
            preprocessor(dense=True),
            GradientBoostingRegressor(
                random_state=11, n_estimators=100, max_depth=depth, learning_rate=0.04
            ),
        )
    final_model.fit(records_matrix(train_dev), [row["finish_position"] for row in train_dev])
    holdout_prediction = assign_event_ranks(holdout, final_model.predict(records_matrix(holdout)))
    holdout_baseline = assign_event_ranks(holdout, [row["grid"] for row in holdout])
    holdout_recent = assign_event_ranks(
        holdout, [row["driver_recent3_finish"] or row["grid"] for row in holdout]
    )
    holdout_season = assign_event_ranks(
        holdout,
        [
            row["season_mean_finish"] or row["driver_prior_mean_finish"] or row["grid"]
            for row in holdout
        ],
    )
    dev_error = np.abs(
        np.asarray([row["finish_position"] for row in dev]) - np.asarray(dev_prediction)
    )
    interval = float(np.quantile(dev_error, 0.8, method="higher"))
    holdout_error = np.abs(
        np.asarray([row["finish_position"] for row in holdout]) - np.asarray(holdout_prediction)
    )
    selection.update(
        {
            "sample_count": {"train": len(train), "development": len(dev), "holdout": len(holdout)},
            "development": {
                "starting_grid_baseline": rank_metrics(dev, dev_baseline),
                "recent_three_race_baseline": rank_metrics(dev, dev_recent),
                "season_form_baseline": rank_metrics(dev, dev_season),
                "model": rank_metrics(dev, dev_prediction),
            },
            "holdout": {
                "starting_grid_baseline": rank_metrics(holdout, holdout_baseline),
                "recent_three_race_baseline": rank_metrics(holdout, holdout_recent),
                "season_form_baseline": rank_metrics(holdout, holdout_season),
                "model": rank_metrics(holdout, holdout_prediction),
            },
            "uncertainty": {
                "development_calibrated_80pct_half_width_positions": interval,
                "holdout_coverage": float(np.mean(holdout_error <= interval)),
            },
        }
    )
    return selection


def finish_band(position: int) -> str:
    return "TOP_5" if position <= 5 else "MIDFIELD_6_15" if position <= 15 else "BACK_16_PLUS"


def evaluate_classification_target(
    train,
    dev,
    holdout,
    target: str,
    baseline_group,
) -> dict[str, Any]:
    selection, model = fit_classifier(train, dev, target)
    classes = list(model.classes_)
    dev_probability = model.predict_proba(records_matrix(dev))
    dev_baseline = smoothed_group_probabilities(train, dev, target, classes, baseline_group)
    train_dev = train + dev
    c_value = float(selection["selected"].rsplit("_", 1)[1])
    final_model = make_pipeline(
        preprocessor(),
        LogisticRegression(C=c_value, max_iter=2000, class_weight="balanced"),
    )
    final_model.fit(records_matrix(train_dev), [row[target] for row in train_dev])
    final_classes = list(final_model.classes_)
    holdout_probability = final_model.predict_proba(records_matrix(holdout))
    holdout_baseline = smoothed_group_probabilities(
        train_dev, holdout, target, final_classes, baseline_group
    )
    selection.update(
        {
            "sample_count": {"train": len(train), "development": len(dev), "holdout": len(holdout)},
            "class_counts": {
                "train": dict(Counter(row[target] for row in train)),
                "development": dict(Counter(row[target] for row in dev)),
                "holdout": dict(Counter(row[target] for row in holdout)),
            },
            "development": {
                "baseline": classification_metrics(
                    [row[target] for row in dev], dev_baseline, classes
                ),
                "model": classification_metrics(
                    [row[target] for row in dev], dev_probability, classes
                ),
            },
            "holdout": {
                "baseline": classification_metrics(
                    [row[target] for row in holdout], holdout_baseline, final_classes
                ),
                "model": classification_metrics(
                    [row[target] for row in holdout], holdout_probability, final_classes
                ),
            },
        }
    )
    return selection


def circuit_median_predict(train, test, target: str) -> list[float]:
    global_value = median(row[target] for row in train)
    by_circuit: dict[str, list[float]] = defaultdict(list)
    for row in train:
        by_circuit[row["circuit_id"]].append(row[target])
    return [
        median(by_circuit[row["circuit_id"]]) if by_circuit.get(row["circuit_id"]) else global_value
        for row in test
    ]


def regression_metrics(actual, predicted) -> dict[str, float]:
    error = np.abs(np.asarray(actual) - np.asarray(predicted))
    return {
        "mae_laps": float(np.mean(error)),
        "median_absolute_error_laps": float(np.median(error)),
        "within_3_laps": float(np.mean(error <= 3)),
        "within_5_laps": float(np.mean(error <= 5)),
    }


def evaluate_first_stop(train, dev, holdout) -> dict[str, Any]:
    candidates = []
    for alpha in (0.1, 1.0, 10.0, 100.0):
        model = make_pipeline(preprocessor(), Ridge(alpha=alpha))
        model.fit(records_matrix(train), [row["first_stop_ratio"] for row in train])
        predicted = model.predict(records_matrix(dev)) * np.asarray(
            [row["race_laps"] for row in dev]
        )
        candidates.append(
            (
                mean_absolute_error([row["first_stop_lap"] for row in dev], predicted),
                f"ridge_{alpha:g}",
                model,
            )
        )
    for depth in (2, 3):
        model = make_pipeline(
            preprocessor(dense=True),
            GradientBoostingRegressor(
                random_state=11, n_estimators=100, max_depth=depth, learning_rate=0.04
            ),
        )
        model.fit(records_matrix(train), [row["first_stop_ratio"] for row in train])
        predicted = model.predict(records_matrix(dev)) * np.asarray(
            [row["race_laps"] for row in dev]
        )
        candidates.append(
            (
                mean_absolute_error([row["first_stop_lap"] for row in dev], predicted),
                f"gbr_depth_{depth}",
                model,
            )
        )
    _, selected_name, selected_model = min(candidates, key=lambda item: item[0])
    dev_prediction = selected_model.predict(records_matrix(dev)) * np.asarray(
        [row["race_laps"] for row in dev]
    )
    dev_baseline = circuit_median_predict(train, dev, "first_stop_lap")
    dev_error = np.abs(np.asarray([row["first_stop_lap"] for row in dev]) - dev_prediction)
    interval = float(np.quantile(dev_error, 0.8, method="higher"))

    train_dev = train + dev
    if selected_name.startswith("ridge"):
        model = make_pipeline(preprocessor(), Ridge(alpha=float(selected_name.rsplit("_", 1)[1])))
    else:
        model = make_pipeline(
            preprocessor(dense=True),
            GradientBoostingRegressor(
                random_state=11,
                n_estimators=100,
                max_depth=int(selected_name.rsplit("_", 1)[1]),
                learning_rate=0.04,
            ),
        )
    model.fit(records_matrix(train_dev), [row["first_stop_ratio"] for row in train_dev])
    holdout_prediction = model.predict(records_matrix(holdout)) * np.asarray(
        [row["race_laps"] for row in holdout]
    )
    holdout_actual = [row["first_stop_lap"] for row in holdout]
    holdout_baseline = circuit_median_predict(train_dev, holdout, "first_stop_lap")
    holdout_error = np.abs(np.asarray(holdout_actual) - holdout_prediction)
    return {
        "selected": selected_name,
        "development_candidates": {name: float(score) for score, name, _ in candidates},
        "sample_count": {"train": len(train), "development": len(dev), "holdout": len(holdout)},
        "coverage_of_all_classified_rows": {
            "train": None,
            "development": None,
            "holdout": None,
        },
        "development": {
            "circuit_median_baseline": regression_metrics(
                [row["first_stop_lap"] for row in dev], dev_baseline
            ),
            "model": regression_metrics([row["first_stop_lap"] for row in dev], dev_prediction),
        },
        "holdout": {
            "circuit_median_baseline": regression_metrics(holdout_actual, holdout_baseline),
            "model": regression_metrics(holdout_actual, holdout_prediction),
        },
        "uncertainty": {
            "development_calibrated_80pct_half_width_laps": interval,
            "holdout_coverage": float(np.mean(holdout_error <= interval)),
        },
    }


def failure_slices(rows, predicted_ranks) -> dict[str, Any]:
    errors = [
        abs(row["finish_position"] - prediction)
        for row, prediction in zip(rows, predicted_ranks, strict=True)
    ]

    def slice_metric(predicate):
        selected = [error for row, error in zip(rows, errors, strict=True) if predicate(row)]
        return {"count": len(selected), "mae": mean(selected) if selected else None}

    events = {}
    for event_key in sorted({row["event_key"] for row in rows}):
        indices = [index for index, row in enumerate(rows) if row["event_key"] == event_key]
        event_errors = [errors[index] for index in indices]
        events[event_key] = {
            "circuit_id": rows[indices[0]]["circuit_id"],
            "drivers": len(indices),
            "mae": mean(event_errors),
            "circuit_prior_races": rows[indices[0]]["circuit_prior_races"],
        }
    return {
        "dnf": slice_metric(lambda row: row["dnf"]),
        "classified": slice_metric(lambda row: row["classified"]),
        "front_grid_1_5": slice_metric(lambda row: row["grid"] <= 5),
        "back_grid_16_plus": slice_metric(lambda row: row["grid"] >= 16),
        "grid_penalty_or_gain": slice_metric(lambda row: row["grid_penalty_delta"] != 0),
        "circuit_prior_available": slice_metric(lambda row: row["circuit_prior_races"] > 0),
        "circuit_prior_unavailable": slice_metric(lambda row: row["circuit_prior_races"] == 0),
        "by_event": events,
    }


def run_research(rows: list[dict[str, Any]]) -> dict[str, Any]:
    train, dev, holdout = split_rows(rows)
    for row in rows:
        row["finish_band"] = finish_band(row["finish_position"])
        row["stop_count_class"] = (
            str(min(row["pit_stop_count"], 3)) if row["pit_stop_count"] < 3 else "3_PLUS"
        )

    finish_rank = evaluate_finish_rank(train, dev, holdout)
    finish_band_result = evaluate_classification_target(
        train,
        dev,
        holdout,
        "finish_band",
        lambda row: (
            "1_5"
            if row["grid"] <= 5
            else "6_10"
            if row["grid"] <= 10
            else "11_15"
            if row["grid"] <= 15
            else "16_PLUS"
        ),
    )

    classified = [row for row in rows if row["classified"]]
    stop_train, stop_dev, stop_holdout = split_rows(classified)
    stop_count = evaluate_classification_target(
        stop_train,
        stop_dev,
        stop_holdout,
        "stop_count_class",
        lambda row: row["circuit_id"],
    )

    first_stop_rows = [row for row in classified if row["first_stop_lap"] is not None]
    first_train, first_dev, first_holdout = split_rows(first_stop_rows)
    first_stop = evaluate_first_stop(first_train, first_dev, first_holdout)
    classified_counts = {
        "train": len(stop_train),
        "development": len(stop_dev),
        "holdout": len(stop_holdout),
    }
    first_stop["coverage_of_all_classified_rows"] = {
        split: first_stop["sample_count"][split] / count if count else 0
        for split, count in classified_counts.items()
    }

    family_rows = [row for row in classified if row["strategy_family"] is not None]
    family_train, family_dev, family_holdout = split_rows(family_rows)
    strategy_result = evaluate_classification_target(
        family_train,
        family_dev,
        family_holdout,
        "strategy_family",
        lambda row: row["circuit_id"],
    )

    dnf_rows = [dict(row, dnf_class="DNF" if row["dnf"] else "CLASSIFIED") for row in rows]
    dnf_train, dnf_dev, dnf_holdout = split_rows(dnf_rows)
    dnf_result = evaluate_classification_target(
        dnf_train,
        dnf_dev,
        dnf_holdout,
        "dnf_class",
        lambda row: row["circuit_id"],
    )

    # Refit the selected rank model only to produce diagnostic holdout slices.
    selection, _ = fit_rank_model(train, dev)
    name = selection["selected"]
    if name.startswith("ridge"):
        diagnostic = make_pipeline(preprocessor(), Ridge(alpha=float(name.rsplit("_", 1)[1])))
    else:
        diagnostic = make_pipeline(
            preprocessor(dense=True),
            GradientBoostingRegressor(
                random_state=11,
                n_estimators=100,
                max_depth=int(name.rsplit("_", 1)[1]),
                learning_rate=0.04,
            ),
        )
    diagnostic.fit(records_matrix(train + dev), [row["finish_position"] for row in train + dev])
    diagnostic_ranks = assign_event_ranks(holdout, diagnostic.predict(records_matrix(holdout)))

    events = Counter((row["year"], row["event_key"]) for row in rows)
    split_counts = {"train": len(train), "development": len(dev), "holdout": len(holdout)}
    feature_coverage = {
        feature: sum(row.get(feature) is not None for row in rows) / len(rows)
        for feature in (
            "grid",
            "qualifying_position",
            "qualifying_gap_seconds",
            "grid_penalty_delta",
        )
    }
    return {
        "phase": "11A",
        "status": "RESEARCH_ONLY",
        "architecture_constraint": "No target-race laps and no Phase 5 tactical-kernel rollout",
        "dataset": {
            "events": {
                "train": len({row["event_key"] for row in train}),
                "development": len({row["event_key"] for row in dev}),
                "holdout": len({row["event_key"] for row in holdout}),
                "total": len(events),
            },
            "driver_rows": {
                "train": len(train),
                "development": len(dev),
                "holdout": len(holdout),
                "total": len(rows),
            },
            "years": {"train": "2021-2023", "development": "2024", "holdout": "2025"},
            "holdout_policy": "Model/parameter choice used development only; 2025 was not used in selection and was evaluated after choices were frozen.",
            "feature_coverage": feature_coverage,
            "target_coverage": {
                "finishing_rank_and_band": {split: 1.0 for split in split_counts},
                "incident_survival": {split: 1.0 for split in split_counts},
                "classified_pit_targets": {
                    split: classified_counts[split] / split_counts[split] for split in split_counts
                },
            },
        },
        "features": {"numeric": list(NUMERIC_FEATURES), "categorical": list(CATEGORICAL_FEATURES)},
        "leakage_audit": leakage_audit(rows),
        "targets": {
            "finishing_rank": finish_rank,
            "finishing_band": finish_band_result,
            "pit_stop_count_classified_finishers": stop_count,
            "first_stop_lap_classified_finishers": first_stop,
            "strategy_family_classified_finishers": strategy_result,
            "incident_survival": dnf_result,
        },
        "holdout_failure_slices": failure_slices(holdout, diagnostic_ranks),
    }


DATA_INVENTORY = [
    ("Starting grid", "AVAILABLE PRE-RACE", "Jolpica race entry; includes applied grid changes."),
    (
        "Qualifying position/Q1-Q2-Q3",
        "AVAILABLE PRE-RACE",
        "Jolpica qualifying classification and recorded session times.",
    ),
    ("Driver/team/circuit", "AVAILABLE PRE-RACE", "Calendar, roster and qualifying metadata."),
    (
        "Prior races / season-to-date",
        "AVAILABLE PRE-RACE",
        "Chronological aggregates built only after each earlier event.",
    ),
    (
        "Prior-season circuit history",
        "AVAILABLE PRE-RACE",
        "Earlier event labels at the same circuit only.",
    ),
    (
        "Grid penalties",
        "AVAILABLE PRE-RACE",
        "Final grid vs qualifying position delta; penalty reason is not consistently available.",
    ),
    (
        "Practice long runs",
        "AVAILABLE PRE-RACE",
        "FastF1 sessions exist, but coverage/clean-run normalization is not production-ready; excluded here.",
    ),
    (
        "Forecast weather",
        "UNAVAILABLE",
        "No historically archived point-in-time forecast in current providers.",
    ),
    (
        "Race-session weather",
        "AVAILABLE BUT LEAKY",
        "FastF1 race weather is observed during/after the target race.",
    ),
    (
        "Per-driver tyre allocation",
        "UNAVAILABLE",
        "Not exposed consistently by the inspected provider interfaces.",
    ),
    (
        "Historical stint patterns",
        "AVAILABLE PRE-RACE",
        "Only aggregates from strictly earlier races; target-race stints are labels.",
    ),
    (
        "Target race pace/tyres/pits/incidents",
        "AVAILABLE ONLY AFTER RACE START",
        "Labels only; prohibited from model features.",
    ),
    ("Final result/status", "AVAILABLE BUT LEAKY", "Target labels only."),
]


def better(model: dict[str, Any], baseline: dict[str, Any], metric: str, lower=True) -> bool:
    return model[metric] < baseline[metric] if lower else model[metric] > baseline[metric]


def recommendation(result: dict[str, Any]) -> tuple[str, list[str]]:
    targets = result["targets"]
    winners = []
    rank = targets["finishing_rank"]
    if all(
        better(rank[split]["model"], rank[split]["starting_grid_baseline"], "mae_positions")
        for split in ("development", "holdout")
    ):
        winners.append("finishing_rank")
    for target in (
        "finishing_band",
        "pit_stop_count_classified_finishers",
        "strategy_family_classified_finishers",
        "incident_survival",
    ):
        if all(
            better(targets[target][split]["model"], targets[target][split]["baseline"], metric)
            for split in ("development", "holdout")
            for metric in ("log_loss", "multiclass_brier")
        ):
            winners.append(target)
    first_stop = targets["first_stop_lap_classified_finishers"]
    if all(
        better(
            first_stop[split]["model"],
            first_stop[split]["circuit_median_baseline"],
            "mae_laps",
        )
        for split in ("development", "holdout")
    ):
        winners.append("first_stop_lap_classified_finishers")
    return ("GO" if winners else "NO_GO"), winners


def render_report(result: dict[str, Any], runtime_seconds: float) -> str:
    decision, winners = recommendation(result)
    dataset = result["dataset"]
    targets = result["targets"]

    def metric(target, split, side, key):
        return targets[target][split][side][key]

    lines = [
        "# Phase 11A — Pre-Race Simulation Research",
        "",
        "**STATUS: COMPLETE — RESEARCH ONLY**",
        "",
        "No production service, API, frontend, strategy policy, or tactical-kernel code was changed.",
        "",
        "## Pre-race information boundary",
        "",
        "The cutoff is the scheduled target-race start. Features may use final grid/qualifying metadata and outcomes from strictly earlier events. Target-race laps, stints, stops, weather, incidents, pace and results are labels only and never enter the feature matrix.",
        "",
        "## Data inventory",
        "",
        "| Feature | Classification | Finding |",
        "| --- | --- | --- |",
    ]
    lines.extend(f"| {name} | {state} | {note} |" for name, state, note in DATA_INVENTORY)
    lines.extend(
        [
            "",
            "## Chronological dataset",
            "",
            f"- Training: 2021–2023, {dataset['events']['train']} races / {dataset['driver_rows']['train']} driver-race rows.",
            f"- Development: 2024, {dataset['events']['development']} races / {dataset['driver_rows']['development']} rows.",
            f"- Untouched holdout: 2025, {dataset['events']['holdout']} races / {dataset['driver_rows']['holdout']} rows.",
            "- Candidate selection used development only. Selected candidates were refit on 2021–2024 before final 2025 evaluation.",
            "- These are the repository's 35 pre-existing evaluation archives, not every race on each calendar. That limits coverage claims and makes this a feasibility screen rather than production validation.",
            f"- Grid coverage is {dataset['feature_coverage']['grid']:.1%}; qualifying-position coverage is {dataset['feature_coverage']['qualifying_position']:.1%}; same-phase qualifying-gap coverage is {dataset['feature_coverage']['qualifying_gap_seconds']:.1%}.",
            f"- Classified-finisher pit targets cover {dataset['target_coverage']['classified_pit_targets']['train']:.1%} / {dataset['target_coverage']['classified_pit_targets']['development']:.1%} / {dataset['target_coverage']['classified_pit_targets']['holdout']:.1%} of train/development/holdout rows.",
            "",
            "## Targets, baselines and models",
            "",
            "- Finishing rank: event-level re-ranking of expected finish scores. Baselines are starting grid, recent-three-race ordering and season-form ordering; candidates are Ridge and shallow gradient boosting. This never emits duplicate ranks.",
            "- Finishing band: TOP 5 / positions 6–15 / 16+. Baseline is the smoothed historical distribution for the corresponding grid band; candidate is multinomial logistic regression.",
            "- Pit-stop count: 1 / 2 / 3+ among classified finishers. Baseline is the circuit historical distribution; candidate is multinomial logistic regression. No classified zero-stop example existed in this sample, so zero-stop probability is not estimable here.",
            "- First stop: lap and broad uncertainty window among classified finishers. Baseline is circuit historical median; candidates are Ridge and shallow gradient boosting on normalized race distance.",
            "- Strategy family: one-stop long/other and multi-stop short/other, using fixed first-stint fractions. Baseline is circuit historical mode/distribution; candidate is multinomial logistic regression.",
            "- Incident/survival: classified versus DNF. Baseline is smoothed circuit history; candidate is logistic regression. This is deliberately separate from pace/rank.",
            "",
            "## Results",
            "",
            "| Target | Development baseline → model | Holdout baseline → model | Result |",
            "| --- | --- | --- | --- |",
            f"| Finishing rank | {metric('finishing_rank', 'development', 'starting_grid_baseline', 'mae_positions'):.3f} → {metric('finishing_rank', 'development', 'model', 'mae_positions'):.3f} position MAE | {metric('finishing_rank', 'holdout', 'starting_grid_baseline', 'mae_positions'):.3f} → {metric('finishing_rank', 'holdout', 'model', 'mae_positions'):.3f} | {'beats baseline' if 'finishing_rank' in winners else 'baseline wins'} |",
            f"| Finishing band | {metric('finishing_band', 'development', 'baseline', 'log_loss'):.3f} → {metric('finishing_band', 'development', 'model', 'log_loss'):.3f} log loss | {metric('finishing_band', 'holdout', 'baseline', 'log_loss'):.3f} → {metric('finishing_band', 'holdout', 'model', 'log_loss'):.3f} | {'beats baseline' if 'finishing_band' in winners else 'baseline wins'} |",
            f"| Pit-stop count (classified) | {metric('pit_stop_count_classified_finishers', 'development', 'baseline', 'log_loss'):.3f} → {metric('pit_stop_count_classified_finishers', 'development', 'model', 'log_loss'):.3f} | {metric('pit_stop_count_classified_finishers', 'holdout', 'baseline', 'log_loss'):.3f} → {metric('pit_stop_count_classified_finishers', 'holdout', 'model', 'log_loss'):.3f} | {'beats baseline' if 'pit_stop_count_classified_finishers' in winners else 'baseline wins'} |",
            f"| First-stop lap (classified) | {metric('first_stop_lap_classified_finishers', 'development', 'circuit_median_baseline', 'mae_laps'):.3f} → {metric('first_stop_lap_classified_finishers', 'development', 'model', 'mae_laps'):.3f} lap MAE | {metric('first_stop_lap_classified_finishers', 'holdout', 'circuit_median_baseline', 'mae_laps'):.3f} → {metric('first_stop_lap_classified_finishers', 'holdout', 'model', 'mae_laps'):.3f} | {'beats baseline' if 'first_stop_lap_classified_finishers' in winners else 'baseline wins'} |",
            f"| Strategy family (classified) | {metric('strategy_family_classified_finishers', 'development', 'baseline', 'log_loss'):.3f} → {metric('strategy_family_classified_finishers', 'development', 'model', 'log_loss'):.3f} | {metric('strategy_family_classified_finishers', 'holdout', 'baseline', 'log_loss'):.3f} → {metric('strategy_family_classified_finishers', 'holdout', 'model', 'log_loss'):.3f} | {'beats baseline' if 'strategy_family_classified_finishers' in winners else 'baseline wins'} |",
            f"| Incident/survival | {metric('incident_survival', 'development', 'baseline', 'log_loss'):.3f} → {metric('incident_survival', 'development', 'model', 'log_loss'):.3f} | {metric('incident_survival', 'holdout', 'baseline', 'log_loss'):.3f} → {metric('incident_survival', 'holdout', 'model', 'log_loss'):.3f} | {'beats baseline' if 'incident_survival' in winners else 'baseline wins'} |",
            "",
            "Full metrics, class counts, Brier scores, calibration error, ranking correlation, pairwise accuracy, intervals and coverage are in `docs/phase11a-pre-race-research.json`.",
            "",
            "## Leakage and failure analysis",
            "",
            f"- Leakage audit: {'PASS' if result['leakage_audit']['passed'] else 'FAIL'}; forbidden feature intersections: {result['leakage_audit']['forbidden_model_features'] or 'none'}; chronological violations: {result['leakage_audit']['chronological_violations']}.",
            "- DNFs, safety cars, red flags, weather changes and penalties remain irreducible/unmodelled event uncertainty. Incident probability is evaluated separately rather than being treated as deterministic pace.",
            "- Circuit identity is sparse: several holdout circuits have only one or no earlier cached race, so circuit baselines frequently fall back to the global historical distribution.",
            f"- Holdout rank-model circuit slice: {result['holdout_failure_slices']['circuit_prior_available']['count']} rows with prior-circuit evidence at {result['holdout_failure_slices']['circuit_prior_available']['mae']:.3f} position MAE; {result['holdout_failure_slices']['circuit_prior_unavailable']['count']} rows without it at {result['holdout_failure_slices']['circuit_prior_unavailable']['mae']:.3f} MAE.",
            "- Stop targets are restricted to classified finishers; early retirements mechanically truncate strategy and would confound pit-behaviour prediction.",
            "- Qualifying gap compares a driver only with the fastest time in the same reached qualifying phase; it is not treated as a cross-session pace delta.",
            "- Jolpica's `Lapped` status was explicitly normalized as classified across every split. A regression test protects that provider semantic; no holdout-driven model or parameter change was made.",
            "",
            "## Decision",
            "",
            f"**{decision.replace('_', '-')} for Phase 11B.** Components that beat their appropriate baseline on both development and holdout primary criteria: {', '.join(winners) if winners else 'none'}.",
            "",
        ]
    )
    if decision == "GO":
        lines.extend(
            [
                "Evidence supports only a modular pre-race architecture: calibrated broad performance/rank distributions plus separately validated pit-behaviour and survival components, sampled at race level. It must remain independent of the Phase 5 tactical kernel; that kernel may take over only after observed race laps exist.",
                "",
                "Any Phase 11B proposal should include only the winning components, preserve their empirical residual/calibration distributions, and treat race-level incidents as explicit uncertainty rather than deterministic forecasts.",
            ]
        )
    else:
        lines.append(
            "The evidence does not justify a production pre-race simulator. Do not proceed by adding heuristic layers or longer tactical rollouts."
        )
    lines.extend(
        [
            "",
            f"Research runtime: {runtime_seconds:.1f}s (excluding first-time provider metadata download).",
            "",
        ]
    )
    return "\n".join(lines)


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cache-dir", type=Path, default=Path(".cache"))
    parser.add_argument("--rebuild-dataset", action="store_true")
    args = parser.parse_args()
    started = time.perf_counter()
    archives = load_archives(args.cache_dir)
    if not archives:
        raise SystemExit("No cached FastF1 archives were found.")
    metadata = await fetch_metadata(archives, METADATA_CACHE)
    if DATASET_CACHE.exists() and not args.rebuild_dataset:
        rows = json.loads(DATASET_CACHE.read_text(encoding="utf-8"))
    else:
        rows = add_chronological_features(build_label_rows(archives, metadata))
        DATASET_CACHE.write_text(json.dumps(rows, indent=2), encoding="utf-8")
    result = run_research(rows)
    runtime = time.perf_counter() - started
    result["runtime_seconds"] = runtime
    result["recommendation"], result["components_beating_baseline"] = recommendation(result)
    JSON_OUTPUT.write_text(json.dumps(result, indent=2), encoding="utf-8")
    REPORT_OUTPUT.write_text(render_report(result, runtime), encoding="utf-8")
    print(
        json.dumps(
            {
                "recommendation": result["recommendation"],
                "winners": result["components_beating_baseline"],
                "runtime_seconds": runtime,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    asyncio.run(main())
