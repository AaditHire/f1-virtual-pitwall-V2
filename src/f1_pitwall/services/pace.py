"""Robust descriptive pace and current-stint trends; all inputs are causal."""

from statistics import median

from f1_pitwall.domain.analysis import PaceAnalysis, TyreAnalysis
from f1_pitwall.services.analysis_context import AnalysisContext


def get_clean_laps(context: AnalysisContext, driver_id: str):
    context.driver(driver_id)
    return list(context.clean[driver_id])


def pace_result(rows, method):
    return PaceAnalysis(
        seconds=median(r.lap_time_seconds for r in rows) if rows else None,
        lap_numbers=[r.number for r in rows],
        sample_count=len(rows),
        method=method,
        confidence="MEDIUM" if len(rows) >= 3 else "LOW" if rows else "INSUFFICIENT",
        components={"lap_times": [r.lap_time_seconds for r in rows]},
        warnings=[] if len(rows) >= 3 else ["Fewer than three recent clean laps."],
    )


def get_recent_pace(context: AnalysisContext, driver_id: str):
    driver = context.driver(driver_id)
    # Never silently reuse pace from old tyres or long before a neutralisation.
    floor = (driver.laps_completed or 0) - 5
    rows = [r for r in context.stint_laps(driver_id) if r.number > floor][-3:]
    return pace_result(rows, "Median of up to three current-stint clean laps in last five laps")


def get_stint_pace(context: AnalysisContext, driver_id: str):
    return pace_result(context.stint_laps(driver_id), "Median current-stint clean lap time")


def robust_line(rows):
    slopes = [
        (b.lap_time_seconds - a.lap_time_seconds) / (b.number - a.number)
        for index, a in enumerate(rows)
        for b in rows[index + 1 :]
    ]
    slope = median(slopes)
    intercept = median(r.lap_time_seconds - slope * r.number for r in rows)
    residual = median(abs(r.lap_time_seconds - intercept - slope * r.number) for r in rows)
    return slope, intercept, residual, slopes


def analyze_tyres(context: AnalysisContext, driver_id: str):
    driver = context.driver(driver_id)
    rows = context.stint_laps(driver_id)
    result = TyreAnalysis(
        driver_id=driver_id,
        compound=driver.compound,
        stint_number=driver.stint_number,
        tyre_age=driver.tyre_age,
        sample_count=len(rows),
        method="Theil-Sen median pairwise slope against completed lap number",
        components={
            "lap_numbers": [r.number for r in rows],
            "lap_times": [r.lap_time_seconds for r in rows],
            "tyre_age_observed_at": driver.tyre_age_observed_at,
            "fuel_correction_sec_per_lap": None,
            "competitive_loss_threshold_seconds": 2.0,
        },
        warnings=[
            "Net observed pace trend, not isolated tyre wear: fuel, traffic and track evolution "
            "are unmeasured. No invented fuel correction is applied.",
            "Only explicitly identified lap deletions are filtered; "
            "absence is not proof of validity.",
        ],
    )
    if len(rows) < 5 or rows[-1].number - rows[0].number < 4:
        result.warnings.append("Need five clean laps spanning at least four laps in this stint.")
        return result
    slope, intercept, residual, slopes = robust_line(rows)
    result.degradation_sec_per_lap = slope
    result.recent_trend_sec_per_lap = robust_line(rows[-5:])[0]
    result.recent_pace_delta = median(r.lap_time_seconds for r in rows[-3:]) - median(
        r.lap_time_seconds for r in rows[:3]
    )
    stale = (driver.laps_completed or 0) - rows[-1].number > 2
    result.confidence = (
        "MEDIUM"
        if len(rows) >= 8
        and rows[-1].number - rows[0].number >= 7
        and residual <= 0.7
        and not stale
        else "LOW"
    )
    result.components.update(
        {
            "intercept_seconds": intercept,
            "residual_mad_seconds": residual,
            "slope_lower_quartile": sorted(slopes)[len(slopes) // 4],
            "last_clean_lap": rows[-1].number,
        }
    )
    # This is a bounded illustrative tyre-life threshold, not a strategy horizon.
    lower = sorted(slopes)[len(slopes) // 4]
    age_is_recent = (
        driver.tyre_age_observed_at is not None
        and sum(
            r.completed_at > driver.tyre_age_observed_at for r in context.rows[driver_id].values()
        )
        <= 1
    )
    remaining = (2.0 - result.recent_pace_delta) / slope if slope > 0 else None
    if (
        result.confidence == "MEDIUM"
        and lower > 0
        and remaining is not None
        and 0 <= remaining <= rows[-1].number - rows[0].number
        and driver.tyre_age is not None
        and age_is_recent
    ):
        result.estimated_competitive_life_laps = driver.tyre_age + remaining
        result.warnings.append(
            "Life is an illustrative age at +2s versus early stint, not tyre failure."
        )
    else:
        result.warnings.append("Competitive life unsupported by a stable positive, bounded trend.")
    if stale:
        result.warnings.append("Most recent clean lap is more than two completed laps old.")
    return result
