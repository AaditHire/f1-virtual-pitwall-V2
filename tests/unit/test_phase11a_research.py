from copy import deepcopy

from scripts.research_phase11a import (
    MODEL_FEATURES,
    add_chronological_features,
    is_classified,
    leakage_audit,
    parse_lap_time,
    recommendation,
    strategy_family,
)


def row(event, day, driver, team, finish, grid, *, dnf=False, stops=1, first=0.35):
    return {
        "event_key": event,
        "event_date": day,
        "year": int(day[:4]),
        "round": int(event.rsplit("-", 1)[1]),
        "circuit_id": "test-circuit",
        "driver_id": driver,
        "driver_code": driver.upper(),
        "constructor_id": team,
        "grid": grid,
        "qualifying_position": grid,
        "qualifying_phase": 3,
        "qualifying_gap_seconds": float(grid - 1),
        "grid_penalty_delta": 0,
        "finish_position": finish,
        "classified": not dnf,
        "dnf": dnf,
        "status": "Accident" if dnf else "Finished",
        "points": 25.0 if finish == 1 else 0.0,
        "race_laps": 50,
        "pit_stop_count": stops,
        "first_stop_lap": round(first * 50) if stops else None,
        "first_stop_ratio": first if stops else None,
        "strategy_family": strategy_family(stops, first if stops else None),
        "compound_sequence": ["MEDIUM", "HARD"] if stops else ["HARD"],
    }


def test_target_race_labels_do_not_enter_its_features():
    rows = [
        row("2021-1", "2021-03-28", "a", "one", 1, 2),
        row("2021-1", "2021-03-28", "b", "two", 2, 1),
        row("2022-1", "2022-03-20", "a", "one", 2, 1),
        row("2022-1", "2022-03-20", "b", "two", 1, 2),
    ]
    changed = deepcopy(rows)
    changed[2].update(finish_position=20, dnf=True, classified=False, pit_stop_count=0)
    changed[3].update(finish_position=19, dnf=True, classified=False, pit_stop_count=0)

    original_features = add_chronological_features(rows)
    changed_features = add_chronological_features(changed)
    for index in (2, 3):
        assert {key: original_features[index][key] for key in MODEL_FEATURES} == {
            key: changed_features[index][key] for key in MODEL_FEATURES
        }


def test_same_event_results_do_not_leak_between_drivers():
    rows = [
        row("2021-1", "2021-03-28", "a", "one", 1, 1),
        row("2021-1", "2021-03-28", "b", "one", 20, 20, dnf=True),
    ]
    featured = add_chronological_features(rows)
    assert featured[0]["team_prior_starts"] == 0
    assert featured[1]["team_prior_starts"] == 0
    assert featured[0]["circuit_prior_races"] == 0
    assert featured[1]["circuit_prior_races"] == 0


def test_feature_allowlist_passes_leakage_audit():
    featured = add_chronological_features([row("2021-1", "2021-03-28", "a", "one", 1, 1)])
    result = leakage_audit(featured)
    assert result["passed"] is True
    assert result["forbidden_model_features"] == []
    assert result["chronological_violations"] == 0


def test_small_target_helpers_are_explicit_and_stable():
    assert parse_lap_time("1:23.456") == 83.456
    assert is_classified("Lapped") is True
    assert is_classified("Retired") is False
    assert strategy_family(1, 0.45) == "ONE_STOP_LONG"
    assert strategy_family(2, 0.25) == "MULTI_STOP_SHORT"
    assert strategy_family(0, None) is None


def test_go_gate_rejects_a_holdout_only_improvement():
    classification = {
        "development": {
            "baseline": {"log_loss": 0.5, "multiclass_brier": 0.4},
            "model": {"log_loss": 0.6, "multiclass_brier": 0.5},
        },
        "holdout": {
            "baseline": {"log_loss": 0.6, "multiclass_brier": 0.5},
            "model": {"log_loss": 0.5, "multiclass_brier": 0.4},
        },
    }
    result = {
        "targets": {
            "finishing_rank": {
                "development": {
                    "starting_grid_baseline": {"mae_positions": 2.0},
                    "model": {"mae_positions": 2.1},
                },
                "holdout": {
                    "starting_grid_baseline": {"mae_positions": 3.0},
                    "model": {"mae_positions": 2.9},
                },
            },
            "finishing_band": classification,
            "pit_stop_count_classified_finishers": classification,
            "strategy_family_classified_finishers": classification,
            "incident_survival": classification,
            "first_stop_lap_classified_finishers": {
                "development": {
                    "circuit_median_baseline": {"mae_laps": 8.0},
                    "model": {"mae_laps": 9.0},
                },
                "holdout": {
                    "circuit_median_baseline": {"mae_laps": 9.0},
                    "model": {"mae_laps": 8.0},
                },
            },
        }
    }
    assert recommendation(result) == ("NO_GO", [])
