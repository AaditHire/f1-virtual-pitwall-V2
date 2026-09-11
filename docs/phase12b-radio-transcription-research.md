# Phase 12B — Historical Radio Transcription Research

## Decision

**CONDITIONAL GO — HUMAN TRANSCRIPT BENCHMARK REQUIRED.**

Local ASR is technically practical on the tested hardware and produced readable text for many
longer clips, but this experiment cannot establish accuracy. The repository and providers contain
no trustworthy human reference transcripts, 36.7% of clips had substantial cross-model token
disagreement, and simple confidence rules missed some plausible-but-suspect outputs. Phase 12C
should not interpret radio until a human-labelled benchmark measures actual accuracy and defines a
safe rejection policy.

No production API, Replay UI, Live Pit Wall, strategy, Phase 3 analysis, PitWallService, or Phase
12A causal behavior was changed.

## Runtime and models

Hardware:

- AMD Ryzen 7 6800HS, 8 cores / 16 logical processors;
- 16 GB system RAM;
- NVIDIA GeForce RTX 3060 Laptop GPU, 6 GB VRAM.

Software and configuration:

- `faster-whisper` 1.2.1 and CTranslate2 4.8.2;
- `Systran/faster-whisper-small.en`, revision
  `d1d751a5f8271d482d14ca55d9e2deeebbae577f`;
- `Systran/faster-whisper-medium.en`, revision
  `a29b04bd15381511a9af671baec01072039215e3`;
- English fixed, beam size 5, temperature 0, `condition_on_previous_text=false`;
- VAD off by default; one worker; CTranslate2 automatic CPU thread count;
- sequential processing of the same selected clips for both models.

CTranslate2 detected the GPU and advertised FP16, but actual inference failed its preflight because
`cublas64_12.dll` was unavailable. The harness fell back to CPU INT8. No failed GPU transcript was
accepted into the results.

## Dataset

The deterministic selector chose ten time-spread clips per event while preferring drivers not yet
selected within that event:

| Event | Clips | Drivers represented |
| --- | ---: | ---: |
| 2021 Bahrain | 10 | 10 |
| 2024 Bahrain | 10 | 10 |
| 2025 Abu Dhabi | 10 | 6 |

The 30 clips cover 17 unique drivers and 306.384 seconds of audio. Duration ranged from 1.632 to
30.360 seconds, with an 8.052-second median. All 30 sanitized Phase 12A references fetched
successfully. Audio lived only in a bounded temporary cache and was deleted when the run finished;
no audio is committed or redistributed.

The sample naturally included engineer instructions, driver responses, short calls, long
instructions, noisy/low-speech clips, names, pit-limiter language, tyres, differential, deployment,
DRS, stint discussion, lap times, and end-of-race conversation. These categories were not used as
prompts or automatic corrections.

## Automatic results

| Metric | `small.en` | `medium.en` |
| --- | ---: | ---: |
| Decode success | 30/30 (100%) | 30/30 (100%) |
| Empty transcript | 0/30 | 0/30 |
| Low-confidence flag | 3/30 (10.0%) | 5/30 (16.7%) |
| High no-speech probability (>0.6) | 3/30 (10.0%) | 4/30 (13.3%) |
| Any suspicious-output flag | 3/30 (10.0%) | 5/30 (16.7%) |
| Median inference | 2.037 s | 5.745 s |
| P90 inference | 2.810 s | 9.129 s |
| Total inference | 65.918 s | 187.400 s |
| Audio-time / processing-time ratio | 4.65× realtime | 1.64× realtime |
| Maximum observed process RSS | 734.8 MB | 1,257.9 MB |

Both models are faster than realtime on CPU for sequential individual clips. `medium.en` took about
2.84 times as long as `small.en`, consumed about 523 MB more observed process memory, and triggered
more low-confidence flags. The larger model did not provide an automatic reliability advantage.

The model pair had a median normalized token edit distance of 20.8%. Eleven of 30 clips (36.7%)
exceeded the experiment's 25% meaningful-disagreement threshold. Agreement is only diagnostic; it
is not accuracy.

No decode produced the repetition, excessive-words-for-duration, or language-mismatch rule. This
does not show those outputs were accurate. In particular, simple confidence thresholds failed to
flag several semantically awkward but fluent results.

## Qualitative and vocabulary audit

The longer, clearer clips frequently produced coherent engineering language, including pit limiter,
tailwind under braking, differential, tyre drop-off, deployment, lap-time and stint context.
However, comparison exposed material instability in exactly the domain-sensitive areas:

- names and entities varied: `Norris` / `Norse`, `Piastri` / `Piastry`, `Lando` / `Lano`, and an
  apparent `Stroll` reference with an extra name-like token;
- technical phrases varied: `DRS` / `dearest`, `fastest stint` / `fast system`, and a likely car
  setting rendered as `Red A11` / `Red Air 11`;
- a 3.288-second clip produced substantially different plausible sentences about Max;
- a 6.096-second noisy clip produced two different fluent but semantically unstable instructions;
- the 1.632-second clip produced `You` versus `Thank you`; both models reported high no-speech
  probability;
- one 8.088-second low-speech clip produced only `You` from both models, showing that agreement can
  repeat the same unhelpful output.

Only `pit` differed under the deliberately small predefined vocabulary-presence audit. That count
understates vocabulary risk because spelling variants and semantically different phrases can still
contain the same keyword. No context-based correction was applied.

These are model disagreements and qualitative warning cases, not reference-validated errors.

## VAD comparison

`small.en` was rerun with VAD enabled on the same six-clip subset:

- all six decoded with VAD on and off;
- median latency was 2.168 s with VAD versus 2.091 s without;
- two of six VAD transcripts differed from the non-VAD transcript by more than 25% of tokens;
- VAD changed wording in short/noisy examples but provided no reference-backed accuracy evidence.

There is no basis to enable VAD by default. The research configuration remains VAD off.

## Ground-truth boundary

Neither FastF1, OpenF1, nor this repository supplied human reference transcripts for the selected
clips. Therefore:

- WER was not calculated;
- CER was not calculated;
- motorsport-keyword accuracy was not calculated;
- one ASR model was never treated as ground truth for the other;
- decode success, confidence, disagreement, and qualitative coherence must not be described as
  transcription accuracy.

The next legitimate validation step is a blinded human transcript set with uncertain/inaudible spans
explicitly marked, followed by WER, CER, key-term accuracy, and rejection calibration on a held-out
subset.

## Research artifacts

- `scripts/research_phase12b.py` — deterministic selection, bounded audio retrieval, local ASR,
  diagnostics, comparison, and artifact generation;
- `docs/phase12b-radio-asr.json` — complete machine-readable configuration, causal metadata,
  transcripts, diagnostics, and comparisons;
- `docs/phase12b-radio-asr-review.csv` — compact human-review table without embedded audio;
- `tests/unit/test_phase12b_research.py` — research infrastructure coverage.

The JSON/CSV retain the Phase 12A session ID, driver ID, `available_at`, causal leader lap, and
sanitized audio reference for every result. Raw ASR text is preserved separately from whitespace-only
normalization.

## Limitations and required gate

- Thirty clips are enough for an initial feasibility signal, not a domain accuracy claim.
- The deterministic time-spread sample was not human-labelled by clarity, speaker, accent, or noise.
- The 2025 event could represent only six drivers because source coverage was sparse.
- English-only models and forced English make language detection non-informative.
- Confidence/no-speech thresholds are heuristic and did not catch every suspicious fluent output.
- CPU latency is practical for historical batch work, but live operational latency was not tested.
- GPU feasibility remains unresolved until compatible CUDA/cuBLAS runtime libraries are installed.
- Remote audio availability and Phase 12A's incomplete provider selection remain upstream risks.
- Model downloads remain in the user's external Hugging Face cache; temporary source audio was
  removed.

Phase 12C should proceed only after the human benchmark demonstrates that usable clips are mostly
coherent, technical vocabulary is acceptable, and unsafe hallucinations can be rejected. It must not
start from the current 100% decode rate as though that were 100% accuracy.

