"""Observed stop residuals and instantaneous race-gap geometry."""

from statistics import median

from f1_pitwall.domain.analysis import PitLossAnalysis, PitWindowAnalysis, RejoinAnalysis
from f1_pitwall.services.analysis_context import AnalysisContext


def green_between(context, start, end):
    status = sorted(
        (c for c in context.race.control if c.track_status is not None and c.at <= end),
        key=lambda c: c.at,
    )
    before = [c for c in status if c.at <= start]
    return bool(before) and all(
        c.track_status == "1" for c in before[-1:] + [c for c in status if c.at > start]
    )


def observed_pit_samples(context: AnalysisContext):
    samples, excluded = [], []
    for pit in context.race.pit_stops:
        reason = None
        if pit.entered_at < context.race.started_at or pit.exited_at is None:
            continue
        rows = sorted(context.rows.get(pit.driver_id, {}).values(), key=lambda r: r.number)
        before = [r for r in rows if r.completed_at < pit.entered_at]
        after = [r for r in rows if r.completed_at >= pit.exited_at]
        if not before or not after:
            reason = "No completed crossing window around stop"
        else:
            start, end = before[-1], after[0]
            affected = [r for r in rows if start.number < r.number <= end.number]
            clean = context.clean.get(pit.driver_id, [])
            pre = [r for r in clean if start.number - 5 < r.number <= start.number][-3:]
            post = [r for r in clean if end.number < r.number <= end.number + 5][:3]
            if (
                len(pre) < 2
                or len(post) < 2
                or not affected
                or len(affected) != end.number - start.number
                or any(r.lap_time_seconds is None for r in affected)
            ):
                reason = "Need consecutive affected laps and two nearby clean laps on each side"
            elif not green_between(context, start.completed_at, post[-1].completed_at):
                reason = "Stop window affected by non-green or unknown track conditions"
            else:
                baseline = (
                    median(r.lap_time_seconds for r in pre)
                    + median(r.lap_time_seconds for r in post)
                ) / 2
                loss = sum(r.lap_time_seconds for r in affected) - baseline * len(affected)
                lane = pit.exited_at - pit.entered_at
                if not 0 < loss < baseline * 0.6 or not 0 < lane < baseline * 0.75:
                    reason = "Abnormal or inconsistent stop, outside broad lap-relative bounds"
                else:
                    samples.append(
                        {
                            "driver_id": pit.driver_id,
                            "entered_at": pit.entered_at,
                            "exited_at": pit.exited_at,
                            "evidence_available_at": max(
                                r.available_at for r in affected + pre + post
                            ),
                            "loss_seconds": loss,
                            "pit_lane_elapsed_seconds": lane,
                            "baseline_lap_seconds": baseline,
                            "affected_laps": [r.number for r in affected],
                            "pre_laps": [r.number for r in pre],
                            "post_laps": [r.number for r in post],
                        }
                    )
        if reason:
            excluded.append(
                {"driver_id": pit.driver_id, "entered_at": pit.entered_at, "reason": reason}
            )
    # A normal-stop estimate should resist long repairs/penalties, with a broad jitter floor.
    if len(samples) >= 4:
        centre = median(s["loss_seconds"] for s in samples)
        mad = median(abs(s["loss_seconds"] - centre) for s in samples)
        keep = []
        for sample in samples:
            if abs(sample["loss_seconds"] - centre) <= max(5.0, 3 * mad):
                keep.append(sample)
            else:
                excluded.append(
                    {
                        "driver_id": sample["driver_id"],
                        "entered_at": sample["entered_at"],
                        "reason": "Stop residual outlier versus observed session stops",
                    }
                )
        samples = keep
    return samples, excluded


def estimate_pit_loss(context: AnalysisContext):
    samples, excluded = observed_pit_samples(context)
    values = [s["loss_seconds"] for s in samples]
    value = median(values) if values else None
    mad = median(abs(v - value) for v in values) if values else None
    result = PitLossAnalysis(
        total_seconds=value,
        sample_count=len(samples),
        confidence="MEDIUM"
        if len(samples) >= 3 and mad <= 3
        else "LOW"
        if samples
        else "INSUFFICIENT",
        method="Causal session median of green-stop lap-time residuals",
        components={
            "samples": samples,
            "excluded_stops": excluded,
            "residual_mad_seconds": mad,
            "pit_lane_elapsed_median_seconds": median(
                s["pit_lane_elapsed_seconds"] for s in samples
            )
            if samples
            else None,
        },
        warnings=[
            "Residual includes tyre, warm-up and traffic effects; normal green stop only.",
            "Pit-lane elapsed time is not race-time loss. Transit and stationary split unknown.",
        ],
    )
    if not samples:
        result.warnings.append("No qualified completed stops yet; no non-causal session fallback.")
    return result


def traffic_level(gap_ahead, gap_behind, density, complete=True):
    if (gap_ahead is not None and gap_ahead <= 1) or density >= 3:
        return "HEAVY_TRAFFIC"
    if (gap_ahead is not None and gap_ahead <= 2) or density >= 2:
        return "MODERATE_TRAFFIC"
    if not complete:
        return "UNKNOWN"
    if (gap_ahead is not None and gap_ahead <= 5) or (gap_behind is not None and gap_behind <= 2):
        return "LIGHT_TRAFFIC"
    return "CLEAR_AIR"


def predict_pit_rejoin(driver_id, state, pit_loss: PitLossAnalysis):
    driver = next((d for d in state.drivers if d.driver.id == driver_id), None)
    if driver is None:
        from f1_pitwall.core.exceptions import NotFound

        raise NotFound(f"Driver {driver_id} is not in this race session")
    result = RejoinAnalysis(
        driver_id=driver_id,
        method="Insert driver at current leader gap plus normal pit loss",
        components={
            "pit_loss_seconds": pit_loss.total_seconds,
            "current_leader_gap": driver.gap_to_leader,
            "density_radius_seconds": 5,
        },
        warnings=["Instantaneous frozen-gap approximation; no lap evolution or overtaking model."],
    )
    if (
        driver.status != "active"
        or driver.gap_to_leader is None
        or driver.lapped
        or pit_loss.total_seconds is None
        or state.track.track_status != "1"
    ):
        result.warnings.append(
            "Requires active same-lap driver, a green track, gap and pit-loss evidence."
        )
        return result
    projected = driver.gap_to_leader + pit_loss.total_seconds
    field = [d for d in state.drivers if d.driver.id != driver_id and d.active is not False]
    known = sorted(
        (d for d in field if d.status == "active" and d.gap_to_leader is not None and not d.lapped),
        key=lambda d: d.gap_to_leader,
    )
    unknown = len(field) - len(known)
    ahead = [d for d in known if d.gap_to_leader <= projected]
    behind = [d for d in known if d.gap_to_leader > projected]
    position = len(ahead) + 1
    result.position_range = (position, position + unknown)
    result.projected_position = position if not unknown else None
    if ahead:
        result.ahead_id = ahead[-1].driver.id
        result.gap_ahead = projected - ahead[-1].gap_to_leader
    if behind:
        result.behind_id = behind[0].driver.id
        result.gap_behind = behind[0].gap_to_leader - projected
    result.nearby_drivers = [d.driver.id for d in known if abs(d.gap_to_leader - projected) <= 5]
    result.traffic = traffic_level(
        result.gap_ahead, result.gap_behind, len(result.nearby_drivers), not unknown
    )
    result.confidence = "LOW" if unknown or pit_loss.confidence == "LOW" else "MEDIUM"
    result.components.update(
        {
            "projected_leader_gap": projected,
            "unknown_cars": unknown,
            "known_gaps": {d.driver.id: d.gap_to_leader for d in known},
        }
    )
    if unknown:
        result.warnings.append(
            "Lapped, pitting or missing-gap cars prevent exact rank and clear-air claim; "
            "reported neighbours are only among cars with usable second-valued gaps."
        )
    return result


def find_pit_window(driver_id, state, pit_loss):
    rejoin = predict_pit_rejoin(driver_id, state, pit_loss)
    return PitWindowAnalysis(
        current_lap=state.current_lap,
        rejoin=rejoin,
        confidence=rejoin.confidence,
        method="Current-lap rejoin geometry only",
        traffic_risk=rejoin.traffic,
        clear_air_opportunity=None
        if rejoin.traffic == "UNKNOWN"
        else rejoin.traffic == "CLEAR_AIR",
        components={"nearby_drivers": rejoin.nearby_drivers, "projection_laps": 0},
        warnings=["No future pit window, strategy ranking or recommendation is calculated."],
    )
