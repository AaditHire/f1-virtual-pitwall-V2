"""Rolling, observation-reanchored Virtual Pit Wall orchestration."""

from __future__ import annotations

import asyncio
from collections import Counter, defaultdict
from time import perf_counter

from f1_pitwall.domain.paired import PitWindow
from f1_pitwall.domain.pitwall import (
    PitWallAction,
    PitWallAlert,
    PitWallDriver,
    PitWallDriverTimeline,
    PitWallRival,
    PitWallSnapshot,
    PitWallTimeline,
    PitWallTimelineEntry,
)
from f1_pitwall.services.analysis import analyze_driver
from f1_pitwall.services.analysis_context import AnalysisContext
from f1_pitwall.services.paired import EQUIVALENCE_BANDS, evaluate_paired_candidates
from f1_pitwall.services.pit_analysis import estimate_pit_loss
from f1_pitwall.services.strategy import (
    NORMAL_STOP_COOLDOWN_LAPS,
    laps_since_last_pit,
    recommend_driver_action,
)


def _outcome(action, horizon=3):
    return next(row for row in action.outcomes if row.horizon_laps == horizon)


def _relevant_rivals(context, driver):
    rivals = []
    for item in context.state.drivers:
        if item.driver.id == driver.driver.id:
            continue
        position_close = (
            item.position is not None
            and driver.position is not None
            and abs(item.position - driver.position) <= 2
        )
        gap_close = (
            item.gap_to_leader is not None
            and driver.gap_to_leader is not None
            and abs(item.gap_to_leader - driver.gap_to_leader) <= 5
        )
        if not (position_close or gap_close):
            continue
        gap = (
            item.gap_to_leader - driver.gap_to_leader
            if item.gap_to_leader is not None and driver.gap_to_leader is not None
            else None
        )
        pace = (
            item.recent_clean_pace - driver.recent_clean_pace
            if item.recent_clean_pace is not None and driver.recent_clean_pace is not None
            else None
        )
        rivals.append(
            PitWallRival(
                driver=item.driver,
                position=item.position,
                gap_seconds=round(gap, 3) if gap is not None else None,
                relative_pace_seconds_per_lap=round(pace, 3) if pace is not None else None,
                pit_stops_completed=item.pit_stops_completed,
                status=item.status,
            )
        )
    return sorted(rivals, key=lambda row: abs(row.gap_seconds or 99))[:4]


def _alerts(
    driver,
    traffic_status,
    rejoin_traffic,
    rivals,
    recommendation,
    decision_state,
    pit_window_state,
):
    alerts = []
    driver_id = driver.driver.id
    if traffic_status == "CLEAR_AIR":
        alerts.append(
            PitWallAlert(
                kind="CLEAR_AIR_WINDOW",
                driver_id=driver_id,
                detail="Observed local traffic is clear.",
            )
        )
    if pit_window_state != "PIT_WINDOW_CLOSED" and rejoin_traffic in {
        "HEAVY_TRAFFIC",
        "UNKNOWN",
    }:
        alerts.append(
            PitWallAlert(
                kind="REJOIN_TRAFFIC_RISK",
                driver_id=driver_id,
                detail=f"Projected pit rejoin traffic is {rejoin_traffic}.",
            )
        )
    alerts.append(
        PitWallAlert(
            kind=pit_window_state,
            driver_id=driver_id,
            detail=f"Paired counterfactual state is {pit_window_state}.",
        )
    )
    if decision_state != "ACTIONABLE":
        alerts.append(
            PitWallAlert(
                kind="STRATEGY_MODEL_UNCERTAIN",
                driver_id=driver_id,
                detail=f"Recommendation state is {decision_state}.",
            )
        )
    for rival in rivals:
        if rival.status == "in_pit":
            alerts.append(
                PitWallAlert(
                    kind="RIVAL_STOPPED",
                    driver_id=driver_id,
                    rival_id=rival.driver.id,
                    detail="A relevant nearby rival is currently in the pit sequence.",
                )
            )
        if (
            rival.position is not None
            and driver.position is not None
            and rival.position > driver.position
            and rival.gap_seconds is not None
            and 0 <= rival.gap_seconds <= 2
            and (rival.relative_pace_seconds_per_lap or 0) < 0
        ):
            alerts.append(
                PitWallAlert(
                    kind="UNDERCUT_THREAT",
                    driver_id=driver_id,
                    rival_id=rival.driver.id,
                    detail="A faster nearby car behind is within two seconds.",
                )
            )
    if recommendation and recommendation.startswith("PIT_NOW"):
        alerts.append(
            PitWallAlert(
                kind="POSITION_AT_RISK",
                driver_id=driver_id,
                detail="Staying out has a separated short-horizon disadvantage.",
            )
        )
    return alerts


def evaluate_driver(context, driver_id, trajectory_count=100, detail=False, pit_loss=None):
    driver = context.driver(driver_id)
    gap_kind = (
        "LAP_DEFICIT"
        if driver.lapped
        else "TIME"
        if driver.gap_to_leader is not None
        else "UNKNOWN"
    )
    if driver.status != "active":
        return PitWallDriver(
            driver=driver.driver,
            status=driver.status,
            current_position=driver.position,
            gap_kind=gap_kind,
            gap_to_leader_seconds=(driver.gap_to_leader if gap_kind == "TIME" else None),
            laps_behind=driver.laps_behind,
            compound=driver.compound,
            tyre_age=driver.tyre_age,
            pit_cycle_position=driver.position,
            decision_state="INSUFFICIENT_DATA",
            main_risk=f"Driver status is {driver.status}; no strategy action is generated.",
            data_quality=driver.quality.model_dump(),
        )
    pit_loss = pit_loss or estimate_pit_loss(context)
    policy = recommend_driver_action(context, driver_id, pit_loss)
    analysis = analyze_driver(context, driver_id, pit_loss) if detail else None
    paired = evaluate_paired_candidates(
        context,
        driver_id,
        policy.actions,
        trajectory_count,
        6000 + context.state.current_lap,
    )
    policy_by_id = {action.id: action for action in policy.actions}
    actions = [
        PitWallAction(
            action=action_id,
            kind="PIT_NOW" if action_id.startswith("PIT_NOW") else "EXTEND",
            policy_score=(
                policy_by_id[action_id].action_score if action_id in policy_by_id else None
            ),
            outcomes=outcomes,
        )
        for action_id, outcomes in paired.marginal_outcomes.items()
    ]
    best = paired.best_comparison
    best_pit_policy = next(
        (action for action in policy.actions if best and action.id == best.pit_action),
        None,
    )
    recommendation = None
    alternative = None
    decision_state = "INSUFFICIENT_DATA"
    margin = None
    overlap = None
    pit_window = None
    if best:
        decision_outcome = next(row for row in best.outcomes if row.horizon_laps == 5)
        interval = decision_outcome.interval_90
        overlap = bool(interval and interval[0] <= 0 <= interval[1])
        window_state = best.pit_window_state
        if window_state == "PIT_WINDOW_STRONG":
            recommendation, alternative = "PIT_NOW", "EXTEND"
        elif window_state == "PIT_WINDOW_CLOSED":
            recommendation, alternative = "EXTEND", "PIT_NOW"
        else:
            recommendation = "HOLD_NO_CLEAR_ADVANTAGE"
            alternative = "PIT_NOW" if window_state == "PIT_WINDOW_OPEN" else "EXTEND"
        applicable = [
            _outcome(action).applicability
            for action in actions
            if action.action in {best.pit_action, best.extend_action} and action.outcomes
        ]
        if decision_outcome.median_time_delta_seconds is None:
            decision_state = "COARSE_ONLY"
        elif any(value in {"WEAK", "OUT_OF_DOMAIN"} for value in applicable):
            decision_state = "COARSE_ONLY"
        elif window_state in {"PIT_WINDOW_OPEN", "PIT_WINDOW_UNCERTAIN"}:
            decision_state = "CAUTION"
        else:
            decision_state = "ACTIONABLE"
        margin = abs(decision_outcome.median_time_delta_seconds or 0)
        advantage = (
            -decision_outcome.median_time_delta_seconds
            if decision_outcome.median_time_delta_seconds is not None
            else None
        )
        cycle_advantage = (
            -decision_outcome.median_pit_cycle_position_delta
            if decision_outcome.median_pit_cycle_position_delta is not None
            else None
        )
        reason = (
            f"PIT clears the paired {EQUIVALENCE_BANDS[5]:.3f}s band with "
            f"frequency {decision_outcome.pit_better_frequency:.3f}."
            if window_state == "PIT_WINDOW_STRONG"
            and decision_outcome.pit_better_frequency is not None
            and decision_outcome.median_time_delta_seconds is not None
            and decision_outcome.median_time_delta_seconds <= -EQUIVALENCE_BANDS[5]
            else "PIT's 80% pit-cycle position range clears the stay-out range."
            if window_state == "PIT_WINDOW_STRONG"
            else "Paired evidence favors PIT but does not clear the strong-window gate."
            if window_state == "PIT_WINDOW_OPEN"
            else "Paired evidence favors EXTEND and shows no pit-cycle position gain."
            if window_state == "PIT_WINDOW_CLOSED"
            else "Paired time and pit-cycle evidence remain practically equivalent."
        )
        pit_window = PitWindow(
            state=window_state,
            best_compound=best_pit_policy.compound if best_pit_policy else None,
            paired_advantage_seconds=round(advantage, 3) if advantage is not None else None,
            pit_cycle_position_advantage=cycle_advantage,
            traffic=policy.traffic_status,
            rejoin=best_pit_policy.traffic_status if best_pit_policy else None,
            uncertainty=decision_state,
            reason=reason,
        )
    elif (since_pit := laps_since_last_pit(context, driver_id)) is not None and (
        since_pit <= NORMAL_STOP_COOLDOWN_LAPS
    ):
        recommendation, alternative = "EXTEND", None
        decision_state = "ACTIONABLE"
        overlap = False
        pit_window = PitWindow(
            state="PIT_WINDOW_CLOSED",
            best_compound=None,
            paired_advantage_seconds=None,
            pit_cycle_position_advantage=None,
            traffic=policy.traffic_status,
            rejoin=None,
            uncertainty=decision_state,
            reason=(
                f"Normal-stop cooldown: {since_pit} of {NORMAL_STOP_COOLDOWN_LAPS} "
                "leader laps have elapsed since the observed pit entry."
            ),
        )
    policy_name = policy.recommended_action
    disagreement = bool(policy_name and recommendation and policy_name != recommendation)
    if disagreement and decision_state == "ACTIONABLE":
        decision_state = "CAUTION"
    rivals = _relevant_rivals(context, driver)
    alerts = _alerts(
        driver,
        policy.traffic_status,
        best_pit_policy.traffic_status if best_pit_policy else "UNKNOWN",
        rivals,
        recommendation,
        decision_state,
        pit_window.state if pit_window else "PIT_WINDOW_CLOSED",
    )
    risk_alert = next(
        (
            alert
            for alert in alerts
            if alert.kind
            in {
                "REJOIN_TRAFFIC_RISK",
                "UNDERCUT_THREAT",
                "POSITION_AT_RISK",
                "STRATEGY_MODEL_UNCERTAIN",
            }
        ),
        None,
    )
    chosen_action = (
        best.pit_action
        if best and recommendation == "PIT_NOW"
        else best.extend_action
        if best
        else None
    )
    chosen = next((action for action in actions if action.action == chosen_action), None)
    net_position = _outcome(chosen).median_net_pit_cycle_position if chosen else None
    return PitWallDriver(
        driver=driver.driver,
        status=driver.status,
        current_position=driver.position,
        gap_kind=gap_kind,
        gap_to_leader_seconds=(driver.gap_to_leader if gap_kind == "TIME" else None),
        laps_behind=driver.laps_behind,
        compound=driver.compound,
        tyre_age=driver.tyre_age,
        recent_pace_seconds_per_lap=driver.recent_clean_pace,
        traffic=policy.traffic_status,
        pit_cycle_position=net_position or driver.position,
        recommendation=recommendation,
        alternative=alternative,
        decision_state=decision_state,
        policy_recommendation=policy_name,
        model_disagreement=disagreement,
        best_pit_compound=best_pit_policy.compound if best_pit_policy else None,
        paired_comparison=(
            best if detail or best is None else best.model_copy(update={"outcomes": []})
        ),
        paired_candidates=paired.comparisons if detail else [],
        pit_window=pit_window,
        evaluated_action_count=len(actions),
        decision_margin_seconds=round(margin, 3) if margin is not None else None,
        uncertainty_overlap=overlap,
        main_risk=(
            risk_alert.detail
            if risk_alert
            else policy.main_risks[0]
            if policy.main_risks
            else "No specific short-horizon risk was identified."
        ),
        main_opportunity=(
            pit_window.reason
            if pit_window
            else "No paired PIT-versus-EXTEND comparison is available."
        ),
        relevant_rivals=rivals,
        alerts=alerts,
        data_quality={
            "race_state": driver.quality.model_dump(),
            "policy": policy.data_quality,
            "short_horizon_only": True,
            "maximum_horizon_laps": 5,
        },
        actions=actions if detail else [],
        engineering_analysis=analysis if detail else None,
    )


def build_pitwall_snapshot(context, trajectory_count=100, detail_driver=None):
    started = perf_counter()
    pit_loss = estimate_pit_loss(context)
    drivers = [
        evaluate_driver(
            context,
            row.driver.id,
            trajectory_count,
            detail=detail_driver == row.driver.id,
            pit_loss=pit_loss,
        )
        for row in context.state.drivers
    ]
    alerts = [alert for driver in drivers for alert in driver.alerts]
    return PitWallSnapshot(
        event=context.state.event,
        lap=context.state.current_lap,
        cutoff_seconds=context.cutoff,
        race_status=context.state.track.track_status or "UNKNOWN",
        trajectory_count=trajectory_count,
        drivers=drivers,
        alerts=alerts,
        elapsed_seconds=round(perf_counter() - started, 3),
    )


def _change_reasons(previous, current):
    reasons = []
    if previous.traffic != current.traffic:
        reasons.append("traffic window changed")
    if previous.current_position != current.current_position:
        reasons.append("position changed")
    if previous.compound != current.compound:
        reasons.append("compound or pit-cycle state changed")
    if previous.pit_cycle_position != current.pit_cycle_position:
        reasons.append("pit-cycle position changed")
    if previous.decision_state != current.decision_state:
        reasons.append("uncertainty state changed")
    previous_rivals = {
        (r.driver.id, r.pit_stops_completed, r.status) for r in previous.relevant_rivals
    }
    current_rivals = {
        (r.driver.id, r.pit_stops_completed, r.status) for r in current.relevant_rivals
    }
    if previous_rivals != current_rivals:
        reasons.append("relevant rival or rival pit state changed")
    return reasons


def build_timeline(race, start_lap, end_lap, driver_id=None, trajectory_count=100):
    if end_lap < start_lap:
        raise ValueError("end_lap must be greater than or equal to start_lap")
    history = defaultdict(list)
    prior_observed = {}
    effective_recommendations = {}
    reanchored = []
    snapshot_elapsed = []
    flips = unsupported = actionable = disagreements = recommendations = 0
    for lap in range(start_lap, end_lap + 1):
        context = AnalysisContext(race, lap)
        reanchored.append(context.state.current_lap)
        if driver_id:
            started = perf_counter()
            current_drivers = [evaluate_driver(context, driver_id, trajectory_count)]
            snapshot_elapsed.append(perf_counter() - started)
        else:
            snapshot = build_pitwall_snapshot(context, trajectory_count)
            current_drivers = snapshot.drivers
            snapshot_elapsed.append(snapshot.elapsed_seconds or 0)
        for current in current_drivers:
            previous = prior_observed.get(current.driver.id)
            previous_effective = effective_recommendations.get(current.driver.id)
            raw_changed = bool(previous and previous.recommendation != current.recommendation)
            reasons = _change_reasons(previous, current) if raw_changed else []
            suppressed = bool(raw_changed and not reasons)
            if previous_effective is not None and (suppressed or not raw_changed):
                recommendation = previous_effective
            else:
                recommendation = current.recommendation
            changed = bool(previous and previous_effective != recommendation)
            age = (
                1
                if changed or not previous
                else history[current.driver.id][-1].recommendation_age + 1
            )
            current_window = current.pit_window.state if current.pit_window else "PIT_WINDOW_CLOSED"
            previous_window = (
                previous.pit_window.state
                if previous and previous.pit_window
                else "PIT_WINDOW_CLOSED"
            )
            window_changed = bool(previous and previous_window != current_window)
            pit_states = {"PIT_WINDOW_OPEN", "PIT_WINDOW_STRONG"}
            if current_window in pit_states:
                window_age = (
                    history[current.driver.id][-1].pit_window_age + 1
                    if previous and previous_window in pit_states
                    else 1
                )
            else:
                window_age = 0
            window_reason = None
            if window_changed:
                direction = (
                    "opened"
                    if current_window in {"PIT_WINDOW_OPEN", "PIT_WINDOW_STRONG"}
                    else "closed"
                    if current_window == "PIT_WINDOW_CLOSED"
                    else "became uncertain"
                )
                detail = (
                    current.pit_window.reason
                    if current.pit_window
                    else f"driver status is {current.status}"
                )
                window_reason = f"Pit window {direction}: {detail}"
            entry = PitWallTimelineEntry(
                lap=lap,
                observed_position=current.current_position,
                gap_kind=current.gap_kind,
                laps_behind=current.laps_behind,
                pit_cycle_position=current.pit_cycle_position,
                traffic=current.traffic,
                recommendation=recommendation,
                decision_state="CAUTION" if suppressed else current.decision_state,
                model_disagreement=current.model_disagreement,
                pit_window_state=current_window,
                pit_window_age=window_age,
                pit_window_change_reason=window_reason,
                recommendation_age=age,
                persistence=age,
                changed=changed,
                change_reasons=reasons
                or (["unsupported flip suppressed by hysteresis"] if suppressed else []),
                unsupported_flip_suppressed=suppressed,
            )
            history[current.driver.id].append(entry)
            prior_observed[current.driver.id] = current
            effective_recommendations[current.driver.id] = recommendation
            recommendations += int(recommendation is not None)
            flips += int(changed)
            unsupported += int(suppressed)
            actionable += int(entry.decision_state == "ACTIONABLE")
            disagreements += int(current.model_disagreement)
    timelines = []
    by_id = {row.driver.id: row.driver for row in AnalysisContext(race, start_lap).state.drivers}
    for key, entries in history.items():
        timelines.append(PitWallDriverTimeline(driver=by_id[key], entries=entries))
    recommendation_entries = [
        entry for timeline in timelines for entry in timeline.entries if entry.recommendation
    ]
    frequency = Counter(entry.recommendation for entry in recommendation_entries)
    pit_entries = [
        entry for entry in recommendation_entries if entry.recommendation.startswith("PIT_NOW")
    ]
    all_entries = [entry for timeline in timelines for entry in timeline.entries]
    one_lap_pit_spikes = 0
    for timeline in timelines:
        for index, entry in enumerate(timeline.entries):
            if entry.recommendation != "PIT_NOW":
                continue
            before = index == 0 or timeline.entries[index - 1].recommendation != "PIT_NOW"
            after = (
                index == len(timeline.entries) - 1
                or timeline.entries[index + 1].recommendation != "PIT_NOW"
            )
            one_lap_pit_spikes += int(before and after)
    return PitWallTimeline(
        event=race.event,
        start_lap=start_lap,
        end_lap=end_lap,
        reanchored_laps=reanchored,
        drivers=timelines,
        metrics={
            "recommendations": recommendations,
            "recommendation_frequency": dict(sorted(frequency.items())),
            "hold_rate": sum(
                e.recommendation == "HOLD_NO_CLEAR_ADVANTAGE" for t in timelines for e in t.entries
            )
            / max(recommendations, 1),
            "pit_rate": sum(
                bool(e.recommendation and e.recommendation.startswith("PIT_NOW"))
                for t in timelines
                for e in t.entries
            )
            / max(recommendations, 1),
            "flip_rate": flips / max(recommendations, 1),
            "unsupported_flip_rate": unsupported / max(recommendations, 1),
            "actionability_coverage": actionable / max(recommendations, 1),
            "model_disagreement_rate": disagreements / max(recommendations, 1),
            "mean_persistence_laps": sum(entry.persistence for entry in recommendation_entries)
            / max(recommendations, 1),
            "mean_pit_call_age_laps": sum(entry.recommendation_age for entry in pit_entries)
            / max(len(pit_entries), 1),
            "pit_window_open_count": sum(
                entry.pit_window_state == "PIT_WINDOW_OPEN" for entry in all_entries
            ),
            "pit_window_strong_count": sum(
                entry.pit_window_state == "PIT_WINDOW_STRONG" for entry in all_entries
            ),
            "one_lap_pit_spikes": one_lap_pit_spikes,
            "elapsed_seconds": sum(snapshot_elapsed),
            "mean_snapshot_elapsed_seconds": sum(snapshot_elapsed) / max(len(snapshot_elapsed), 1),
        },
    )


def run_pitwall_replay(race, start_lap, end_lap, driver_id=None, trajectory_count=100):
    """Evaluate consecutive observed laps; no simulated state crosses a lap boundary."""
    return build_timeline(race, start_lap, end_lap, driver_id, trajectory_count)


class PitWallService:
    def __init__(self, replay):
        self.replay = replay

    async def snapshot(self, year, round, lap, trajectory_count=100):
        race = await self.replay.load_race(year, round)
        return await asyncio.to_thread(
            build_pitwall_snapshot, AnalysisContext(race, lap), trajectory_count
        )

    async def snapshot_history(self, race, lap, trajectory_count=100):
        return await asyncio.to_thread(
            build_pitwall_snapshot, AnalysisContext(race, lap), trajectory_count
        )

    async def driver(self, year, round, lap, driver_id, trajectory_count=100):
        race = await self.replay.load_race(year, round)
        context = AnalysisContext(race, lap)
        return await asyncio.to_thread(evaluate_driver, context, driver_id, trajectory_count, True)

    async def driver_history(self, race, lap, driver_id, trajectory_count=100):
        context = AnalysisContext(race, lap)
        return await asyncio.to_thread(evaluate_driver, context, driver_id, trajectory_count, True)

    async def timeline(self, year, round, start_lap, end_lap, driver_id=None, trajectory_count=100):
        race = await self.replay.load_race(year, round)
        return await asyncio.to_thread(
            build_timeline, race, start_lap, end_lap, driver_id, trajectory_count
        )
