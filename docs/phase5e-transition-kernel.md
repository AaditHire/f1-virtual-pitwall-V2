# Phase 5E one-lap probabilistic transition kernel

## Scope and chronology

This phase adds a one-lap kernel for fixed PIT_NOW and EXTEND actions. It does not replan,
simulate a whole race, or model safety cars, rain, failures, or championship outcomes.

Residual fitting uses 1,418 factual records from 2021–2023. Error-correlation selection uses
507 records from 2024. The frozen final evaluation contains 582 factual 2025 records, of which
484 have a green-flag outcome window. No parameter is fitted or selected on 2025 outcomes.

## Architecture

The first transition retains the Phase 5C deterministic +1 prediction. Later transitions use
the existing causal lap-by-lap physics trace and update a minimal mutable state containing the
driver, time or lap-deficit state, compound, tyre age, stint, recent pace, traffic, pit count,
laps since pit, active status, uncertainty, and pit phase. Direct +3/+5 predictions are used
only as evaluation baselines.

The stochastic layer samples empirical residuals conditioned on action, pit phase, and Phase
5D applicability where at least 12 samples exist. Sparse groups shrink to their action/phase
pool. WEAK and OUT_OF_DOMAIN states broaden the residual distribution. Random sampling uses a
caller-supplied seed and supports 100, 500, or 1,000 trajectories.

PIT transitions explicitly expose PIT_ENTRY, PIT_TRANSIT, PIT_EXIT, POST_STOP_LAP_1, and
NORMAL_RUNNING. EXTEND_3 remains a fixed plan: three stay-out laps, then the pit transition.

## Correlated error selection

The tested persistent model combines a trajectory-specific latent pace offset with lap noise
at a fixed 0.65/0.35 weight. The independent model samples each lap from its applicable
empirical phase pool. On the frozen 2024 selection score, independent sampling scored 0.156
versus 0.225 for persistent sampling, so independent sampling is packaged for production.

On 2024 the selected model achieved 80% coverage of 84.2%, 81.4%, and 82.9% at +1/+3/+5.
Mean 80% widths increased monotonically from 3.782s to 6.994s to 8.919s.

## Final 2025 direct-versus-rollout results

Across PIT and EXTEND factual cases:

| Horizon | Model | MAE | Bias | P90 AE |
|---|---|---:|---:|---:|
| +1 | Phase 5D direct | 1.263s | +0.351s | 2.736s |
| +1 | Probabilistic rollout median | 1.229s | +0.373s | 2.699s |
| +3 | Phase 5D direct | 2.279s | +0.615s | 5.500s |
| +3 | Deterministic one-lap rollout | 2.356s | +1.002s | 5.729s |
| +3 | Probabilistic rollout median | 2.284s | -0.006s | 5.416s |
| +5 | Phase 5D direct | 3.277s | +0.585s | 8.187s |
| +5 | Deterministic one-lap rollout | 3.473s | +1.296s | 8.620s |
| +5 | Probabilistic rollout median | 3.244s | -0.444s | 7.600s |

PIT-only results show the main tail benefit:

| Horizon | Model | MAE | Bias | P90 AE |
|---|---|---:|---:|---:|
| +1 | Direct | 2.882s | +0.568s | 7.313s |
| +1 | Probabilistic | 2.644s | +0.856s | 6.797s |
| +3 | Direct | 3.616s | +1.001s | 8.542s |
| +3 | Deterministic rollout | 4.275s | +3.711s | 11.698s |
| +3 | Probabilistic | 3.742s | -0.362s | 7.749s |
| +5 | Direct | 6.247s | +1.106s | 18.379s |
| +5 | Deterministic rollout | 8.473s | +7.072s | 21.471s |
| +5 | Probabilistic | 5.748s | -0.081s | 13.171s |

The empirical correction controls recursive PIT bias and improves the +5 tail, although +3
median MAE is slightly worse than the direct model.

## Calibration and sharpness

Final aggregate time calibration and mean interval widths:

| Horizon | Coverage 50/80/90 | Width 50/80/90 |
|---|---|---|
| +1 | 48.1% / 77.3% / 82.3% | 1.124s / 3.473s / 5.484s |
| +3 | 49.0% / 70.6% / 79.4% | 2.804s / 6.624s / 10.023s |
| +5 | 45.6% / 67.9% / 76.2% | 3.760s / 8.786s / 12.333s |

PIT-only 80% coverage is 57.6%, 64.4%, and 63.3%; its corresponding widths are 9.021s,
14.648s, and 17.580s. PIT 90% coverage is 64.4%, 86.7%, and 83.3%, with widths of 14.255s,
21.232s, and 25.012s. The intervals widen correctly but do not retain nominal final coverage.

## Position distributions

Overall position MAE is 0.746, 1.094, and 1.472 positions at +1/+3/+5. The 80% position-range
coverage is 81.8%, 79.5%, and 77.4%; 90% coverage is 84.6%, 84.0%, and 80.3%. Crossing outcomes
remain probabilistic through sampled timing relative to the local field. Unknown cars and
lapped states retain broad ranges rather than forced passes or fabricated seconds.

## Generalization and backmarkers

Albert Park is unseen in the fitting and selection circuits. Its final timing MAE is 0.520s,
1.989s, and 3.084s, with 80% coverage of 91.4%, 80.0%, and 82.4%. Its +5 bias is +2.714s, so
good coverage does not imply an unbiased point forecast. These timed cases are EXTEND cases;
unseen-circuit PIT timing remains unestablished.

For P16+, final timing MAE is 1.089s, 2.208s, and 3.327s. The 80% timing coverage declines from
78.2% to 67.4% to 63.2%. Position MAE is 0.500, 0.893, and 1.512 with +5 80% position coverage
of 72.6%. LAP_DEFICIT and UNKNOWN states never receive invented seconds gaps.

## Decision

The one-lap kernel is a better technical foundation than direct +5 PIT prediction because it
exposes state transitions, is reproducible, widens uncertainty with horizon, controls recursive
bias, and reduces PIT +5 tail error. It is not calibrated well enough for whole-race simulation:
final 80% and 90% coverage fail materially, PIT intervals are already wide, position coverage
degrades with horizon, and unseen-circuit PIT evidence is absent. Whole-race readiness remains
NO-GO.
