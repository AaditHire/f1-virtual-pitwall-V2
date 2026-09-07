"""Conservative causal strategy decisions over a three-lap horizon."""

import asyncio
from math import sqrt

from f1_pitwall.domain.strategy import (
    ActionComparison,
    ExcludedDriver,
    FullGridStrategy,
    StrategyAction,
    StrategyDecision,
)
from f1_pitwall.services.analysis_context import AnalysisContext
from f1_pitwall.services.fresh_tyre import estimate_fresh_tyre_delta
from f1_pitwall.services.pace import analyze_tyres, get_recent_pace
from f1_pitwall.services.pair_analysis import calculate_overcut, calculate_undercut
from f1_pitwall.services.pit_analysis import estimate_pit_loss, predict_pit_rejoin
from f1_pitwall.services.traffic import analyze_traffic, blockage_penalty

HORIZON_LAPS = 3
MINIMUM_DECISION_MARGIN_SECONDS = 0.75
PAIR_SIGNAL_CAP_SECONDS = 0.25
TYRE_FORECAST_MAE_SECONDS = 0.506
FRESH_TYRE_MAE_SECONDS = 0.911
REJOIN_POSITION_MAE = 1.013
DRY_COMPOUNDS = ("SOFT", "MEDIUM", "HARD")
WET_COMPOUNDS = ("INTERMEDIATE", "WET")
POINTS = (25, 18, 15, 12, 10, 8, 6, 4, 2, 1)


def _round(value):
    return round(value, 3) if value is not None else None


def _bounded_fresh_delta(value):
    """One-MAE dead band and one-MAE cap keep LOW evidence secondary."""
    if value == 0:
        return 0.0
    sign = 1 if value > 0 else -1
    return sign * min(max(abs(value) - FRESH_TYRE_MAE_SECONDS, 0.0), FRESH_TYRE_MAE_SECONDS)


def _points(position):
    return POINTS[position - 1] if position is not None and 1 <= position <= len(POINTS) else 0


def _objective(driver, field_size):
    position = driver.position
    track_priority = (
        1 + (field_size - position) / max(field_size - 1, 1) if position is not None else 1
    )
    points_now = _points(position)
    points_at_risk = max(0, points_now - _points(position + 1 if position else None))
    risk_weight = track_priority + points_at_risk / max(POINTS)
    return {
        "track_position_priority": _round(track_priority),
        "points_at_current_position": points_now,
        "points_to_next_position": points_at_risk,
        "risk_weight": _round(risk_weight),
        "formula": "1 + normalized track position + adjacent points loss / 25",
    }


def _observed_compounds(context, driver_id):
    observed = {
        s.compound.upper()
        for s in context.race.stints
        if s.compound and s.observed_at <= context.cutoff
    }
    used = {
        s.compound.upper()
        for s in context.race.stints
        if s.driver_id == driver_id and s.compound and s.observed_at <= context.cutoff
    }
    return observed, used


def _pit_compounds(context, driver):
    if not driver.compound:
        return []
    current = driver.compound.upper()
    observed, _ = _observed_compounds(context, driver.driver.id)
    family = DRY_COMPOUNDS if current in DRY_COMPOUNDS else WET_COMPOUNDS
    # Physical set inventory is absent, so use only compounds already observed in the race.
    return [compound for compound in family if compound in observed and compound != current]


def _max_extension(context, driver):
    total = context.state.total_scheduled_laps
    if total is None:
        return HORIZON_LAPS
    remaining = max(0, total - context.state.current_lap)
    current = driver.compound.upper() if driver.compound else None
    _, used = _observed_compounds(context, driver.driver.id)
    if current in DRY_COMPOUNDS and len(used & set(DRY_COMPOUNDS)) < 2:
        remaining = max(0, remaining - 1)
    return min(HORIZON_LAPS, remaining)


def generate_actions(context, driver_id):
    driver = context.driver(driver_id)
    if driver.status != "active":
        return []
    actions = []
    if context.state.track.track_status == "1":
        for compound in _pit_compounds(context, driver):
            actions.append(
                StrategyAction(
                    id=f"PIT_NOW_{compound}", kind="PIT_NOW", compound=compound
                )
            )
    for laps in range(1, _max_extension(context, driver) + 1):
        actions.append(
            StrategyAction(id=f"EXTEND_{laps}", kind="EXTEND", extension_laps=laps)
        )
    return actions


def _current_blockage(context, traffic, pace):
    return blockage_penalty(
        context,
        traffic.ahead_id,
        traffic.gap_ahead,
        pace,
        traffic.status != "UNKNOWN",
    )


def _rejoin_blockage(context, rejoin, fresh_pace):
    return blockage_penalty(
        context,
        rejoin.ahead_id,
        rejoin.gap_ahead,
        fresh_pace,
        rejoin.projected_position is not None,
    )


def _pair_signal(context, driver, pit_loss, kind, compound=None):
    if driver.position is None or driver.position <= 1:
        return 0.0, None
    target = next(
        (d for d in context.state.drivers if d.position == driver.position - 1), None
    )
    if target is None or target.status != "active" or target.lapped:
        return 0.0, None
    result = (
        calculate_undercut(context, driver.driver.id, target.driver.id, pit_loss, compound)
        if kind == "undercut"
        else calculate_overcut(context, driver.driver.id, target.driver.id, pit_loss, compound)
    )
    margin = result.estimated_margin
    contribution = max(-PAIR_SIGNAL_CAP_SECONDS, min(PAIR_SIGNAL_CAP_SECONDS, margin or 0.0))
    return contribution, {
        "kind": kind,
        "target_id": target.driver.id,
        "raw_margin_seconds": _round(margin),
        "opportunity": result.opportunity,
        "confidence": result.confidence,
        "capped_contribution_seconds": _round(contribution),
        "cap_seconds": PAIR_SIGNAL_CAP_SECONDS,
    }


def _evaluate_action(
    context, driver, action, pit_loss, pace, tyres, traffic, objective, evidence_cache
):
    action.expected_rejoin = predict_pit_rejoin(driver.driver.id, context.state, pit_loss)
    action.traffic_status = (
        action.expected_rejoin.traffic if action.kind == "PIT_NOW" else traffic.status
    )
    missing = []
    if pace.seconds is None:
        missing.append("recent clean pace")
    if pit_loss.total_seconds is None:
        missing.append("causal pit-loss estimate")
    if action.expected_rejoin.position_range is None:
        missing.append("usable rejoin geometry")
    if missing:
        action.warnings.append("Cannot score: missing " + ", ".join(missing) + ".")
        action.score_components = {
            "missing_inputs": missing,
            "tyre_model": "zero_slope",
            "diagnostic_slopes_used": False,
            "objective": objective,
        }
        return action

    extension = 0 if action.kind == "PIT_NOW" else action.extension_laps
    fresh_laps = HORIZON_LAPS - extension
    fresh_key = action.compound
    if fresh_key not in evidence_cache["fresh"]:
        evidence_cache["fresh"][fresh_key] = estimate_fresh_tyre_delta(
            context, driver.driver.id, pit_loss, action.compound
        )
    fresh = evidence_cache["fresh"][fresh_key]
    raw_delta = fresh.fresh_tyre_delta or 0.0
    bounded_delta = _bounded_fresh_delta(raw_delta)
    fresh_pace = pace.seconds - bounded_delta
    current_penalty = _current_blockage(context, traffic, pace.seconds)
    rejoin_penalty = _rejoin_blockage(context, action.expected_rejoin, fresh_pace)
    unknown_traffic = sum(value is None for value in (current_penalty, rejoin_penalty))
    current_penalty = current_penalty or 0.0
    rejoin_penalty = rejoin_penalty or 0.0
    expected = (
        pit_loss.total_seconds
        + extension * (pace.seconds + current_penalty)
        + fresh_laps * (fresh_pace + rejoin_penalty)
    )
    # Errors are carried in root-sum-square form; front-running/points exposure scales risk.
    tyre_uncertainty = TYRE_FORECAST_MAE_SECONDS * sqrt(extension)
    fresh_uncertainty = FRESH_TYRE_MAE_SECONDS * sqrt(fresh_laps) if fresh_laps else 0.0
    geometry_delay_uncertainty = TYRE_FORECAST_MAE_SECONDS * sqrt(extension)
    traffic_uncertainty = TYRE_FORECAST_MAE_SECONDS * unknown_traffic
    uncertainty = sqrt(
        tyre_uncertainty**2
        + fresh_uncertainty**2
        + geometry_delay_uncertainty**2
        + traffic_uncertainty**2
    )
    uncertainty_penalty = uncertainty * objective["risk_weight"]
    pair_kind = "undercut" if action.kind == "PIT_NOW" else "overcut"
    pair_key = (pair_kind, action.compound)
    if pair_key not in evidence_cache["pair"]:
        evidence_cache["pair"][pair_key] = _pair_signal(
            context, driver, pit_loss, pair_kind, action.compound
        )
    pair_signal, pair = evidence_cache["pair"][pair_key]
    score = -expected - uncertainty_penalty + pair_signal
    action.estimated_total_time_seconds = _round(expected)
    action.action_score = _round(score)
    action.confidence = "LOW"
    action.score_components = {
        "units": "seconds-equivalent; higher is better",
        "recent_pace_seconds": _round(pace.seconds),
        "horizon_laps": HORIZON_LAPS,
        "extension_laps": extension,
        "fresh_laps": fresh_laps,
        "pit_loss_seconds": _round(pit_loss.total_seconds),
        "current_traffic_seconds_per_lap": _round(current_penalty),
        "rejoin_traffic_seconds_per_lap": _round(rejoin_penalty),
        "fresh_tyre_delta_seconds_per_lap": _round(raw_delta),
        "bounded_fresh_tyre_delta_seconds_per_lap": _round(bounded_delta),
        "fresh_tyre_rule": "one-validation-MAE dead band, then capped at one MAE",
        "fresh_tyre_confidence": fresh.confidence,
        "fresh_tyre_validation_mae_seconds": FRESH_TYRE_MAE_SECONDS,
        "tyre_model": "zero_slope",
        "tyre_forecast": "zero_slope",
        "tyre_forecast_validation_mae_seconds": TYRE_FORECAST_MAE_SECONDS,
        "diagnostic_slopes_used": False,
        "expected_short_horizon_time_seconds": _round(expected),
        "uncertainty_seconds": _round(uncertainty),
        "uncertainty_penalty_seconds": _round(uncertainty_penalty),
        "unknown_traffic_inputs": unknown_traffic,
        "traffic_opportunity_seconds": _round(
            HORIZON_LAPS * (current_penalty - rejoin_penalty)
        ),
        "pair_signal": pair,
        "pair_signal_contribution_seconds": _round(pair_signal),
        "objective": objective,
        "rejoin_position_mae": REJOIN_POSITION_MAE,
    }
    action.warnings.extend(
        [
            "Fresh-tyre evidence is LOW confidence and its held-out error is penalized.",
            "Extension assumes zero relative tyre degradation; diagnostic slopes are ignored.",
            "Delayed-stop rejoin uses current frozen geometry and gains uncertainty with delay.",
        ]
    )
    if pair:
        action.warnings.append("The LOW-confidence pair signal is capped and cannot decide alone.")
    if unknown_traffic:
        action.warnings.append("Missing neighbour pace adds empirical forecast-error uncertainty.")
    return action


def recommend_driver_action(context, driver_id, pit_loss=None):
    driver = context.driver(driver_id)
    decision = StrategyDecision(
        year=context.state.event.year,
        round=context.state.event.round,
        lap=context.state.current_lap,
        cutoff_seconds=context.cutoff,
        driver=driver.driver,
        eligible=driver.status == "active",
        current_position=driver.position,
        current_compound=driver.compound,
        tyre_age=driver.tyre_age,
        minimum_decision_margin=MINIMUM_DECISION_MARGIN_SECONDS,
        data_quality={
            "race_state": driver.quality.model_dump(),
            "missing_inputs": [],
            "horizon_laps": HORIZON_LAPS,
        },
    )
    if driver.status != "active":
        decision.main_risks = [f"Driver status is {driver.status}; no strategy actions generated."]
        return decision

    pit_loss = pit_loss or estimate_pit_loss(context)
    pace = get_recent_pace(context, driver_id)
    tyres = analyze_tyres(context, driver_id)
    rejoin = predict_pit_rejoin(driver_id, context.state, pit_loss)
    traffic = analyze_traffic(context, driver_id, rejoin)
    decision.traffic_status = traffic.status
    decision.expected_rejoin = rejoin
    objective = _objective(driver, len(context.state.drivers))
    evidence_cache = {"fresh": {}, "pair": {}}
    decision.actions = [
        _evaluate_action(
            context,
            driver,
            action,
            pit_loss,
            pace,
            tyres,
            traffic,
            objective,
            evidence_cache,
        )
        for action in generate_actions(context, driver_id)
    ]
    scored = sorted(
        (action for action in decision.actions if action.action_score is not None),
        key=lambda action: action.action_score,
        reverse=True,
    )
    missing = []
    if pace.seconds is None:
        missing.append("recent clean pace")
    if pit_loss.total_seconds is None:
        missing.append("pit loss")
    if rejoin.position_range is None:
        missing.append("rejoin geometry")
    decision.data_quality.update(
        {
            "missing_inputs": missing,
            "recent_pace_confidence": pace.confidence,
            "tyre_forecast_confidence": tyres.confidence,
            "pit_loss_confidence": pit_loss.confidence,
            "rejoin_confidence": rejoin.confidence,
            "traffic_confidence": traffic.confidence,
        }
    )
    if len(scored) < 2 or not all(
        any(action.kind == kind for action in scored) for kind in ("PIT_NOW", "EXTEND")
    ):
        decision.recommended_action = "INSUFFICIENT_DATA"
        decision.main_reasons = [
            "A pit-now and an extension action could not both be scored causally."
        ]
        absent = ", ".join(missing or ["legal pit compound"])
        decision.main_risks = [f"Missing or unsupported inputs: {absent}."]
        return decision

    best_pit = max(
        (action for action in scored if action.kind == "PIT_NOW"),
        key=lambda action: action.action_score,
    )
    best_extend = max(
        (action for action in scored if action.kind == "EXTEND"),
        key=lambda action: action.action_score,
    )
    best, second = sorted(
        (best_pit, best_extend), key=lambda action: action.action_score, reverse=True
    )
    margin = best.action_score - second.action_score
    decision.decision_score = best.action_score
    decision.alternative_score = second.action_score
    decision.decision_margin = _round(margin)
    decision.alternative_action = second.id
    decision.confidence = "LOW"
    if margin < MINIMUM_DECISION_MARGIN_SECONDS:
        decision.recommended_action = "HOLD_NO_CLEAR_ADVANTAGE"
        decision.main_reasons = [
            f"Best and second action differ by only {margin:.2f}s-equivalent.",
            "The margin does not clear the 0.75s minimum decision threshold.",
        ]
    elif best.kind == "PIT_NOW" and (
        best.traffic_status == "UNKNOWN"
        or best.score_components["traffic_opportunity_seconds"]
        < MINIMUM_DECISION_MARGIN_SECONDS
    ):
        decision.recommended_action = "HOLD_NO_CLEAR_ADVANTAGE"
        decision.main_reasons = [
            "PIT_NOW leads numerically, but no measured traffic/rejoin benefit clears "
            "the decision threshold.",
            "LOW-confidence fresh-tyre and pair evidence cannot trigger PIT_NOW alone.",
        ]
    elif best.kind == "PIT_NOW":
        decision.recommended_action = "PIT_NOW"
        decision.recommended_compound = best.compound
        decision.expected_rejoin = best.expected_rejoin
        decision.main_reasons = [
            f"{best.id} leads the next action by {margin:.2f}s-equivalent.",
            f"Current traffic is {traffic.status}; projected rejoin traffic is "
            f"{best.traffic_status}.",
        ]
    else:
        decision.recommended_action = "EXTEND"
        decision.recommended_extension_laps = best.extension_laps
        decision.main_reasons = [
            f"{best.id} leads the next action by {margin:.2f}s-equivalent.",
            "The zero-slope forecast preserves current relative pace over the short extension.",
        ]
    decision.main_risks = [
        "Fresh-tyre effect remains LOW confidence (0.911s held-out MAE).",
        "Rejoin is an instantaneous frozen-gap estimate (about 1 position MAE).",
        "No weather, safety-car, overtaking or whole-race simulation is included.",
    ]
    return decision


def analyze_strategy_all(context):
    pit_loss = estimate_pit_loss(context)
    decisions, excluded = [], []
    for driver in context.state.drivers:
        if driver.status == "active":
            decisions.append(recommend_driver_action(context, driver.driver.id, pit_loss))
        else:
            excluded.append(
                ExcludedDriver(
                    driver=driver.driver,
                    status=driver.status,
                    reason="Driver is not active at the causal cutoff; no actions generated.",
                )
            )
    return FullGridStrategy(
        year=context.state.event.year,
        round=context.state.event.round,
        lap=context.state.current_lap,
        cutoff_seconds=context.cutoff,
        active_count=len(decisions),
        decisions=decisions,
        excluded=excluded,
        warnings=[
            "All decisions share one causal replay prefix and one pit-loss estimate.",
            "Lapped or incomplete-gap active drivers may return INSUFFICIENT_DATA.",
        ],
    )


def compare_actions(context, driver_id):
    decision = recommend_driver_action(context, driver_id)
    return ActionComparison(
        year=decision.year,
        round=decision.round,
        lap=decision.lap,
        driver=decision.driver,
        recommended_action=decision.recommended_action,
        decision_margin=decision.decision_margin,
        minimum_decision_margin=decision.minimum_decision_margin,
        actions=sorted(
            decision.actions,
            key=lambda action: action.action_score if action.action_score is not None else -1e12,
            reverse=True,
        ),
    )


class StrategyService:
    def __init__(self, replay):
        self.replay = replay

    async def _run(self, year, round, lap, operation):
        race = await self.replay.load_race(year, round)
        return await asyncio.to_thread(lambda: operation(AnalysisContext(race, lap)))

    async def recommend_driver_action(self, year, round, lap, driver_id):
        return await self._run(
            year, round, lap, lambda context: recommend_driver_action(context, driver_id)
        )

    async def recommend_full_grid(self, year, round, lap):
        return await self._run(year, round, lap, analyze_strategy_all)

    async def compare_actions(self, year, round, lap, driver_id):
        return await self._run(
            year, round, lap, lambda context: compare_actions(context, driver_id)
        )
