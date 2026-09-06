"""Robust descriptive pace and current-stint trends; all inputs are causal."""

from collections import defaultdict
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


def field_reference(context: AnalysisContext, excluded_driver: str):
    """Leave-one-driver-out race-lap medians from at least five other clean cars."""
    by_lap = defaultdict(list)
    for identity, rows in context.clean.items():
        if identity == excluded_driver:
            continue
        for row in rows:
            by_lap[row.number].append(row.lap_time_seconds)
    return {lap: median(values) for lap, values in by_lap.items() if len(values) >= 5}


def normalized_lap_times(
    context: AnalysisContext, driver_id: str, lap_numbers=None, current_stint=True
):
    reference = field_reference(context, driver_id)
    rows = context.stint_laps(driver_id) if current_stint else context.clean[driver_id]
    selected = set(lap_numbers) if lap_numbers is not None else None
    return [
        (row.number, row.lap_time_seconds - reference[row.number])
        for row in rows
        if row.number in reference and (selected is None or row.number in selected)
    ]


def robust_line(rows, x=lambda row: row.number, y=lambda row: row.lap_time_seconds):
    slopes = [
        (y(b) - y(a)) / (x(b) - x(a))
        for index, a in enumerate(rows)
        for b in rows[index + 1 :]
        if x(b) != x(a)
    ]
    slope = median(slopes)
    intercept = median(y(row) - slope * x(row) for row in rows)
    residual = median(abs(y(row) - intercept - slope * x(row)) for row in rows)
    return slope, intercept, residual, slopes


def analyze_tyres(context: AnalysisContext, driver_id: str):
    driver = context.driver(driver_id)
    rows = context.stint_laps(driver_id)
    normalized = normalized_lap_times(context, driver_id)
    result = TyreAnalysis(
        driver_id=driver_id,
        compound=driver.compound,
        stint_number=driver.stint_number,
        tyre_age=driver.tyre_age,
        sample_count=len(normalized),
        reference_coverage=len(normalized) / len(rows) if rows else None,
        method="Zero-slope short-horizon forecast relative to leave-one-driver-out field pace",
        components={
            "lap_numbers": [r.number for r in rows],
            "lap_times": [r.lap_time_seconds for r in rows],
            "normalized_lap_numbers": [number for number, _ in normalized],
            "normalized_pace_seconds": [value for _, value in normalized],
            "tyre_age_observed_at": driver.tyre_age_observed_at,
            "reference_minimum_other_drivers": 5,
            "forecast_target": "Pace change relative to contemporaneous field median",
            "selected_model": "zero_slope",
            "validation_samples": 1278,
            "validation_mae_seconds": 0.506,
        },
        warnings=[
            "Historical holdouts did not support a fitted degradation trend; zero expected "
            "relative loss is a conservative forecast assumption, not evidence of zero wear.",
            "Only explicitly identified lap deletions are filtered; "
            "absence is not proof of validity.",
        ],
    )
    if len(normalized) < 5 or normalized[-1][0] - normalized[0][0] < 4:
        result.warnings.append(
            "Need five reference-covered clean laps spanning at least four laps in this stint."
        )
        return result
    raw_slope, raw_intercept, raw_residual, raw_slopes = robust_line(rows)
    normalized_slope, intercept, residual, slopes = robust_line(
        normalized, x=lambda point: point[0], y=lambda point: point[1]
    )
    recent = normalized[-5:]
    result.degradation_sec_per_lap = 0.0
    result.expected_3_lap_pace_loss = 0.0
    result.expected_5_lap_pace_loss = 0.0
    result.recent_trend_sec_per_lap = robust_line(
        recent, x=lambda point: point[0], y=lambda point: point[1]
    )[0]
    result.recent_pace_delta = median(value for _, value in normalized[-3:]) - median(
        value for _, value in normalized[:3]
    )
    stale = (driver.laps_completed or 0) - rows[-1].number > 2
    result.confidence = "LOW"
    result.components.update(
        {
            "intercept_seconds": intercept,
            "residual_mad_seconds": residual,
            "slope_lower_quartile": sorted(slopes)[len(slopes) // 4],
            "last_clean_lap": rows[-1].number,
            "candidate_raw_slope_sec_per_lap": raw_slope,
            "candidate_raw_intercept_seconds": raw_intercept,
            "candidate_raw_residual_mad_seconds": raw_residual,
            "candidate_raw_slope_lower_quartile": sorted(raw_slopes)[len(raw_slopes) // 4],
            "candidate_normalized_slope_sec_per_lap": normalized_slope,
            "candidate_recent_normalized_slope_sec_per_lap": result.recent_trend_sec_per_lap,
        }
    )
    result.warnings.append(
        "Raw and normalized fitted slopes are diagnostic components only; competitive life "
        "is unavailable because no fitted candidate beat zero-slope validation."
    )
    if stale:
        result.warnings.append("Most recent clean lap is more than two completed laps old.")
    return result
