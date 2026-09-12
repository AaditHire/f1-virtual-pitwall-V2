# F1 Virtual Pit Wall — Project State

Authoritative handoff through **Phase 13C**. Phase 13C is complete with a **CONDITIONAL GO for
continued research and controlled internal use** and a **NO-GO for a production/public chatbot**;
Phase 13D has not started. The latest functional Phase 13C commit is `d75f876`; the subsequent
`0e2df14` commit added only an accidental Excel temporary lock file, which this handoff cleanup
removes. The repository, checked-in reports, frozen JSON artifacts, and tests remain the ultimate
source of truth.

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
- There is no database, authentication, agent, production RAG, or MCP product feature. Phase 13A's
  historical retrieval package is isolated research only.

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

## Frontend Phase 8 baseline

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
- Phase 8B subsequently evolved the editorial surfaces while preserving the denser Pit Wall architecture and working data behavior; later Phase 9 and 10 sections describe further frontend additions.

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

STATUS: IMPLEMENTED; ORIGINAL APPROVAL CHECKPOINT NOT RECORDED

Git history shows that Phase 8B was implemented in `e0263d4` (`added proper front end UI`) and
refined in `abae9c8` (`refined front end`) before Phase 9 began. The implementation commit added the
five coordinated Phase 8B concept images under `frontend/docs/concepts/phase8b/`, updated the design
system, introduced the editorial assets/components, and redesigned Home, Weekend and Pit Wall
surfaces. Phase 9 and Phase 10 then extended that redesigned frontend with Replay and live
engineering/strategy workspaces.

The repository does not contain a commit message or report proving that the originally specified
explicit design-approval checkpoint occurred, so this handoff does not claim that process step. It
records only the verifiable concepts and implementation. The former `NOT STARTED` label was stale;
Phase 8B is not an outstanding future phase and was superseded chronologically by completed Phase 9
and Phase 10 frontend work.

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

STATUS: COMPLETE — NO-GO FOR UNGATED AUTOMATED RADIO INTELLIGENCE

- The exact 30 Phase 12B clips are frozen in a versioned manifest with selection SHA-256
  `111cb5264152d0ba1875363894e9b6c018b4f384cefd75ba03bbca129676c131`; an existing manifest
  cannot be silently replaced when the Phase 12B artifact changes.
- A local-only blind annotator exposes neutral clip IDs and on-demand audio, while withholding race
  context, ASR predictions, confidence, disagreement and flags. Draft/complete saves are resumable.
- Human references and post-reference human semantic reviews are separate from the unchanged Phase
  12B predictions. Strict schemas reject ASR fields and require `annotation_source: human`.
- Deterministic WER, CER, `[inaudible]` handling, human-labelled critical-term recovery and evaluation
  assembly are implemented. All 30 genuine human references and all 60 human semantic comparisons
  were imported through protected XLSX workflows. Repeat imports remain overwrite-protected.
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
- Final corpus WER/CER is 24.37%/15.97% for `small.en` and 21.85%/13.96% for `medium.en`.
  Human `MATERIAL_ERROR` rates are 70.00% and 43.33%, respectively. Existing confidence flags recall
  only 14.29% and 30.77% of those material errors. Both models hallucinated speech on both `UNUSABLE`
  clips. The human workbook contains zero labelled critical terms, so the existing keyword-accuracy
  denominator is zero and no keyword rate is reported.
- Phase 12C concludes **NO-GO for automated downstream radio intelligence with the current models and
  diagnostics**. `medium.en` is the stronger offline research candidate, but neither model is safe
  without rejection and human-review controls. Phase 12D has not started. Guide:
  `docs/phase12c-annotation-guide.md`; benchmark status: `docs/phase12c-human-benchmark.md`; recovery
  report: `docs/phase12c-transcript-recovery.md`; machine-readable evaluation:
  `docs/phase12c-radio-evaluation.json`; descriptive summary: `docs/phase12c-radio-summary.json`.

Preferred commands:

```powershell
.venv\Scripts\python -m scripts.export_phase12c_annotation_xlsx
.venv\Scripts\python -m scripts.import_phase12c_annotation_xlsx outputs\phase12c_annotation_pack\phase12c_manual_annotations.xlsx
.venv\Scripts\python -m scripts.import_phase12c_annotation_xlsx outputs\phase12c_annotation_pack\phase12c_manual_annotations.xlsx --confirm-import
.venv\Scripts\python -m scripts.export_phase12c_semantic_review_xlsx
.venv\Scripts\python -m scripts.import_phase12c_semantic_review_xlsx outputs\phase12c_semantic_review\phase12c_semantic_review.xlsx
.venv\Scripts\python -m scripts.import_phase12c_semantic_review_xlsx outputs\phase12c_semantic_review\phase12c_semantic_review.xlsx --confirm-import
```

## Phase 13A — Historical F1 RAG Foundation

STATUS: COMPLETE — CONDITIONAL GO FOR RETRIEVAL HARDENING ONLY

- An isolated `f1_pitwall.knowledge` research package defines provenance-first documents, deterministic
  section/fixed chunking, BM25, local dense LSA, reciprocal-rank hybrid retrieval, metadata filtering,
  retrieval metrics and a conservative structured-query boundary. It is not wired to any API or UI.
- The bounded 2021–2025 corpus contains 45 documents / 53 selected chunks: 20 Jolpica event
  classifications across Bahrain, Monaco, Monza and Yas Marina; 14 sampled driver-season summaries;
  four circuit histories; and seven concise FIA/Formula1.com explanatory summaries. No full article,
  radio record or ASR transcript is ingested.
- The frozen 40-question benchmark covers event history (20), driver/team (6), circuit/history (6),
  regulation/terminology (5) and multi-document context (3). Labels are deterministic/human-reviewed,
  not LLM-generated. Exact results remain `STRUCTURED_FACT` queries.
- Metadata-filtered section BM25 was the simplest best method: Recall@1 93.75%, Recall@3/5 95.00%,
  MRR 0.950 and median query latency about 0.06 ms. Dense LSA and hybrid retrieval tied filtered recall
  but did not improve it and were slower. Metadata filtering improved BM25 Recall@1 by 22.5 points.
- Two multi-event comparisons missed their required event documents in the top five. Coverage is too
  narrow for generation or a production chatbot. A reviewed Phase 13B may harden retrieval, broaden
  authoritative coverage and test explicit multi-period decomposition; it must not start automatically.
- Jolpica's non-commercial/share-alike data terms and volunteer-service limitations require review
  before product use. FIA/F1 copyrighted material is stored only as concise factual paraphrase plus
  provenance URL; redistribution rights are not assumed.
- Full report: `docs/phase13a-historical-rag-research.md`; frozen corpus/benchmark and machine-readable
  results: `docs/phase13a-knowledge-corpus.json`, `docs/phase13a-retrieval-benchmark.json`, and
  `docs/phase13a-retrieval-results.json`.

## Phase 13B — Historical F1 Retrieval Hardening

STATUS: COMPLETE — GO FOR A NARROW GROUNDED-ANSWER EXPERIMENT ONLY

- The Phase 13A corpus, benchmark and results remain frozen and hash-verified. Phase 13B adds separate
  artifacts covering all 114 Grands Prix from 2021–2025: 309 provenance-first documents and 317
  section-aware chunks across 35 drivers, 12 season-specific constructors and 28 circuits.
- The new sealed benchmark has 125 questions, including 20 that require all evidence from two
  documents and five explicit corpus-coverage failures. It distinguishes `STRUCTURED_ONLY`,
  `RAG_ONLY` and `MIXED` evidence needs and scores Evidence Coverage and Complete Evidence Success.
- Metadata-filtered BM25 generalized to 87.50% Recall@5. Explicit deterministic decomposition raised
  Recall@3/5 and multi-document Evidence Coverage/Complete Evidence Success@3/5 to 100%. It preserves
  named years/events/entities, rejects invented scopes, merges results round-robin and deduplicates by
  source. No LLM performs parsing, retrieval, ranking or answer generation.
- Route classification matched all 125 labels. Five unsupported private-retirement-cause questions
  remain `CORPUS_COVERAGE_FAILURE`; the selected method has no supported retrieval, routing,
  ambiguity, multi-document or source-conflict failure on this benchmark.
- Event metadata was the decisive ablation: removing it reduced applicable top-one accuracy from 100%
  to 4%. Season, driver and circuit removals did not affect the templated queries because their names
  remained strong lexical signals; those filters remain explicit guardrails.
- Constructor IDs remain season-specific. Renault/Alpine, Racing Point/Aston Martin and the Faenza
  lineage are documented as lineage relationships but never silently merged. Ambiguous aliases fail
  closed.
- Every Jolpica and official document is marked `PRODUCTION_REVIEW_REQUIRED`; this research makes no
  legal conclusion. No secondary journalism, full article, radio/ASR data, API, UI, agent, MCP,
  database or answer-generation dependency was added.
- Full report: `docs/phase13b-historical-rag-hardening.md`; artifacts:
  `docs/phase13b-knowledge-corpus.json`, `docs/phase13b-retrieval-benchmark.json`, and
  `docs/phase13b-retrieval-results.json`.

## Phase 13C — Grounded Historical Answer Generation

STATUS: COMPLETE — CONDITIONAL GO FOR RESEARCH/CONTROLLED INTERNAL USE; NO-GO FOR PUBLIC PRODUCTION

- The frozen 60-question benchmark retains SHA-256
  `969ff9d522a4f047493c1c1fc2ba9eaeddeb80f10988bd271f87078c7112809e`. Its route distribution is
  30 `STRUCTURED_ONLY`, 25 `RAG_ONLY` and five `MIXED` questions.
- Exact structured questions remain deterministic and consumed no provider requests. The other 30
  questions used AgentRouter's Anthropic-compatible Messages interface through the official
  `anthropic` SDK, fixed model `claude-opus-4-8`, and a forced non-executing `grounded_answer` tool.
- The one primary run completed all 60 rows using 30 AgentRouter requests, no retries and 80,753
  tokens. AgentRouter returned no cost or balance field. Six responses failed the frozen 320-token
  output contract; telemetry was retained and no answer was repaired or rerun.
- Deterministic results include 54/55 structured facts, 70/79 required facts, 79/79 valid emitted
  citations, 54/60 citation-complete answers, 55/60 citation-supported answers, 4/5 correct refusals,
  0/55 false refusals, 22/22 complete multi-document results, 2/2 conflict cases and 2/3 strict
  prompt-injection fixtures. Route compliance was 60/60.
- The protected 25-row human review imported without changes to questions, evidence, generated
  answers, citations or deterministic summaries. Grounding was 19 PASS, 0 MINOR_ISSUE and 6 FAIL;
  usefulness was 14 GOOD, 5 ACCEPTABLE and 6 POOR; all 25 were judged non-misleading. Excluding the
  six malformed/no-answer rows, grounding was 19/19 PASS.
- The tracked XLSX remains the original blank review template. The completed canonical judgments are
  versioned separately in `docs/phase13c-human-review-results.json`; validating the blank template as
  though it were a completed workbook correctly rejects its empty verdict cells.
- All six human failures were genuine malformed/no-answer reliability failures at the frozen
  320-token limit: 6/30 (20%) AgentRouter generations and 6/25 (24%) RAG_ONLY questions. They were
  not repaired or rerun. Human review judged prompt-injection handling 3/3, while the frozen strict
  matcher remains 2/3 because it flagged an injected phrase quoted only as rejected data. One MIXED
  required-fact miss was likewise a literal `yas_marina` string-matching limitation; frozen automated
  metrics remain unchanged.
- Phase 13C is a **CONDITIONAL GO** for continued research and controlled internal experimentation.
  `STRUCTURED_ONLY` should remain deterministic. RAG_ONLY and MIXED valid-answer behavior is
  promising, but the malformed rate, refusal/citation gaps, exact-fact guardrails and unresolved
  source/licensing review make this a **NO-GO for a production/public chatbot**.
- The frozen benchmark was not rerun and still has SHA-256
  `969ff9d522a4f047493c1c1fc2ba9eaeddeb80f10988bd271f87078c7112809e`. No production API,
  frontend, agent, MCP, radio context or deterministic race system changed. Full report:
  `docs/phase13c-grounded-answer-research.md`; human review:
  `docs/phase13c-human-review-results.json`.
