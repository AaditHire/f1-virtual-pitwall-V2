"""Paired statistical helpers for Phase 13D Experiment A."""

from __future__ import annotations

import math
import random


def exact_mcnemar_p(
    control_fails_treatment_succeeds: int, control_succeeds_treatment_fails: int
) -> float:
    """Return the two-sided exact binomial McNemar p-value."""
    b = control_fails_treatment_succeeds
    c = control_succeeds_treatment_fails
    discordant = b + c
    if discordant == 0:
        return 1.0
    tail = sum(math.comb(discordant, index) for index in range(min(b, c) + 1))
    return min(1.0, 2.0 * tail / (2**discordant))


def paired_failure_analysis(
    pairs: list[tuple[bool, bool]], *, bootstrap_seed: int, resamples: int = 100_000
) -> dict[str, object]:
    """Compare paired control/treatment failure flags with frozen inference."""
    if not pairs:
        raise ValueError("paired analysis requires at least one pair")
    both_succeed = sum(not control and not treatment for control, treatment in pairs)
    control_fails_treatment_succeeds = sum(
        control and not treatment for control, treatment in pairs
    )
    control_succeeds_treatment_fails = sum(
        not control and treatment for control, treatment in pairs
    )
    both_fail = sum(control and treatment for control, treatment in pairs)
    control_failures = control_fails_treatment_succeeds + both_fail
    treatment_failures = control_succeeds_treatment_fails + both_fail
    count = len(pairs)
    differences = [int(control) - int(treatment) for control, treatment in pairs]
    rng = random.Random(bootstrap_seed)
    estimates = []
    for _ in range(resamples):
        estimates.append(sum(rng.choice(differences) for _ in range(count)) / count)
    estimates.sort()
    lower = estimates[int(0.025 * resamples)]
    upper = estimates[min(resamples - 1, math.ceil(0.975 * resamples) - 1)]
    return {
        "pairs": count,
        "both_succeed": both_succeed,
        "control_fails_treatment_succeeds": control_fails_treatment_succeeds,
        "control_succeeds_treatment_fails": control_succeeds_treatment_fails,
        "both_fail": both_fail,
        "control_failures": control_failures,
        "control_failure_rate": control_failures / count,
        "treatment_failures": treatment_failures,
        "treatment_failure_rate": treatment_failures / count,
        "treatment_minus_control_percentage_points": 100
        * (treatment_failures - control_failures)
        / count,
        "relative_failure_reduction": (
            (control_failures - treatment_failures) / control_failures if control_failures else None
        ),
        "exact_mcnemar_two_sided_p": exact_mcnemar_p(
            control_fails_treatment_succeeds, control_succeeds_treatment_fails
        ),
        "control_minus_treatment_failure_rate_bootstrap_95_percentile_ci": [
            lower,
            upper,
        ],
        "bootstrap_resamples": resamples,
        "bootstrap_seed": bootstrap_seed,
    }
