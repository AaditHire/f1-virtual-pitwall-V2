"""Traffic facts and an explicit one-lap, no-overtake blockage approximation."""

from f1_pitwall.domain.analysis import TrafficAnalysis
from f1_pitwall.services.pace import get_recent_pace
from f1_pitwall.services.pit_analysis import traffic_level


def analyze_traffic(context, driver_id, rejoin=None):
    driver = context.driver(driver_id)
    by_position = {d.position: d for d in context.state.drivers if d.position is not None}
    ahead = by_position.get(driver.position - 1) if driver.position else None
    behind = by_position.get(driver.position + 1) if driver.position else None
    pace = get_recent_pace(context, driver_id).seconds
    ahead_pace = get_recent_pace(context, ahead.driver.id).seconds if ahead else None
    behind_pace = get_recent_pace(context, behind.driver.id).seconds if behind else None
    active = driver.status == "active"
    gap_ahead = driver.gap_to_ahead if active else None
    gap_behind = driver.gap_to_behind if active else None
    complete = (
        active
        and driver.position is not None
        and (driver.position == 1 or gap_ahead is not None)
        and (behind is None or gap_behind is not None)
    )
    known_gaps = [gap for gap in (gap_ahead, gap_behind) if gap is not None]
    density = sum(gap <= 5 for gap in known_gaps)
    level = traffic_level(gap_ahead, gap_behind, density, complete)
    relative_ahead = ahead_pace - pace if ahead_pace is not None and pace is not None else None
    relative_behind = behind_pace - pace if behind_pace is not None and pace is not None else None
    return TrafficAnalysis(
        driver_id=driver_id,
        status=level,
        ahead_id=ahead.driver.id if ahead else None,
        behind_id=behind.driver.id if behind else None,
        gap_ahead=gap_ahead,
        gap_behind=gap_behind,
        relative_pace_ahead=relative_ahead,
        relative_pace_behind=relative_behind,
        clear_air=level == "CLEAR_AIR" if level != "UNKNOWN" else None,
        slower_car_blockage=(gap_ahead <= 2 and relative_ahead > 0.3)
        if gap_ahead is not None and relative_ahead is not None
        else None,
        rejoin_density=len(rejoin.nearby_drivers) if rejoin and rejoin.position_range else None,
        confidence="MEDIUM"
        if complete and pace is not None
        else "LOW"
        if known_gaps
        else "INSUFFICIENT",
        method="Reported adjacent intervals and recent clean-stint medians",
        components={
            "own_pace": pace,
            "ahead_pace": ahead_pace,
            "behind_pace": behind_pace,
            "adjacent_density_within_5s": density,
            "positive_relative_pace_means": "neighbour is slower than driver",
            "blockage_threshold_seconds": 2,
            "pace_advantage_threshold_seconds": 0.3,
        },
        warnings=["Race-order neighbours do not identify every lapped car physically nearby."]
        + ([] if complete else ["Incomplete current intervals; no complete clear-air claim."]),
    )


def blockage_penalty(context, ahead_id, gap_ahead, own_pace, complete):
    if not complete or own_pace is None:
        return None
    if ahead_id is None:
        return 0.0
    if gap_ahead is None:
        return None
    other = get_recent_pace(context, ahead_id).seconds
    if other is None:
        return None
    advantage = max(0.0, other - own_pace)
    # At most the one-lap pace advantage is lost; a 1s following headway is assumed.
    return min(advantage, max(0.0, advantage + 1.0 - gap_ahead))
