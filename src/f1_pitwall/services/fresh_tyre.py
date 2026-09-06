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
    return {
        "driver_id": identity,
        "old_compound": old.compound,
        "new_compound": new.compound,
        "old_age": old.tyre_age,
        "fresh_tyre_delta": median(pre) - post[0],
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
    selected = exact if new_compound is not None and exact else samples
    gains = [sample["fresh_tyre_delta"] for sample in selected]
    warmups = [
        sample["warm_up_delta_seconds"]
        for sample in selected
        if sample["warm_up_delta_seconds"] is not None
    ]
    value = median(gains) if gains else None
    dispersion = median(abs(gain - value) for gain in gains) if gains else None
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
            "validation_samples": 105,
            "validation_mae_seconds": 0.911,
            "zero_delta_baseline_mae_seconds": 1.399,
        },
        warnings=[
            "Current-race completed stops only; no later-race or generic fixed bonus.",
            "First clean full post-stop lap includes observed warm-up effects but remains "
            "sensitive to compound choice, traffic and driver differences.",
        ],
    )
    if new_compound is not None and not exact:
        result.warnings.append(
            "No exact compound-transition evidence; pooled same-old-compound evidence used."
        )
    if not selected:
        result.warnings.append("No reference-covered matching stop was available by the cutoff.")
    return result
