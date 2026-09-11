# Phase 12C manual radio annotation guide

## Preferred workflow: Excel annotation pack

Generate the blind local workbook and its exact frozen audio files from the repository root:

```powershell
.venv\Scripts\python -m scripts.export_phase12c_annotation_xlsx
```

Open:

```text
outputs\phase12c_annotation_pack\phase12c_manual_annotations.xlsx
```

Use the `Annotations` sheet from row 1 through row 30:

1. Click `Open audio`.
2. Listen without opening Phase 12B predictions or recovered transcript candidates.
3. Type exactly what you hear in `Human Transcript`.
4. Preserve repetitions and spoken shorthand. Do not clean up grammar.
5. Enter `[inaudible]` for a genuinely unclear span instead of guessing.
6. Choose `Usability` and `Speaker` from their dropdowns.
7. Optionally enter audible critical phrases separated by semicolons, for example
   `BOX; MEDIUM; LAP 24`, and add a brief note if useful.
8. Save the workbook after all 30 rows are complete.

All human input columns start blank. The workbook contains no `small.en`, `medium.en`, model
confidence, disagreement, suspicious flags, or external recovered transcript suggestions. The local
MP3 files and workbook are annotation working files under an ignored `outputs` directory. They do
not replace the frozen manifest or canonical repository artifacts.

Validate the completed workbook without writing canonical references:

```powershell
.venv\Scripts\python -m scripts.import_phase12c_annotation_xlsx outputs\phase12c_annotation_pack\phase12c_manual_annotations.xlsx
```

After the dry run reports 30 completed rows, perform the explicit protected import:

```powershell
.venv\Scripts\python -m scripts.import_phase12c_annotation_xlsx outputs\phase12c_annotation_pack\phase12c_manual_annotations.xlsx --confirm-import
```

The importer checks the frozen hash, exact IDs and order, immutable metadata, audio mapping,
dropdown values, required transcript/usability/speaker fields, and critical-term presence. It refuses
unknown or duplicate clips, incomplete workbooks, and silent replacement of an existing canonical
reference. Do not use `--overwrite-empty-workbook` after beginning annotation; that export option is
only for intentionally regenerating a known-empty workbook.

## Alternate browser workflow

The original browser annotator remains available but is no longer the preferred workflow.

Start it with:

From the repository root:

```powershell
.venv\Scripts\python -m scripts.annotate_phase12c
```

The tool binds only to `127.0.0.1:8765`. It streams one official audio clip through a bounded local proxy and does not permanently download the full dataset. Progress is saved to `docs/phase12c-radio-human-references.json`; an immediately preceding version is retained as a `.bak` file after subsequent saves.

Do not open `docs/phase12b-radio-asr.json` or the Phase 12B review CSV while annotating. The annotation UI deliberately provides only a neutral clip ID and audio. It never sends race identity, driver identity, lap context, ASR text, confidence, disagreement, or suspicious-output flags to the browser.

## Review recovered human-source candidates first

The separate recovery pass found four `VERIFIED_LIKELY` human-editorial candidates. Review them with:

```powershell
.venv\Scripts\python -m scripts.review_phase12c_recovery
```

This local tool binds to `127.0.0.1:8766` and shows independent event/driver/lap metadata, the original audio, the external candidate, its source, and matching evidence. It never loads or shows either ASR prediction or diagnostic. Listen to the whole clip before accepting; all four current candidates contain strategy-critical content and therefore require human confirmation. A candidate that is wrong, incomplete, or not uniquely matched must be rejected or routed to manual transcription.

Accepted candidates are still not canonical references until explicitly promoted with:

```powershell
.venv\Scripts\python -m scripts.promote_phase12c_reference CLIP_ID --confirm
```

Promotion refuses unresolved candidates and silent overwrites. The 26 `NO_REFERENCE` clips continue through the blind annotation flow below. See `docs/phase12c-transcript-recovery.md` for exact IDs and provenance.

## Literal reference rules

- Listen to the audio and write natural English that preserves what was spoken.
- Do not improve grammar, expand shorthand, or substitute what race context suggests.
- Preserve repetitions: spoken “box box” is `box box`.
- Do not force names, numbers, `DRS`, compounds, or technical settings from hindsight.
- For a genuinely unintelligible span, enter `[inaudible]` instead of guessing. The insert button uses this exact marker.
- Save a draft whenever useful. Mark a clip complete only after the transcript, usability, and speaker type have been reviewed.

During scoring, punctuation and case are normalized. `[inaudible]` matches an arbitrary hypothesis span at zero edit cost and does not contribute to the WER/CER denominator. A reference consisting only of `[inaudible]` has no WER/CER score.

## Additional human labels

Audio usability:

- `CLEAR`: all or nearly all speech is intelligible with little interference.
- `PARTIAL`: most content is intelligible, but one or more spans are uncertain or inaudible.
- `POOR`: only limited fragments are reliable because of noise, clipping, overlap, or radio quality.
- `UNUSABLE`: no reliable spoken reference can be produced.

Speaker type:

- `DRIVER`, `ENGINEER`, or `MIXED` only when reasonably identifiable.
- Use `UNKNOWN` when uncertain; do not infer identity from context.

After the literal reference is written, add operationally critical terms only when their exact text occurs in that human reference. Categories cover names, numbers, laps, compounds, pit/box and stay-out calls, DRS, safety car/VSC, delta, brake balance, differential, engine/strategy modes, wing adjustments, and other operational content.

Notes are optional and should describe uncertainty or audio quality, never an ASR suggestion.

## Completed review workflow

All 30 human references are now complete and protected. Generate the separate post-reference semantic
review workbook with:

```powershell
.venv\Scripts\python -m scripts.export_phase12c_semantic_review_xlsx
```

Open `outputs\phase12c_semantic_review\phase12c_semantic_review.xlsx`. It contains 60 rows: each
frozen clip paired once with `small.en` and once with `medium.en`. Read the frozen human reference
and model prediction, then choose `SAFE_EQUIVALENT`, `MINOR_ERROR`, or `MATERIAL_ERROR`. Notes are
optional. Do not edit either source-text column.

Validate without writing:

```powershell
.venv\Scripts\python -m scripts.import_phase12c_semantic_review_xlsx outputs\phase12c_semantic_review\phase12c_semantic_review.xlsx
```

After all 60 rows pass, import explicitly:

```powershell
.venv\Scripts\python -m scripts.import_phase12c_semantic_review_xlsx outputs\phase12c_semantic_review\phase12c_semantic_review.xlsx --confirm-import
```

The importer verifies the frozen selection, row mapping, human references, and Phase 12B prediction
text before accepting labels. It refuses incomplete workbooks and silent overwrites. Neither the
reference transcripts nor semantic verdicts may be generated by ASR, Codex, or another LLM.

This workflow is now complete: all 30 references and all 60 semantic reviews are present. The final
Phase 12C result is documented in `docs/phase12c-human-benchmark.md`. Do not rerun either import with
an overwrite option unless a separately reviewed correction is explicitly authorized.
