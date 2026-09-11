# Phase 12C — Human Transcript Benchmark

STATUS: COMPLETE — NO-GO FOR UNGATED AUTOMATED RADIO INTELLIGENCE

The frozen Phase 12C benchmark now contains 30/30 human reference transcripts and 60/60 human
semantic reviews. The protected XLSX import preserved the human references and both frozen ASR
outputs. No model was rerun or tuned after the results were observed.

## Frozen sample and integrity

- Clips: 30, in the original Phase 12B deterministic order
- Events: 2021 Bahrain (10), 2024 Bahrain (10), 2025 Abu Dhabi (10)
- Selection SHA-256: `111cb5264152d0ba1875363894e9b6c018b4f384cefd75ba03bbca129676c131`
- Frozen manifest SHA-256: `b3eccfec45adf50eb763395a1a35ea8aff0d916409fbc590bb13dcb8ef6b4b56`
- Phase 12B ASR SHA-256: `48378031d90efd950a784a8b4373cdecca7e42e7c90312d8e5d123a2114eb6ca`
- Human references: 30/30, `annotation_source: human`
- Semantic reviews: 60/60, `reviewer_source: human`

The repeated semantic import was refused, confirming that existing review labels cannot be silently
overwritten.

## Scoring method

WER and CER use Unicode NFKC normalization, case-folding, punctuation removal, and collapsed
whitespace. The exact token `[inaudible]` is a zero-cost wildcard and is excluded from the reference
denominator. Corpus rates aggregate edit errors and reference units; medians and percentiles are
computed across per-clip rates.

Three human markers are not the exact wildcard and therefore remain literal scored text under the
unchanged methodology:

- `60ff02552550ceacdd14`: `[inaudible just a beep]`, `UNUSABLE`
- `1b43ac3bb8e7475fb7c6`: `[nothing]`, `UNUSABLE`
- `4ce87c430c63376d8bb4`: `[gibberish]` inside a `CLEAR` transcript

The two `UNUSABLE` clips are reported separately and excluded from useful-audio conclusions. The
human wording was not rewritten.

## Accuracy

| Metric | small.en | medium.en |
|---|---:|---:|
| Overall corpus WER | 24.37% (145/595) | 21.85% (130/595) |
| Median per-clip WER | 18.18% | 20.37% |
| Per-clip WER P75 / P90 | 41.43% / 70.00% | 41.90% / 66.92% |
| Overall corpus CER | 15.97% (396/2,479) | 13.96% (346/2,479) |
| Median per-clip CER | 13.39% | 14.10% |
| CLEAR WER | 23.73% (140/590) | 21.02% (124/590) |
| CLEAR CER | 15.20% (373/2,454) | 13.12% (322/2,454) |
| UNUSABLE WER | 100.00% (5/5) | 120.00% (6/5) |
| UNUSABLE CER | 92.00% (23/25) | 96.00% (24/25) |
| Short `<3s` WER | 42.86% (3/7) | 57.14% (4/7) |
| Short `<3s` CER | 37.93% (11/29) | 51.72% (15/29) |
| `>=3s` WER | 24.15% (142/588) | 21.43% (126/588) |
| `>=3s` CER | 15.71% (385/2,450) | 13.51% (331/2,450) |

There are 28 `CLEAR`, zero `PARTIAL`, zero `POOR`, and two `UNUSABLE` clips. The useful-audio slice
therefore equals the `CLEAR` slice.

## Human semantic judgments

| Verdict | small.en | medium.en | Overall |
|---|---:|---:|---:|
| SAFE_EQUIVALENT | 5/30 (16.67%) | 7/30 (23.33%) | 12/60 (20.00%) |
| MINOR_ERROR | 4/30 (13.33%) | 10/30 (33.33%) | 14/60 (23.33%) |
| MATERIAL_ERROR | 21/30 (70.00%) | 13/30 (43.33%) | 34/60 (56.67%) |

`medium.en` is materially better on human semantic safety, but a 43.33% material-error rate is still
far too high for direct race-engineering use.

## Operational vocabulary and representative failures

The human reference workbook contains zero labelled `critical_terms`. Under the existing Phase 12C
keyword method, the exact denominator is therefore **0** and keyword accuracy is **not available**.
No keyword rate is inferred from ASR output or retroactively manufactured from the references.

Human-labelled `MATERIAL_ERROR` examples show operational risk despite that missing quantitative
keyword denominator:

- `4df79c5f836cfde079a0`: `small.en` changed “DRS” to “dearest”; `medium.en` retained DRS and received
  `MINOR_ERROR`.
- `8fb61c40c51e423dae24`: both models replaced “Verstappen” in a pit-stop-window message with “staff”
  or “Stefan”; both received `MATERIAL_ERROR`.
- `a10902306b8f99f9c26a`: `small.en` changed “fastest stint” to “fast system”; `medium.en` preserved the
  stint/medium context and received `SAFE_EQUIVALENT`.
- `001f755456fb4a3ffaa1`: both models changed “I can hear you” to “I can't hear you,” a negation error;
  both received `MATERIAL_ERROR`.
- `3f178622c44fe5af07c2`: the 2.424-second “Drop it off” became “Jumping off” or “I'm jumping off”; both
  received `MATERIAL_ERROR`.

These examples cover DRS, driver identity, pit-window context, stint terminology, tyre context,
negation, and short commands. They are representative of the human labels rather than a substitute
for the unavailable critical-term score.

## Hallucination and short clips

Both models hallucinated speech on both `UNUSABLE` references:

- The beep-only clip `60ff02552550ceacdd14` became “You” for both models.
- The no-speech clip `1b43ac3bb8e7475fb7c6` became “You” for `small.en` and “Thank you” for
  `medium.en`.

All four outputs received `MATERIAL_ERROR`. The existing `>0.6` no-speech diagnostic flagged all
four, so it is useful for these obvious cases. It does not solve the wider fluent-error problem.

There are three clips under three seconds. For each model, two were `MATERIAL_ERROR` and one was
`SAFE_EQUIVALENT`. Short-clip WER/CER is worse for `medium.en` despite its better full-sample results.
Very short clips should not be accepted automatically.

## Confidence and disagreement

| Existing gate | small.en | medium.en |
|---|---:|---:|
| Flagged outputs | 3/30 (10.00%) | 5/30 (16.67%) |
| Material rate when flagged | 100.00% | 80.00% |
| Material rate when unflagged | 66.67% | 36.00% |
| Recall of all material errors | 14.29% | 30.77% |

The flags are precise warnings but have inadequate recall. Most material errors appear in outputs
that were not flagged. Median log probability and no-speech probability are worse for material than
non-material outputs, but the overlap is too large for an ungated trust decision on this sample.

Phase 12B's predefined meaningful-disagreement threshold is token disagreement `>0.25`:

- Meaningful disagreement: 11 clips / 22 model reviews; material-error rate 72.73%; 10/11 clips had
  at least one material model output.
- No meaningful disagreement: 19 clips / 38 reviews; material-error rate 47.37%; 12/19 clips still had
  at least one material model output.

Disagreement is a useful warning signal, but agreement is not evidence of truth.

## Compute trade-off

Frozen Phase 12B CPU/int8 measurements:

| Metric | small.en | medium.en |
|---|---:|---:|
| Median processing time | 2.037s | 5.745s |
| P90 processing time | 2.810s | 9.129s |
| Audio-time / processing-time | 4.65x realtime | 1.64x realtime |
| Maximum process RSS | 734.8 MB | 1,257.9 MB |

`medium.en` reduces corpus WER by 2.52 percentage points, CER by 2.02 points, and material errors by
26.67 points, but costs about 2.82x median latency and 1.71x maximum RSS. If a model must be retained
for offline research, `medium.en` is the better evidence-backed candidate. Neither model is safe to
use without rejection and review controls.

## Decision

**NO-GO for automated downstream radio intelligence with the current models and diagnostics.**

The reason is not decode availability: both models decoded every clip. The blockers are the 43.33%
best-case material-error rate, dangerous operational substitutions, hallucinated speech, absent
validated keyword coverage, and existing confidence gates that recall only 30.77% of `medium.en`
material errors. Sparse recent provider coverage remains an additional product limitation.

Further isolated transcription/gating research could be proposed separately, but Phase 12C does not
authorize production transcript use or Phase 12D implementation.

Reproducible artifacts:

- `docs/phase12c-radio-evaluation.json`
- `docs/phase12c-radio-summary.json`
- `scripts/evaluate_phase12c.py`
- `scripts/summarize_phase12c.py`

No Replay, Live Pit Wall, API, RaceState, strategy service, or production radio behavior changed.
