"""Conditional one-lap stop-offset comparisons, never strategy decisions."""

from f1_pitwall.domain.analysis import PairAnalysis
from f1_pitwall.services.fresh_tyre import estimate_fresh_tyre_delta
from f1_pitwall.services.pace import analyze_tyres, get_recent_pace
from f1_pitwall.services.pit_analysis import predict_pit_rejoin
from f1_pitwall.services.traffic import analyze_traffic, blockage_penalty


def calculate_pair(context, driver_id, target_id, pit_loss, kind, new_compound=None):
    driver, target = context.driver(driver_id), context.driver(target_id)
    own_pace = get_recent_pace(context, driver_id)
    target_pace = get_recent_pace(context, target_id)
    fresh_id = driver_id if kind == "undercut" else target_id
    fresh = estimate_fresh_tyre_delta(context, fresh_id, pit_loss, new_compound)
    rejoin = predict_pit_rejoin(fresh_id, context.state, pit_loss)
    traffic = analyze_traffic(context, driver_id)
    tyres = analyze_tyres(context, driver_id)
    gap = (
        driver.gap_to_leader - target.gap_to_leader
        if driver.gap_to_leader is not None and target.gap_to_leader is not None
        else None
    )
    result = PairAnalysis(
        kind=kind,
        driver_id=driver_id,
        target_id=target_id,
        current_gap=gap,
        method="Conditional one-clean-lap stop offset with equal normal-stop costs",
        components={
            "driver_recent_pace": own_pace.seconds,
            "target_recent_pace": target_pace.seconds,
            "driver_degradation": tyres.degradation_sec_per_lap,
            "driver_tyre_confidence": tyres.confidence,
            "driver_recent_relative_pace_delta": tyres.recent_pace_delta,
            "driver_candidate_normalized_slope": tyres.components.get(
                "candidate_normalized_slope_sec_per_lap"
            ),
            "fresh_tyre_evidence": fresh.model_dump(),
            "pit_loss_seconds_each": pit_loss.total_seconds,
            "pit_loss_difference_seconds": 0 if pit_loss.total_seconds is not None else None,
            "rejoin": rejoin.model_dump(),
            "current_traffic": traffic.model_dump(),
            "offset_laps": 1,
            "decision_band_seconds": 0.5,
        },
        fresh_tyre=fresh,
        warnings=[
            "Conditional clean-lap margin, not a probability or a pit recommendation.",
            "Fresh-used difference also contains compound, fuel and traffic effects; "
            "transfer is uncertain.",
            "The out-lap is excluded; first-full-lap warm-up, traffic and unequal stop "
            "execution can still reverse the margin.",
        ],
        conditions_required=[
            "Both cars make equal normal green-flag stops, separated by one racing lap.",
            "Observed fresh-used pace difference transfers to the proposed tyre change.",
            "No additional out-lap/warm-up loss, overtakes, neutralisation "
            "or gap evolution beyond the offset.",
        ],
    )
    if (
        driver_id == target_id
        or gap is None
        or gap < 0
        or any(d.status != "active" or d.lapped for d in (driver, target))
    ):
        result.warnings.append("Requires distinct active same-lap cars with driver behind target.")
        return result
    result.required_gain = gap
    if (
        own_pace.seconds is None
        or target_pace.seconds is None
        or fresh.fresh_tyre_delta is None
        or pit_loss.total_seconds is None
        or rejoin.projected_position is None
    ):
        result.warnings.append(
            "Insufficient current pace, fresh-tyre evidence or complete rejoin geometry."
        )
        return result
    fresh_pace = (
        own_pace.seconds if kind == "undercut" else target_pace.seconds
    ) - fresh.fresh_tyre_delta
    penalty = blockage_penalty(context, rejoin.ahead_id, rejoin.gap_ahead, fresh_pace, True)
    own_penalty = blockage_penalty(
        context, traffic.ahead_id, traffic.gap_ahead, own_pace.seconds, traffic.status != "UNKNOWN"
    )
    if penalty is None or (kind == "overcut" and own_penalty is None):
        result.warnings.append("Missing neighbour pace prevents a traffic-adjusted margin.")
        return result
    result.estimated_fresh_tyre_gain = fresh.fresh_tyre_delta
    result.traffic_penalty = penalty
    result.components.update(
        {
            "estimated_fresh_clean_lap_seconds": fresh_pace,
            "driver_current_traffic_penalty": own_penalty,
        }
    )
    if kind == "undercut":
        result.estimated_margin = target_pace.seconds - fresh_pace - penalty - gap
    else:
        result.estimated_margin = fresh_pace - own_pace.seconds + penalty - own_penalty - gap
    result.opportunity = (
        "YES"
        if result.estimated_margin > 0.5
        else "NO"
        if result.estimated_margin < -0.5
        else "MARGINAL"
    )
    # Transferred clean-lap evidence cannot justify high-confidence out-lap claims.
    result.confidence = "LOW"
    return result


def calculate_undercut(context, attacker, target, pit_loss, new_compound=None):
    return calculate_pair(context, attacker, target, pit_loss, "undercut", new_compound)


def calculate_overcut(context, driver, opponent, pit_loss, new_compound=None):
    return calculate_pair(context, driver, opponent, pit_loss, "overcut", new_compound)
