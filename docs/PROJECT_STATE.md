# F1 Virtual Pit Wall — Project State

Authoritative handoff through **Phase 8**. Current baseline: `main` at `0e6b0d9` (`build production F1 command-center frontend`). The repository, checked-in reports, JSON artifacts, and tests remain the ultimate source of truth.

## Architecture

```text
Jolpica / OpenF1 / RSS / FastF1 archives
  → provider adapters + bounded in-process TTL cache
  → normalized Pydantic domain models
  → causal replay / analysis / strategy / simulation services
  → FastAPI `/api/v1` routes
  → Next.js App Router frontend (`frontend/`)
```

- Python 3.12+, FastAPI, Pydantic, HTTPX, FastF1, scikit-learn/joblib.
- Provider-specific data stays in `src/f1_pitwall/providers/`; services consume normalized domain objects.
- Historical replay enforces an archive publication-time cutoff. Future data is used only as evaluation labels.
- Missing gaps, positions, tyres, weather, and timing remain missing or `UNKNOWN`; the system does not fabricate seconds gaps.
- There is no database, authentication, agent, RAG, or MCP product feature.

## Completed phases

- **Phase 1 — F1 data hub:** seasons, calendars, sessions, events, qualifying, grids, results, standings, news, provider health, timezone serialization, and `/home` aggregation.
- **Phase 2 — Historical replay:** causal full-grid race snapshots from timestamped FastF1 source streams.
- **Phase 3 / 3B — Deterministic analysis and calibration:** pace, tyres, traffic, pit loss, rejoin, undercut/overcut evidence; calibrated conservative zero-slope tactical forecast.
- **Phase 4 — Causal strategy policy:** legal PIT/EXTEND candidates, conservative gating, HOLD/INSUFFICIENT states, leakage protection, and historical comparison.
- **Phase 5 / 5B / 5C — Short-horizon transitions:** causal 1/3/5-lap action outcomes, error diagnosis, improved PIT transition model, and explicit uncertainty.
- **Phase 5D — Reliability envelope:** applicability labels and calibrated intervals; whole-race readiness remained NO-GO.
- **Phase 5E — Stochastic transition kernel:** reproducible probabilistic one-lap transitions anchored at 1/3/5 laps.
- **Phase 5F — Calibration/stress:** sampling, circuit/season, backmarker, and longer-rollout stress tests fixed the practical uninterrupted horizon at about five laps.
- **Phase 6 — Rolling full-grid Pit Wall:** re-anchors to real observations every lap, full-grid recommendations, rivals, deterministic alerts, history, and hysteresis.
- **Phase 6B — Paired decisions:** matched PIT-vs-EXTEND trajectories, equivalence bands, regret, pit windows, and paired uncertainty.
- **Phase 6C — Broad decision audit and narrow fix:** removed post-stop repeat-PIT behavior and prevented pit-cycle-only promotion to STRONG.
- **Phase 6D — Focused PIT signal experiment:** all three transparent candidates failed; none was integrated.
- **Phase 6E — Strategic stint value experiment:** common-horizon owed-stop model added as an isolated service; integration remained NO-GO.
- **Phase 7 — Current/live backend:** current-weekend aggregation and OpenF1 live-style observations feed the existing causal RaceState/PitWall pipeline.
- **Phase 8 — Frontend V1:** production Next.js dashboard for Home, Weekend, Live Pit Wall, Standings, and News.

## Model status and validation

**All strategy output remains `EXPERIMENTAL`. It is a decision aid, not an authoritative race instruction.**

- The validated tactical simulation surface is **approximately five laps**: outputs exist at +1, +3, and +5. Beyond five laps the model must re-anchor to newly observed state; autonomous +8 or whole-race continuation is not validated.
- Phase 3B selected a LOW-confidence zero-slope relative-pace forecast because fitted degradation slopes did not beat the validation baseline. The zero baseline recorded 0.506 s MAE at both +3 and +5.
- Phase 4 produced 33 decisive calls from 476 validation snapshots (6.9% coverage) and did **not** establish superiority over baselines.
- Phase 5E final probabilistic rollout recorded 3.244 s aggregate +5 MAE and 5.748 s PIT-only +5 MAE. Interval coverage remained below nominal targets; whole-race simulation was rejected.
- Phase 5F confirmed five laps as the practical uninterrupted horizon. Five hundred trajectories is adequate for routine tactical use; 1,000 improves reported PIT interval stability.
- Original Phase 6 evaluation covered 3,780 driver-lap observations and produced no PIT_NOW call under the original safeguards.
- Phase 6B produced PIT signals, but Phase 6C found that pit-cycle position did not converge better than physical position: 2.21-place vs 1.42-place MAE at factual-stop +5 checkpoints. After the narrow fix, 3,860 untouched green states produced zero STRONG/PIT_NOW calls.
- Phase 6D candidates did not separate near-stop states from controls. The held-out races stayed untouched and no candidate entered production.
- **Phase 6E remains isolated.** Its strategic hybrid did not beat the generic fresh-advantage baseline, its action separation was overconfident, and split-stop evidence did not support its predicted direction. It does not alter public Pit Wall recommendations.
- Phase 7 live/archive compatibility matched all 22 common drivers at the recorded final checkpoint for position, laps, compound, stint, and completed pit count. This does not prove intermediate publication-time equality.

### Approaches that are not validated

- Whole-race or autonomous long-horizon simulation.
- Phase 6E strategic stint recommendations or public Pit Wall integration.
- Phase 6D PIT-opportunity candidates.
- Pit-cycle position as an independently reliable decision signal.
- Historical team stops as counterfactual optimality labels.
- Tyre degradation slopes as superior to the zero-slope baseline.
- Unseen-circuit PIT timing, robust backmarker timing, and nominal +5 interval calibration.
- Safety Car/VSC, red flag, rain, reliability-failure, tyre-inventory, or fuel-effect strategy modeling.

## Important backend APIs

| Area | Endpoints |
| --- | --- |
| Process | `GET /health` |
| Hub | `GET /api/v1/home`, `/news`, `/providers/status` |
| Season/event | `/seasons*`, `/events/current`, `/events/next`, `/sessions/next`, `/events/{year}/{round}/*`, `/results/latest`, `/standings/*` |
| Replay | `GET /api/v1/replay/{year}/{round}/laps`, `/{lap}`, `/{lap}/drivers/{driver_id}`, `/{lap}/radio`, `/{lap}/drivers/{driver_id}/radio` |
| Analysis | `GET /api/v1/analysis/{year}/{round}/{lap}/drivers/{driver_id}` plus `/tyres`, `/traffic`, `/undercut`, `/overcut` |
| Strategy/simulation | `GET /api/v1/strategy/...`; `POST /api/v1/simulation/short-horizon`, `/transition`, `/rollout` |
| Historical Pit Wall | `GET /api/v1/pitwall/{year}/{round}/timeline`, `/{lap}`, `/{lap}/drivers/{driver_id}` |
| Current/live | `GET /api/v1/weekend/current`, `/live/status`, `/live/race`, `/live/pitwall`, `/live/pitwall/drivers/{driver_id}`, `/live/weather`, `/live/race-control` |

Calendar/current endpoints accept `?timezone=Asia/Kolkata`; canonical timestamps remain UTC.

## Current live backend

```text
OpenF1 LiveTimingProvider ─┐
                          ├→ timestamped HistoricalRace → RaceState → existing PitWallService
FastF1 timing archive ────┘
```

- `LiveService` converts provider capabilities into the internal causal history; analysis, simulation, and Pit Wall services remain shared with replay mode.
- Scheduled time alone never means LIVE. Recent observations determine `LIVE`, `DELAYED`, `HISTORICAL_ONLY`, or `UNAVAILABLE`.
- Every current payload exposes source/retrieval/provider timestamps, data age, and `FRESH`, `DELAYED`, `STALE`, or `UNKNOWN` freshness.
- `/live/pitwall` requires an active Race plus drivers, positions, laps, stints, track evidence, and at least three reported laps. Otherwise it returns partial state and `missing_requirements` without inventing a recommendation.
- Endpoints fetch on request; there is no backend polling loop. Provider reads use bounded in-process TTL caching.

## Frontend through Phase 8

- Location: `frontend/`; Next.js 16 App Router, React 19, TypeScript, Tailwind CSS.
- Routes: `/`, `/weekend`, `/pitwall`, `/standings`, `/news`. Replay is visible as “later” and has no fake implementation.
- Home and Weekend use Server Components for initial data. `LivePitWall` is the interactive Client Component for polling and driver selection.
- Typed API modules live in `frontend/src/lib/api/`; server requests use `API_BASE_URL`, browser requests use the `/backend/*` same-origin rewrite.
- Live refresh is non-overlapping, slows outside live sessions, pauses while the tab is hidden, supports manual refresh, and preserves stale/partial/offline states.
- Home shows current/next event, countdown, dynamic Sprint-compatible session timeline, compact standings, real news, and live status.
- Weekend shows schedule/status, qualifying, grid, latest results, weather, and race control with partial-provider handling.
- Pit Wall supports honest offline mode and a live-shaped full grid, selected-driver detail, tyres, pace, traffic, paired outcomes, alerts, weather, race control, and freshness. Driver detail uses `/live/pitwall/drivers/{driver_id}`.
- Loading, empty, error/retry, stale, partial, keyboard/focus, semantic table, ARIA, and responsive states are implemented and tested.

## Current design system

- Dark engineering baseline: graphite `#080c0f`, charcoal surfaces, fine `#28333a` rules, white text, muted cool gray.
- Accent/status: motorsport red `#ef3340`, live green `#46d47b`, caution amber `#ffb82e`.
- Typography: Barlow Condensed for display, IBM Plex Sans for UI, IBM Plex Mono only for time/numeric data.
- Open horizontal bands, dense tables, restrained 3–4 px radii, visible focus, text labels alongside color, and reduced-motion support.
- Phase 8B is expected to evolve the editorial surfaces substantially while preserving the denser Pit Wall architecture and all working data behavior.

## Run and verify

Backend:

```powershell
.venv\Scripts\python -m uvicorn f1_pitwall.main:app --app-dir src --reload
.venv\Scripts\python -m pytest -q
.venv\Scripts\ruff check src tests scripts
.venv\Scripts\ruff format --check src tests scripts
.venv\Scripts\python scripts/smoke.py
```

Network tests are explicit and can be slow/provider-dependent; do not run expensive historical audits unless required.

Frontend:

```powershell
cd frontend
npm install
npm run dev
npm run typecheck
npm run lint
npm test
npm run build
```

Phase 8 recorded 8 passing Vitest tests plus successful typecheck, ESLint, and production build. Re-run commands rather than treating recorded counts as current truth.

## Phase 8B — UI/UX Redesign

STATUS: NOT STARTED

Phase 8B goal:  
Use Open Design to redesign the existing frontend using the current VPW UI as the structural reference and Formula1.com only as visual inspiration.

- Screenshots 1–3 are the existing VPW frontend.
- Screenshot 4 is Formula1.com inspiration only.
- First generate coordinated Open Design concepts for Home desktop, Weekend desktop, Pit Wall desktop, Home mobile, and Pit Wall mobile.
- Stop for explicit design approval before implementation.
- Preserve routes, APIs, polling, freshness, real-data behavior, accessibility, and tests. Do not begin Phase 9.

## Phase 9A / 9B / 9C — Historical Replay

STATUS: PHASE 9C COMPLETE

- `/replay` provides season, event, leader-lap and full-grid historical navigation with an immutable browser request cache.
- The selected-driver engineering workspace consumes the existing Phase 3 driver, undercut and overcut analysis routes. It does not calculate engineering metrics in TypeScript.
- Race state, pace inputs, current tyre/stint state, traffic, pit loss and estimated rejoin geometry are shown with backend confidence and explicit observed/estimated provenance.
- Undercut/overcut remains supporting LOW-confidence context only.
- Predictive tyre degradation, tyre-life, cliff and health values are intentionally absent because prior validation did not support them.
- Analysis loading/errors are isolated from the RaceState grid. Lap scrubbing remains bounded and superseded analysis results are ignored.
- Phase 9B validation: 18 frontend tests, 136 backend tests, 23 focused Phase 3 tests, the Bahrain 2024 raw-prefix causal regression, typecheck, ESLint, Ruff and production build pass.
- Phase 9C exposes the existing historical Pit Wall driver detail in the selected-driver workspace. It reuses the backend recommendation, confidence state, pit-window gate, paired PIT-versus-EXTEND outcomes, alerts, and embedded Phase 3 analysis; no strategy logic is duplicated in TypeScript.
- Historical strategy is explicitly EXPERIMENTAL and limited to the existing +1/+3/+5 tactical horizon. Every lap change re-anchors to its causal RaceState; +5 is not presented as a whole-stint or whole-race optimization.
- Strategy failure is isolated from both the timing grid and Phase 3 engineering analysis. Exact driver/lap responses share the bounded immutable replay cache, while adjacent laps trigger a fresh backend re-anchor.
- Phase 9C validation: 22 frontend tests, 136 backend tests, 10 focused Pit Wall tests (including future-mutation causality, normal-stop cooldown and position-only gating), TypeScript, ESLint, Ruff, and production build pass.

## Phase 10A — Live Pit Wall Engineering & Strategy UX

STATUS: COMPLETE

- `/pitwall` now follows the live operational hierarchy Race State → selected-driver engineering → EXPERIMENTAL short-horizon strategy while retaining the dynamic full-grid timing view.
- The selected driver uses the existing `/live/pitwall/drivers/{driver_id}` response and its embedded Phase 3 analysis. Shared Replay presentation components render pace, tyre/stint, traffic, pit/rejoin, confidence, pit-window, alerts and paired +1/+3/+5 evidence without duplicating model logic in TypeScript.
- A single non-overlapping polling loop refreshes provider status first, then live RaceState, full-grid Pit Wall, support data and the selected-driver detail. Driver and refresh generations reject superseded responses so an older request cannot replace a newer lap or selection.
- `LIVE_AVAILABLE`, `DELAYED_AVAILABLE`, `HISTORICAL_ONLY` and `UNAVAILABLE` remain backend-authoritative. Delayed RaceState stays usable with amber freshness context; retained secondary analysis is explicitly marked stale and cannot appear fresher than its RaceState.
- Timing remains independently available when engineering or strategy fails. Mobile view tabs preserve intentional timing-grid scrolling while avoiding page-level overflow.
- No backend route, model, threshold, infrastructure or production fixture was added for Phase 10A.

## Phase 11A — Pre-Race Simulation Research

STATUS: COMPLETE — NO-GO FOR PHASE 11B

- The isolated study uses 35 cached FastF1 race archives plus Jolpica grid/qualifying/results metadata: 17 races / 339 driver rows for 2021–2023 training, 7 / 140 for 2024 development, and 11 / 219 for the untouched 2025 holdout.
- The pre-race boundary permits grid, qualifying, identity/circuit and strictly-prior event aggregates. Target-race laps, pace, stints, stops, weather, incidents and results are labels only. Focused tests verify target and same-event labels cannot enter features.
- Starting grid beat every finishing-rank candidate on development (2.214 vs best-model 2.371 position MAE). The selected Ridge model narrowly improved holdout MAE (3.425 to 3.342) but failed the required cross-period generalization gate.
- Circuit-history baselines beat the tested models for finishing bands, first-stop timing, strategy family and incident/survival. Pit-stop count improved only on holdout, not development. No target beat its baseline on both splits.
- First-stop uncertainty was too broad to be useful (development-calibrated 80% half-width about 17.7 laps); finishing-rank intervals also under-covered (72.1% holdout coverage for a nominal 80% interval).
- Phase 11B is not justified by current evidence. No simulator, model artifact, production service, API, frontend integration or tactical-kernel change was added.
- Full report: `docs/phase11a-pre-race-research.md`; machine-readable metrics: `docs/phase11a-pre-race-research.json`.

## Phase 12A — Radio Intelligence Foundation

STATUS: COMPLETE — CONDITIONAL GO FOR HISTORICAL RESEARCH ONLY

- FastF1 `TeamRadio` metadata is normalized into the existing cached `HistoricalRace`; radio absence
  remains an empty optional capability and does not destroy Replay RaceState.
- Each `RadioRecord` uses the archive packet publication time as `available_at`, joins racing number
  through the session roster, exposes only a sanitized official MP3 reference, and maps to the latest
  leader-lap cutoff at or before publication. No filename-derived timing or future lap is inferred.
- Replay radio APIs support full-session and per-driver cutoff-safe feeds with bounded latest-N
  filtering. No OpenF1 current/live route is exposed because the repository lacks authenticated
  streaming and trustworthy radio delay/freshness semantics.
- Real audit: 2021 Bahrain 138 messages/20 drivers; 2024 Bahrain 146/20; 2025 Abu Dhabi 22/6. All 306
  normalized records had provider timestamps and official audio references; sampled MP3s returned
  `audio/mpeg` without downloading the archive.
- Radio metadata contains no transcript or complete-conversation guarantee. Coverage is sharply
  variable and OpenF1 reports significant reduction from 2026 onward.
- Full report and mapping rule: `docs/phase12a-radio-foundation.md`; reproducible audit:
  `scripts/audit_phase12a_radio.py`.

## Phase 12B — Historical Radio Transcription Research

STATUS: COMPLETE — CONDITIONAL GO; HUMAN TRANSCRIPT BENCHMARK REQUIRED

- An isolated `faster-whisper` experiment compared `small.en` and `medium.en` on the same 30
  deterministic Phase 12A clips: ten each from 2021 Bahrain, 2024 Bahrain and 2025 Abu Dhabi,
  representing 17 drivers and 306.384 seconds of audio.
- Actual CUDA inference failed because `cublas64_12.dll` was unavailable, so both models ran on CPU
  INT8. `small.en` median/P90 latency was 2.037/2.810 seconds versus 5.745/9.129 for `medium.en`;
  observed process RSS peaked at 734.8 MB and 1,257.9 MB respectively.
- Both models decoded all 30 clips with no empty transcript, but 11/30 (36.7%) had greater than 25%
  token disagreement. Low-confidence indicators affected 10.0% / 16.7%, and simple rules missed
  some fluent but suspect text. Cross-model agreement remains diagnostic, not accuracy.
- No human reference transcripts exist, so WER, CER and motorsport-keyword accuracy were not
  calculated. Names and technical phrases showed material instability. VAD provided no evidence of
  improvement on a six-clip comparison and remains off.
- No production API, frontend, radio mapping, analysis, strategy or Pit Wall behavior changed. Full
  report: `docs/phase12b-radio-transcription-research.md`; review artifacts:
  `docs/phase12b-radio-asr.json` and `docs/phase12b-radio-asr-review.csv`.

## Phase 12C — Human Transcript Benchmark

STATUS: PAUSED FOR HUMAN XLSX ANNOTATION

- The exact 30 Phase 12B clips are frozen in a versioned manifest with selection SHA-256
  `111cb5264152d0ba1875363894e9b6c018b4f384cefd75ba03bbca129676c131`; an existing manifest
  cannot be silently replaced when the Phase 12B artifact changes.
- A local-only blind annotator exposes neutral clip IDs and on-demand audio, while withholding race
  context, ASR predictions, confidence, disagreement and flags. Draft/complete saves are resumable.
- Human references and post-reference human semantic reviews are separate from the unchanged Phase
  12B predictions. Strict schemas reject ASR fields and require `annotation_source: human`.
- Deterministic WER, CER, `[inaudible]` handling, human-labelled critical-term recovery and evaluation
  assembly are implemented, but evaluation is blocked until 30 real human references and 60 human
  semantic comparisons exist.
- A metadata-only external-reference recovery pass researched all 30 clips without parsing or using
  ASR content. RaceFans yielded four `VERIFIED_LIKELY` human-editorial candidates; provider-message
  identity and full-clip coverage were not strong enough for `VERIFIED_EXACT`. All four are
  strategy-critical and require listening-based human confirmation. The other 26 clips remain
  `NO_REFERENCE` and require blind manual transcription.
- External candidates, review decisions, canonical references, and promotion provenance are separate
  protected artifacts. Likely and strategy-critical candidates cannot auto-promote, and an existing
  canonical reference cannot be silently overwritten.
- The preferred human-ground-truth workflow is now the blind XLSX annotation pack. The export command
  creates `outputs/phase12c_annotation_pack/phase12c_manual_annotations.xlsx` plus 30 exact local MP3
  working files using only the frozen sanitized audio references. All transcript, usability, speaker,
  critical-term and notes cells start blank; no ASR or recovered transcript text is included.
- The XLSX importer performs a no-write validation pass by default. It verifies the selection hash,
  exact clip order, immutable metadata, local audio formulas and human fields, then requires an
  explicit `--confirm-import` before using the existing protected canonical-reference mechanism.
  Incomplete workbooks and silent overwrites are refused.
- Canonical progress remains 0/30, so no accuracy metrics or GO/NO-GO claim is available. Guide:
  `docs/phase12c-annotation-guide.md`; benchmark status: `docs/phase12c-human-benchmark.md`; recovery
  report: `docs/phase12c-transcript-recovery.md`.

Preferred commands:

```powershell
.venv\Scripts\python -m scripts.export_phase12c_annotation_xlsx
.venv\Scripts\python -m scripts.import_phase12c_annotation_xlsx outputs\phase12c_annotation_pack\phase12c_manual_annotations.xlsx
.venv\Scripts\python -m scripts.import_phase12c_annotation_xlsx outputs\phase12c_annotation_pack\phase12c_manual_annotations.xlsx --confirm-import
```
