"""Interpretable causal multi-lap pace evidence with team shrinkage."""

from statistics import median

from f1_pitwall.services.pace import normalized_lap_times
from f1_pitwall.services.traffic import analyze_traffic


def _mad(values, centre):
    return median(abs(value - centre) for value in values) if values else None


def _driver_field_pace(context, driver_id):
    cache = getattr(context, "_predictive_pace_driver_cache", {})
    if driver_id in cache:
        return cache[driver_id]
    driver = context.driver(driver_id)
    points = normalized_lap_times(context, driver_id)
    recent = [value for _, value in points[-3:]]
    stint = [value for _, value in points]
    selected = recent if len(recent) >= 2 else stint
    result = {
        "seconds": median(selected) if selected else None,
        "recent_seconds": median(recent) if recent else None,
        "stint_seconds": median(stint) if stint else None,
        "sample_count": len(selected),
        "lap_numbers": [number for number, _ in points],
        "mad_seconds": _mad(selected, median(selected)) if selected else None,
        "compound": driver.compound,
        "source": "recent_normalized_clean_pace"
        if len(recent) >= 2
        else "current_stint_normalized_clean_pace"
        if stint
        else "insufficient",
    }
    cache[driver_id] = result
    context._predictive_pace_driver_cache = cache
    return result


def _team_prior(context, driver_id):
    driver = context.driver(driver_id)
    if driver.constructor is None:
        return None, [], 0
    teammates = [
        item
        for item in context.state.drivers
        if item.driver.id != driver_id
        and item.constructor is not None
        and item.constructor.id == driver.constructor.id
        and item.status == "active"
    ]
    evidence = [
        (item.driver.id, _driver_field_pace(context, item.driver.id)) for item in teammates
    ]
    usable = [(identity, item) for identity, item in evidence if item["seconds"] is not None]
    values = [item["seconds"] for _, item in usable]
    return (
        median(values) if values else None,
        [identity for identity, _ in usable],
        sum(item["sample_count"] for _, item in usable),
    )


def estimate_field_relative_pace(context, driver_id):
    individual = _driver_field_pace(context, driver_id)
    team_seconds, teammates, team_samples = _team_prior(context, driver_id)
    own = individual["seconds"]
    own_weight = min(3.0, float(individual["sample_count"]))
    team_weight = min(1.0, team_samples / 6) if team_seconds is not None else 0.0
    if own is not None and team_seconds is not None:
        expected = (own * own_weight + team_seconds * team_weight) / (
            own_weight + team_weight
        )
        source = "driver_normalized_pace_shrunk_to_team"
    elif own is not None:
        expected, source = own, individual["source"]
    elif team_seconds is not None:
        expected, source = team_seconds, "team_normalized_pace_fallback"
    else:
        expected, source = 0.0, "field_relative_zero_fallback"
    individual_mad = individual["mad_seconds"] or 0.75
    team_disagreement = (
        abs(own - team_seconds) if own is not None and team_seconds is not None else 0
    )
    uncertainty = max(0.5, individual_mad, team_disagreement * team_weight)
    return {
        "expected_field_relative_pace_seconds": expected,
        "uncertainty_seconds_per_lap": uncertainty,
        "source": source,
        "driver_evidence": individual,
        "team_prior_seconds": team_seconds,
        "team_driver_ids": teammates,
        "team_sample_count": team_samples,
        "traffic_state": analyze_traffic(context, driver_id).status,
    }


def estimate_multi_lap_relative_pace(context, driver_id):
    driver = estimate_field_relative_pace(context, driver_id)
    leader = next((item for item in context.state.drivers if item.position == 1), None)
    if leader is None:
        return {
            "expected_relative_pace_seconds_per_lap": None,
            "uncertainty_seconds_per_lap": None,
            "driver": driver,
            "leader": None,
        }
    reference = estimate_field_relative_pace(context, leader.driver.id)
    return {
        "expected_relative_pace_seconds_per_lap": (
            driver["expected_field_relative_pace_seconds"]
            - reference["expected_field_relative_pace_seconds"]
        ),
        "uncertainty_seconds_per_lap": (
            driver["uncertainty_seconds_per_lap"] ** 2
            + reference["uncertainty_seconds_per_lap"] ** 2
        )
        ** 0.5,
        "driver": driver,
        "leader": reference,
    }
