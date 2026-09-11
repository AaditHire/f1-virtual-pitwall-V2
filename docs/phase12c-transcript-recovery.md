# Phase 12C — External transcript-reference recovery

STATUS: COMPLETE RECOVERY PASS; HUMAN BENCHMARK REMAINS BLOCKED

This extension searched for trustworthy, human-editorial transcripts for the existing 30 frozen
radio clips. It did not regenerate audio, rerun ASR, alter predictions, or populate the canonical
human-reference file. External text is evidence until it passes the controlled human review and
promotion workflow.

## Frozen-artifact baseline

Recorded before implementation:

- Git HEAD: `04af1c7711904e11a6ecd5d7d890659bc8758c04`
- frozen manifest file SHA-256:
  `b3eccfec45adf50eb763395a1a35ea8aff0d916409fbc590bb13dcb8ef6b4b56`
- frozen selection SHA-256:
  `111cb5264152d0ba1875363894e9b6c018b4f384cefd75ba03bbca129676c131`
- Phase 12B predictions SHA-256:
  `48378031d90efd950a784a8b4373cdecca7e42e7c90312d8e5d123a2114eb6ca`
- canonical human-reference file SHA-256:
  `1482917f3d3e1c7f10db5d71a87acb927ab8762956f81e8e93131fc7e9169e23`
- frozen clips: 30 unique IDs; canonical references: 0

The final verification section records the post-change values.

## Isolation and architecture

The recovery flow has three explicit layers:

1. `phase12c-radio-external-references.json` stores public human-source candidates, provenance,
   metadata-only matching evidence, search attempts, and verification status.
2. `phase12c-radio-recovery-reviews.json` stores explicit human accept/reject/manual decisions.
3. `phase12c-radio-human-references.json` remains the protected canonical benchmark reference set.

`scripts/recover_phase12c_references.py` loads only the frozen manifest. It records the Phase 12B
artifact's byte hash for regression provenance but never parses that artifact. The external artifact
loader rejects prediction- and diagnostic-shaped fields. Matching rules use event, session, driver,
causal lap, provider publication time, recording identifier, and source context only. No external
quote is compared to `small.en`, `medium.en`, or any other machine transcript.

`VERIFIED_EXACT` requires a human-editorial literal quote, full clip coverage, and positive event,
session, driver, temporal, and unique-message association. Partial quotes cannot be exact. Conflicts
require human review. `VERIFIED_LIKELY` and strategy-critical exact candidates require an explicit
listening-based human acceptance. Promotion requires a separate confirmation command and refuses to
silently overwrite an existing canonical reference. Provenance remains in a linked promotion file so
the existing canonical schema and evaluator stay unchanged.

## Search method and source coverage

All 30 clips received one targeted metadata-only search attempt in each source family. Queries used
season, event, race session, driver, causal leader lap where present, and radio/transcript terms.

| Source family | Clips searched | Usable candidates | Exact | Likely |
| --- | ---: | ---: | ---: | ---: |
| RaceFans | 30 | 4 | 0 | 4 |
| Official Formula 1 | 30 | 0 | 0 | 0 |
| Other reputable sources | 30 | 0 | 0 | 0 |

RaceFans yielded a human-editorial, lap-indexed 2025 Abu Dhabi radio table for Norris, Piastri, and
Verstappen. It did not expose the archived provider message timestamp or audio identifier, and some
lap windows contained multiple messages. Four candidates therefore remain `VERIFIED_LIKELY`, never
exact. Official F1 event features were useful context but did not provide complete human-written
literal transcripts for the frozen clips. A 2025 third-party archive explicitly described its text
as speech-recognition output and was rejected. YouTube auto-captions, search snippets alone,
paraphrases, forums, social media, and unclear/machine sources were not accepted.

One RaceFans retrieval returned HTTP 429 during direct access. This is logged as a source-access
limitation and was not interpreted as evidence that a transcript does not exist. The repository does
not have a stable approved search-provider API, so the 30-clip research was performed once using
Codex web search and serialized into the reproducible deterministic artifact; the script does not
pretend to automate public search.

## Results

- `VERIFIED_EXACT`: 0
- `VERIFIED_LIKELY`: 4
- `NO_REFERENCE`: 26
- `REFERENCE_CONFLICT`: 0
- partial candidates: 0 separately classified; uncertain coverage is part of all four likely records

All four likely candidates are strategy-critical and require the reviewer to listen to the frozen
audio before acceptance:

- `a10902306b8f99f9c26a` — Max Verstappen, leader lap 33
- `270e9276bb1642362d98` — Oscar Piastri, leader lap 42
- `58873bfeacf456d1c406` — Lando Norris, leader lap 54
- `dbb3be6dae4048ad9e94` — Max Verstappen, leader lap 58

The 26 `NO_REFERENCE` clips are:

```text
a4b32135bee93aff528d e379bf084fef0c643829 1cf6244e289cf4991d69
935cfc6715c26d86b3b7 001f755456fb4a3ffaa1 65176951b41734010473
b4504c321116794ed3bb 05dd996c421ffc8b7786 ec52b364072158e403a3
310beefb2cff2aedd802 60ff02552550ceacdd14 070ec2cab07ac3b116f2
b1c56a1f4f165175213b 4ce87c430c63376d8bb4 202806d2e08329702a94
bdb53de0cb61662f0801 01d1686be3a2f9127bca 3f178622c44fe5af07c2
627f97ccf0d5a7fb5ac9 db5ddd59bc986b6f80c1 8bcc172f800623c510a4
1b43ac3bb8e7475fb7c6 5bbfc3b54b34c8ee7635 ae858548b54923d2291b
4df79c5f836cfde079a0 8fb61c40c51e423dae24
```

No external candidate is presently safe to promote without review. Human work remaining is exactly:

- 4 clips need quick listening-based confirmation or correction;
- 26 clips need full blind listening/transcription;
- 0 clips are safely covered by an exact, non-critical external reference.

If a likely candidate is rejected or incomplete after listening, it returns to full manual
transcription. The existing blind annotator remains authoritative for all unresolved clips.

## Review and promotion

Start the contextual external-candidate review tool:

```powershell
.venv\Scripts\python -m scripts.review_phase12c_recovery
```

It binds to `127.0.0.1:8766`, prioritizes likely/critical/conflict records, and shows only frozen
audio, independent metadata, human-source candidate text, provenance, and matching reason. It does
not load or expose machine outputs. For `NO_REFERENCE`, use the existing blind annotator instead.

After an explicit accepted review, promote one candidate with:

```powershell
.venv\Scripts\python -m scripts.promote_phase12c_reference CLIP_ID --confirm
```

The command refuses unresolved candidates, missing human confirmation, and existing references
unless an explicit reviewed override is provided. It writes canonical and linked provenance files
only after all validation succeeds.

## Contamination incident and mitigation

During initial repository discovery, a broad text-search command unintentionally printed one Phase
12B review-CSV row for the first frozen clip. That text was not used in a web query, candidate match,
classification, or transcript recovery; that clip remains `NO_REFERENCE`. Subsequent research used
manifest metadata only. The implemented recovery script never parses prediction content, the review
payload contains no machine fields, and tests enforce both boundaries. This incident is disclosed so
the benchmark provenance remains auditable.

## Completion boundary

The canonical set remains 0/30. External candidates do not count as trusted references. Final WER,
CER, critical-term accuracy, semantic comparison, and model selection remain blocked until all 30
human references have been completed or explicitly accepted under the rules above. No Phase 12D work
was started.

## Final verification

- frozen manifest file SHA-256:
  `b3eccfec45adf50eb763395a1a35ea8aff0d916409fbc590bb13dcb8ef6b4b56`
- frozen selection SHA-256:
  `111cb5264152d0ba1875363894e9b6c018b4f384cefd75ba03bbca129676c131`
- Phase 12B predictions SHA-256:
  `48378031d90efd950a784a8b4373cdecca7e42e7c90312d8e5d123a2114eb6ca`
- canonical human-reference file SHA-256:
  `1482917f3d3e1c7f10db5d71a87acb927ab8762956f81e8e93131fc7e9169e23`
- frozen clips: 30 unique IDs in original order; canonical references: 0; review decisions: 0;
  promotions: 0
- focused Phase 12A/12B/12C regression: 39 passed
- complete backend suite: 180 passed, 21 intentionally deselected provider/network tests
- Ruff lint, focused formatting check, compile check, and `git diff --check`: passed
- evaluator guard: correctly refused to run with 30 human references missing; no evaluation artifact
  exists
- browser QA: 1440×1000 desktop, 390×844 mobile, and interactive in-app-browser pass;
  real frozen audio loaded and played, isolated save/resume worked, no visible horizontal overflow,
  and no unexpected console error was observed
