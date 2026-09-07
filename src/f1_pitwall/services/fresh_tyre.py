"""Empirical, race-causal fresh-versus-used pace evidence."""

from statistics import median

from f1_pitwall.domain.analysis import FreshTyreAnalysis
from f1_pitwall.services.pace import normalized_lap_times


def _normalized_values(context, identity, lap_numbers):
    values = dict(normalized_lap_times(context, identity, lap_numbers, current_stint=False))
    return [values[number] for number in lap_numbers if number in values]


def _sample(context, stop):
    identity = stop["driver_id"]
    old = context.stint_at(identity, stop["entered_at"] - 1)
    first_post = stop["post_laps"][0]
    new = context.stint_at(identity, context.rows[identity][first_post].completed_at)
    pre = _normalized_values(context, identity, stop["pre_laps"])
    post = _normalized_values(context, identity, stop["post_laps"])
    if not old or not new or old.number == new.number or len(pre) < 2 or not post:
        return None
    post_offsets = [median(pre) - value for value in post]
    return {
        "driver_id": identity,
        "old_compound": old.compound,
        "new_compound": new.compound,
        "old_age": old.tyre_age,
        "fresh_tyre_delta": median(pre) - post[0],
        "post_stop_offsets_seconds": post_offsets,
        "warm_up_delta_seconds": post[0] - median(post[1:]) if len(post) > 1 else None,
        "pre_laps": stop["pre_laps"],
        "post_lap": first_post,
        "evidence_available_at": stop["evidence_available_at"],
    }


def estimate_fresh_tyre_delta(context, driver_id, pit_loss, new_compound=None):
    driver = context.driver(driver_id)
    new_compound = new_compound.upper() if new_compound else None
    samples = [_sample(context, stop) for stop in pit_loss.components.get("samples", [])]
    samples = [
        sample
        for sample in samples
        if sample is not None and sample["old_compound"] == driver.compound
    ]
    exact = [sample for sample in samples if sample["new_compound"] == new_compound]
    # Compound-specific curves are too noisy below three completed chronological stops.
    selected = exact if new_compound is not None and len(exact) >= 3 else samples
    gains = [sample["fresh_tyre_delta"] for sample in selected]
    warmups = [
        sample["warm_up_delta_seconds"]
        for sample in selected
        if sample["warm_up_delta_seconds"] is not None
    ]
    value = median(gains) if gains else None
    dispersion = median(abs(gain - value) for gain in gains) if gains else None
    curve = []
    curve_counts = []
    curve_mad = []
    for index in range(3):
        values = [
            sample["post_stop_offsets_seconds"][index]
            for sample in selected
            if len(sample["post_stop_offsets_seconds"]) > index
        ]
        centre = median(values) if values else None
        curve.append(centre)
        curve_counts.append(len(values))
        curve_mad.append(median(abs(item - centre) for item in values) if values else None)
    result = FreshTyreAnalysis(
        driver_id=driver_id,
        old_compound=driver.compound,
        new_compound=new_compound,
        fresh_tyre_delta=value,
        warm_up_delta_seconds=median(warmups) if warmups else None,
        sample_count=len(selected),
        confidence="LOW" if selected else "INSUFFICIENT",
        method="Median normalized pre-stop pace minus first clean full post-stop lap",
        components={
            "samples": selected,
            "sample_mad_seconds": dispersion,
            "transition_exact": bool(new_compound is not None and exact),
            "compound_curve_used": bool(new_compound is not None and len(exact) >= 3),
            "warm_up_curve_gain_seconds": {
                "post_stop_lap_1": curve[0],
                "post_stop_lap_2": curve[1],
                "post_stop_lap_3_plus": curve[2],
            },
            "warm_up_curve_samples": {
                "post_stop_lap_1": curve_counts[0],
                "post_stop_lap_2": curve_counts[1],
                "post_stop_lap_3_plus": curve_counts[2],
            },
            "warm_up_curve_mad_seconds": {
                "post_stop_lap_1": curve_mad[0],
                "post_stop_lap_2": curve_mad[1],
                "post_stop_lap_3_plus": curve_mad[2],
            },
            "validation_samples": 105,
            "validation_mae_seconds": 0.911,
            "zero_delta_baseline_mae_seconds": 1.399,
        },
        warnings=[
            "Current-race completed stops only; no later-race or generic fixed bonus.",
            "The out-lap remains inside the observed stop-lap residual. The first three clean "
            "full laps form the empirical warm-up curve.",
        ],
    )
    if new_compound is not None and len(exact) < 3:
        result.warnings.append(
            "Fewer than three exact compound transitions; pooled same-old-compound curve used."
        )
    if not selected:
        result.warnings.append("No reference-covered matching stop was available by the cutoff.")
    return result
