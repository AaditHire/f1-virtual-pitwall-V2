# Research log

This page holds the phase-by-phase research history that used to open the README. The product
overview, setup and API are in the [README](../README.md). The authoritative handoff remains
[PROJECT_STATE.md](PROJECT_STATE.md).

## Status

The authoritative handoff is [docs/PROJECT_STATE.md](PROJECT_STATE.md). The repository is
implemented through **Phase 13D-D Experiment C automated evaluation**:

- Phases 1–10 deliver the backend, causal replay and strategy research surfaces, current/live data,
  and the frontend application.
- Phases 11–12 cover pre-race simulation and historical radio-transcription research with explicit
  negative findings and safety gates.
- Phase 13C is closed with a **CONDITIONAL GO** for research and controlled internal use and a
  **NO-GO** for a production/public chatbot.
- Phase 13D-B Experiment A is complete, including genuine human review. The narrow
  320-versus-640-token budget effect is supported, but the full preregistered H1 is not supported.
- Phase 13D-C Experiment B recovered 11/30 frozen mechanical failures with one identical retry, but
  left 19/100 final failures and missed several automated guardrails. Human review found 10
  Grounding PASS, one MINOR, zero FAIL, and zero misleading recovered answers. Mechanical recovery
  is supported but heterogeneous; human safety passes with a minor quality qualification, while
  absolute reliability and automated quality fail.
- Phase 13D-D Experiment C recovered all 22 original truncations with one 1280-token fallback and
  reached 0/100 unrecovered in a diagnostic candidate policy. Required-fact coverage still failed,
  the benchmark is development data rather than an untouched holdout, and genuine review of all 22
  fallback answers is pending.
- Experiment C is **PAUSED — HUMAN REVIEW REQUIRED**. The production/public chatbot remains
  **NO-GO**.

## Historical replay (Phase 2)

FastF1 supplies cached, timestamped race archives. Replay returns the complete session field at the first reported completion of leader lap N, using only facts published by that instant. Phase 1 endpoints remain unchanged.

```powershell
Invoke-RestMethod 'http://127.0.0.1:8000/api/v1/replay/2024/1/laps'
Invoke-RestMethod 'http://127.0.0.1:8000/api/v1/replay/2024/1/25'
# Use a driver.id returned by the snapshot:
Invoke-RestMethod 'http://127.0.0.1:8000/api/v1/replay/2024/1/25/drivers/f1:MAXVER01'
```

Optional configuration: `F1_REPLAY_CACHE_DIR` (default `.cache/fastf1`) and `F1_REPLAY_CACHE_SIZE` (default 4 normalized sessions per process). First loads require network access; later requests reuse the normalized session. Downloaded archives use FastF1's disk cache.

```powershell
.venv\Scripts\python -m pytest -q -p no:cacheprovider
.venv\Scripts\python -m pytest -m network -q -p no:cacheprovider
.venv\Scripts\python scripts/smoke.py --replay 2024 1 25 > replay-output.json
.venv\Scripts\python scripts/replay_table.py replay-output.json
```

Add `--replay-only` to the smoke command to exercise only health and replay endpoints. This avoids unrelated homepage providers; the full regression tests still run separately. OpenF1 may require authentication globally during live F1 sessions, which can temporarily block existing Phase 1 network checks.

See [replay semantics and limitations](replay.md) and the [full-grid example](replay-sample.md).

## Deterministic analysis (Phase 3)

Analysis uses the same cached replay, filtered at the requested leader-lap cutoff. It exposes clean pace, current-stint tyre trends, observed pit loss, approximate rejoin geometry, traffic, and conditional undercut/overcut margins with evidence and confidence. Missing evidence returns null and warnings. No strategy recommendations or future-race simulation are included.

```powershell
Invoke-RestMethod 'http://127.0.0.1:8000/api/v1/analysis/2023/14/19/drivers/f1:CHALEC01'
Invoke-RestMethod 'http://127.0.0.1:8000/api/v1/analysis/2023/14/19/drivers/f1:CHALEC01/tyres'
Invoke-RestMethod 'http://127.0.0.1:8000/api/v1/analysis/2023/14/19/drivers/f1:CHALEC01/traffic'
Invoke-RestMethod 'http://127.0.0.1:8000/api/v1/analysis/2023/14/19/undercut?attacker=f1:CHALEC01&target=f1:CARSAI01'
Invoke-RestMethod 'http://127.0.0.1:8000/api/v1/analysis/2023/14/19/overcut?driver=f1:CHALEC01&target=f1:CARSAI01'
.venv\Scripts\python scripts/analysis_sample.py 2023 14 19 LEC SAI
.venv\Scripts\python scripts/smoke.py --analysis 2023 14 19
.venv\Scripts\python scripts/evaluate_analysis.py --output docs/analysis-validation.json
```

See [analysis methods, confidence and validation](analysis.md). Historical evaluation uses later laps only as labels, outside the runtime engine. The tyre trend did **not** outperform the zero-slope baseline in the recorded evaluation.

Phase 3B therefore selects a LOW-confidence zero-slope relative-pace forecast while exposing fitted slopes as diagnostics. It also replaces the raw fresh-tyre estimate with a leave-one-driver-out field-normalized estimate that improved on later 2024 holdouts. See the [Phase 3B calibration report](analysis-calibration.md).

## Rolling Virtual Pit Wall (Phase 6)

The Pit Wall evaluates the complete observed grid with the existing causal analysis, policy, and
stochastic transition services. Each new lap is rebuilt from real archived observations; simulated
states never feed the next decision. Outcomes are limited to the validated 1-, 3-, and 5-lap
envelope.

```powershell
Invoke-RestMethod 'http://127.0.0.1:8000/api/v1/pitwall/2024/1/25'
Invoke-RestMethod 'http://127.0.0.1:8000/api/v1/pitwall/2024/1/25/drivers/f1:MAXVER01'
Invoke-RestMethod 'http://127.0.0.1:8000/api/v1/pitwall/2024/1/timeline?start_lap=20&end_lap=25'
.venv\Scripts\python scripts/smoke.py --pitwall 2024 1 25
```

See the [architecture and operating limits](pitwall.md) and the recorded
[four-race rolling evaluation](pitwall-evaluation.json).

Phase 6B replaces independent marginal-interval comparison with matched PIT-versus-EXTEND
trajectories, empirical equivalence bands, expected regret, and a structured pit-window model.
See the [paired Pit Wall report](phase6b-paired-pitwall.md),
[threshold calibration](phase6b-paired-calibration.json), and
[frozen 2025 evaluation](phase6b-paired-evaluation.json).

Phase 6C adds the [broad historical decision audit](phase6c-historical-decision-audit.md),
[frozen 27-race evidence](phase6c-frozen-audit.json),
[untouched fix validation](phase6c-fix-validation.json), and
[full-grid performance runs](phase6c-performance.json). Its current decision is NO-GO for
current/live integration pending a defensible pit-cycle or short-horizon PIT signal.

Phase 6D compares three transparent, pit-cycle-independent opportunity formulations in the
[focused PIT signal report](phase6d-focused-pit-opportunity.md) and
[development evidence](phase6d-development.json). None separated near-stop states from
matched controls, so the held-out races remain untouched and Phase 7 remains NO-GO.

Phase 6E adds a separate [strategic stint-value model report](phase6e-strategic-stint-value.md),
[chronological validation](phase6e-validation.json), and frozen empirical
[stint priors](../src/f1_pitwall/models/strategic_stint_priors.json). Common-horizon owed-stop
accounting works, but holdout forecasts do not beat the generic baseline, so public Pit Wall
recommendations remain unchanged.

## Current/live weekend backend (Phase 7)

Current mode auto-detects the event and provider session. Scheduled start time alone never marks a
session live. Every live response includes explicit availability and freshness, and missing timing
remains missing. The same `PitWallService` accepts FastF1 archive history and OpenF1 live-style
history; current strategy output is always labelled **EXPERIMENTAL**.

```powershell
Invoke-RestMethod 'http://127.0.0.1:8000/api/v1/weekend/current?timezone=Asia/Kolkata'
Invoke-RestMethod 'http://127.0.0.1:8000/api/v1/live/status?timezone=Asia/Kolkata'
Invoke-RestMethod 'http://127.0.0.1:8000/api/v1/live/race'
Invoke-RestMethod 'http://127.0.0.1:8000/api/v1/live/pitwall'
```

See the [Phase 7 backend contract](phase7-current-live-backend.md),
[live/archive compatibility result](phase7-live-historical-consistency.json), and
[runtime measurements](phase7-performance.json).

## Frontend and replay UX (Phases 8–10)

Phase 8 introduced the Next.js dashboard and Phase 8B implemented the current visual redesign;
the original approval checkpoint was not recorded separately. Phases 9A–9C added the historical
Replay workspace, and Phase 10A completed the live Pit Wall engineering and strategy UX. Frontend
rendering preserves backend uncertainty, missing data, and experimental labels rather than
inventing recommendations.

See the [frontend guide](../frontend/README.md) and [design system](../frontend/docs/design-system.md).

## Research tracks (Phases 11–13D)

- **Phase 11A:** Pre-race simulation research completed with a NO-GO for Phase 11B.
- **Phases 12A–12C:** Historical radio ingestion, transcription, and human benchmarking completed;
  ungated automated radio intelligence remains NO-GO.
- **Phases 13A–13B:** Historical retrieval foundation and hardening completed for narrow research.
- **Phase 13C:** Grounded answer generation completed with a CONDITIONAL GO for controlled research
  and a production/public chatbot NO-GO.
- **Phase 13D-B Experiment A:** Paired prospective output-budget evaluation and human review
  completed. The narrow effect is supported; full H1 is not supported.
- **Phase 13D-C Experiment B:** One identical retry recovered 11/30 mechanical failures. Human review
  passed with a minor quality qualification; 19/22 original truncations remained unrecovered.
- **Phase 13D-D Experiment C:** One 1280-token fallback recovered 22/22 original truncations and the
  diagnostic candidate policy reached 0/100 unrecovered. Automated required-fact coverage failed,
  human review is pending, and a new untouched holdout is required for any readiness claim.

The frozen metrics, benchmark hashes, human-review state, and all negative experimental findings
are maintained in [docs/PROJECT_STATE.md](PROJECT_STATE.md) and the phase-specific reports.
