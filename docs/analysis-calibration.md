# Phase 3B analysis calibration

This phase calibrates the Phase 3 tyre, fresh-tyre and stop-offset models. It does not make strategy recommendations. The reproducible evaluation is `scripts/calibrate_analysis.py`; its complete sample-level report is written to `.cache/analysis-calibration.json` and is intentionally excluded from Git because it is several megabytes. The script records every grouping and sample needed to reproduce the tables below.

## Chronological design

Candidates were selected on four 2023 development races: Bahrain, Monaco, Spain and Monza. The final report uses five later 2024 races: Bahrain, Suzuka, Monaco, Spain and Monza. No race-specific constants were fitted, and the production tyre/fresh-tyre models use no historical race archive at runtime. Within a replay, every reference and stop sample must have been published by the requested cutoff. Future laps are labels inside the evaluator only.

The set covers conventional, high-degradation, high-speed, street and low-overtaking layouts. Rolling samples occur every five leader laps where the subject remains on the same stint. Forecast horizons are three and five racing laps.

## Diagnosis

Phase 3 fitted a robust slope to raw lap time. The Phase 3B target is more precise: change in a driver's pace relative to the contemporaneous field over the next three or five racing laps. For driver `d` on race lap `n`:

`reference(d,n) = median(clean lap time of every suitable driver except d on lap n)`

The reference requires at least five other drivers. `relative_pace(d,n) = lap_time(d,n) - reference(d,n)`. Leaving the subject out prevents its own tyre behavior from moving its reference. This removes broad fuel/track evolution without assuming a fuel-burn constant.

Across all 2,296 development and validation samples, median raw slope was −0.007 s/lap and median normalized slope was +0.041 s/lap. Their −0.060 s/lap median difference is consistent with a substantial common race effect. That common-effect proxy correlates 0.563 with signed raw-model error. Normalization therefore reduces confounding and bias, but its fitted slopes remain too noisy to forecast better than no change.

Absolute raw-model error correlates weakly with tyre age (0.051), stint length (0.044), and clean-lap count (0.021). Reference coverage is more useful (correlation −0.288). Error is not confined to a single compound or traffic label:

| Combined group | n | Zero MAE | Raw MAE | Normalized MAE |
|---|---:|---:|---:|---:|
| Hard | 1,392 | 0.489 | 0.587 | 0.561 |
| Medium | 607 | 0.522 | 0.657 | 0.580 |
| Soft | 259 | 0.458 | 0.520 | 0.593 |
| Clear air | 511 | 0.529 | 0.733 | 0.665 |
| Heavy traffic | 449 | 0.531 | 0.724 | 0.599 |
| Early race | 590 | 0.453 | 0.712 | 0.511 |
| Middle race | 966 | 0.538 | 0.571 | 0.581 |
| Late race | 740 | 0.539 | 0.796 | 0.684 |

Wet/intermediate data is sparse and unstable: 34 development intermediate samples have 1.447s zero-slope MAE and 5.355s raw-slope MAE. Driver/team slices also vary materially (among groups with at least 30 samples, raw MAE ranges from 0.543s for STR to 1.080s for SAR, and from 0.586s for Alpine to 0.894s for Williams). These are diagnostics, not driver/team coefficients.

The clean-lap rules remain conservative: pit in/out, non-green control periods, explicit deletions, impossible timing, and start laps are excluded. No new traffic deletion was added. Current archive intervals cannot prove that an earlier lap was compromised, and removing laps merely because their times disagree would bias the fit. Traffic status remains an exposed diagnostic grouping.

Warm-up exclusion is not stable across chronological splits. On five-lap forecasts, keeping all clean full laps is best in development (0.620s MAE versus 0.630/0.635 when skipping one/two); skipping two is best in validation (0.602s versus 0.620 keeping all). The disagreement is small and reverses by split, so production does not hard-code a first- or second-flying-lap exclusion. Pit in/out laps remain excluded.

## Tyre model comparison and selection

All candidates predict future **relative pace**, anchored to the median of the latest three reference-covered current-stint laps. The target is the median relative pace of clean same-stint laps available within the next K racing laps. Model A predicts no change. B applies the current all-stint raw Theil-Sen slope. C applies the all-stint normalized Theil-Sen slope. D applies the last-five normalized slope.

| Development model (2023) | n | MAE | Median AE | Bias |
|---|---:|---:|---:|---:|
| Zero slope | 1,018 | **0.529** | **0.324** | −0.207 |
| Raw all-stint | 1,018 | 0.730 | 0.397 | −0.370 |
| Normalized all-stint | 1,018 | 0.591 | 0.396 | −0.022 |
| Recent normalized, 5 laps | 1,018 | 0.724 | 0.489 | −0.071 |

| Validation model (2024) | n | MAE | Median AE | Bias |
|---|---:|---:|---:|---:|
| Zero slope | 1,278 | **0.506** | **0.364** | −0.112 |
| Raw all-stint | 1,278 | 0.640 | 0.467 | −0.199 |
| Normalized all-stint | 1,278 | 0.601 | 0.457 | +0.134 |
| Recent normalized, 5 laps | 1,278 | 0.818 | 0.610 | +0.170 |

Validation MAE for zero/raw/normalized/recent is 0.506/0.614/0.581/0.748 at three laps and 0.506/0.665/0.620/0.888 at five laps. Zero wins every validation circuit: Bahrain 0.379s, Suzuka 0.461s, Monaco 0.703s, Spain 0.431s, and Monza 0.399s. The result is not driven by one circuit.

Production therefore uses a zero-slope short-horizon forecast. `degradation_sec_per_lap`, `expected_3_lap_pace_loss`, and `expected_5_lap_pace_loss` are `0.0` only when at least five normalized laps span four laps; confidence is LOW. The warning states that zero is a conservative forecast assumption, not evidence of zero physical degradation. With less evidence, all forecast values are null and confidence is INSUFFICIENT. Raw, normalized, and recent-normalized slopes remain visible as diagnostic components. Competitive life is unavailable because no fitted trend beat the baseline.

No tyre forecast receives MEDIUM/HIGH confidence: the held-out zero-model MAE is 0.506s. Confidence uses actual reference coverage and the measured historical model error rather than the former residual/count threshold.

## Fresh-tyre model

The empirical target is the normalized median of up to three pre-stop clean laps minus the normalized first clean full post-stop lap. The out-lap is excluded; the first full lap intentionally includes observed warm-up effects. Samples come only from qualified stops already completed in the current race. They match the subject's current old compound. If an optional proposed new compound is supplied, exact old-to-new transitions are preferred; otherwise the estimator pools matching old-compound transitions and exposes each transition and tyre age.

Primary historical scores use the pooled estimator and do not reveal the eventual compound to the model. The actual transition is retained only as an evaluation grouping. Callers may supply `new_compound` as an explicit scenario input; that optional path is not the basis of the primary score.

| Split | n | Zero-delta MAE | Raw estimator MAE | Normalized estimator MAE | Normalized median AE | Normalized bias |
|---|---:|---:|---:|---:|---:|---:|
| Development 2023 | 86 | 1.232 | 0.909 | **0.845** | 0.617 | +0.389 |
| Validation 2024 | 105 | 1.399 | 0.972 | **0.911** | 0.708 | +0.362 |

Median evidence is four stops. Normalization improves both chronological splits and beats the no-gain baseline, so it replaces the Phase 3 raw estimator. Error remains close to a second and biased high; confidence is capped at LOW. No completed matching stop returns INSUFFICIENT, never a fixed bonus. `warm_up_delta_seconds` reports the empirical first-full-lap difference versus later post-stop clean laps when available.

Transition slices are mixed. Normalization improves MEDIUM→HARD from 0.825s to 0.672s in development and HARD→HARD from 1.017s to 0.859s in validation, but slightly worsens development SOFT→HARD (1.114s to 1.260s). Old-age buckets also reverse: 20+ laps is strongest in development (0.720s) and weakest in validation (1.104s). The estimator therefore exposes transition/age evidence without fitting separate constants to these small groups.

## Undercut and overcut calibration

Pair validation uses adjacent cars that actually stopped one leader lap apart. Outcomes are relative order after both exits; a usable time gap supplies margin error. YES/NO are decisive; MARGINAL abstains from the confusion matrix, and UNKNOWN records insufficient evidence. This avoids inflating classification accuracy with the majority negative class.

| Split/model | Cases | Numeric | Unknown | TP | FP | TN | FN | Margin MAE |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| Development undercut | 17 | 3 | 14 | 0 | 1 | 1 | 1 | 2.419s |
| Validation undercut | 19 | 2 | 17 | 1 | 0 | 0 | 1 | 2.227s |
| Development overcut | 18 | 3 | 15 | 0 | 0 | 3 | 0 | 1.625s |
| Validation overcut | 9 | 1 | 8 | 0 | 0 | 1 | 0 | 2.685s |

Actual positive prevalence is 29.4%/31.6% for development/validation undercuts and 0%/11.1% for overcuts. Coverage is too low and margin error too high to claim reliable calibration. Production retains the transparent conditional equation and LOW confidence, now fed by normalized empirical fresh-tyre evidence. Missing reference, stop, gap or rejoin evidence returns UNKNOWN. No confidence upgrade or recommendation logic is justified. Overcut has only one numeric later holdout and no validated positive example.

## Rejoin preservation

The Phase 3 rejoin code is unchanged. Expanded evaluation covers 225 pre-stop range cases and 77 exact point estimates across nine races. Aggregate exact MAE is 1.013 positions, median error 1, 90th percentile 2, and 76.6% are within one position. Mean distance outside the predicted range is 0.698 positions.

| Race | Range cases | Exact cases | Exact MAE | P90 error |
|---|---:|---:|---:|---:|
| Bahrain 2023 | 34 | 12 | 0.917 | 2 |
| Monaco 2023 | 14 | 0 | unavailable | unavailable |
| Spain 2023 | 37 | 20 | 0.700 | 2 |
| Monza 2023 | 21 | 15 | 0.800 | 2 |
| Bahrain 2024 | 24 | 0 | unavailable | unavailable |
| Suzuka 2024 | 31 | 0 | unavailable | unavailable |
| Monaco 2024 | 3 | 0 | unavailable | unavailable |
| Spain 2024 | 34 | 15 | 0.533 | 1 |
| Monza 2024 | 27 | 15 | 2.200 | 6 |

The aggregate remains close to one position and improves on several circuits, but Monza 2024 exposes a material tail failure. Missing/lapped-car gaps often suppress point estimates, especially at Monaco, Bahrain 2024 and Suzuka. These limitations remain explicit rather than being patched with circuit-specific constants.

## Reproduction

```powershell
.venv\Scripts\python scripts/calibrate_analysis.py --cached
.venv\Scripts\python -m pytest -q -p no:cacheprovider
.venv\Scripts\python -m pytest tests/integration/test_analysis_live.py -m network -q -p no:cacheprovider
.venv\Scripts\ruff check src tests scripts
.venv\Scripts\ruff format --check src tests scripts
.venv\Scripts\python scripts/smoke.py --analysis 2023 14 19
```

The `--cached` option reuses normalized archives but reconstructs every causal snapshot. Without it, archives are loaded through the existing provider and saved beneath `.cache/`. Phase 4 is not included.
