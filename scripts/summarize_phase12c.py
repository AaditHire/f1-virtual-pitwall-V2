"""Build reproducible descriptive summaries for the frozen Phase 12C evaluation."""

from __future__ import annotations

import argparse
import json
import statistics
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

from scripts.phase12c_benchmark import (
    INAUDIBLE,
    MODELS,
    atomic_write,
    scored_units,
    wildcard_edit_distance,
)
from scripts.phase12c_xlsx import file_sha256


def quantile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * fraction
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    weight = position - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


def corpus_error(rows: list[dict], *, character: bool) -> dict:
    errors = 0
    denominator = 0
    for row in rows:
        reference = scored_units(row["reference_transcript"], character)
        hypothesis = scored_units(row["raw_transcript"], character)
        errors += wildcard_edit_distance(reference, hypothesis)
        denominator += sum(unit != INAUDIBLE for unit in reference)
    return {
        "errors": errors,
        "reference_units": denominator,
        "rate": errors / denominator if denominator else None,
    }


def metric_summary(rows: list[dict]) -> dict:
    wer_values = [row["wer"] for row in rows if row["wer"] is not None]
    cer_values = [row["cer"] for row in rows if row["cer"] is not None]
    return {
        "rows": len(rows),
        "wer": corpus_error(rows, character=False),
        "cer": corpus_error(rows, character=True),
        "per_clip_wer": {
            "median": statistics.median(wer_values) if wer_values else None,
            "p25": quantile(wer_values, 0.25),
            "p75": quantile(wer_values, 0.75),
            "p90": quantile(wer_values, 0.90),
        },
        "per_clip_cer": {
            "median": statistics.median(cer_values) if cer_values else None,
            "p25": quantile(cer_values, 0.25),
            "p75": quantile(cer_values, 0.75),
            "p90": quantile(cer_values, 0.90),
        },
    }


def semantic_summary(rows: list[dict]) -> dict:
    counts = Counter(row["semantic_label"] for row in rows)
    return {
        label: {"count": counts[label], "rate": counts[label] / len(rows)}
        for label in ("SAFE_EQUIVALENT", "MINOR_ERROR", "MATERIAL_ERROR")
    }


def gate_summary(rows: list[dict], predicate) -> dict:
    flagged = [row for row in rows if predicate(row)]
    unflagged = [row for row in rows if not predicate(row)]
    material = [row for row in rows if row["semantic_label"] == "MATERIAL_ERROR"]
    flagged_material = [row for row in flagged if row["semantic_label"] == "MATERIAL_ERROR"]
    return {
        "flagged": len(flagged),
        "flag_rate": len(flagged) / len(rows),
        "material_error_rate_flagged": len(flagged_material) / len(flagged) if flagged else None,
        "material_error_rate_unflagged": (
            sum(row["semantic_label"] == "MATERIAL_ERROR" for row in unflagged) / len(unflagged)
            if unflagged
            else None
        ),
        "material_error_recall": len(flagged_material) / len(material) if material else None,
    }


def build_summary(evaluation: dict, phase12b: dict, paths: dict[str, Path]) -> dict:
    rows = evaluation["rows"]
    by_model = {}
    for model in MODELS:
        selected = [row for row in rows if row["model_id"] == model]
        slices = {}
        for usability in ("CLEAR", "PARTIAL", "POOR", "UNUSABLE"):
            subset = [row for row in selected if row["usability"] == usability]
            slices[usability] = metric_summary(subset) if subset else {"rows": 0}
        slices["SHORT_LT_3S"] = metric_summary([row for row in selected if row["short_clip"]])
        slices["LONGER_GTE_3S"] = metric_summary([row for row in selected if not row["short_clip"]])
        slices["USEFUL_NON_UNUSABLE"] = metric_summary(
            [row for row in selected if row["usability"] != "UNUSABLE"]
        )
        keyword_rows = [row["keyword_score"] for row in selected]
        labelled_terms = sum(row["labelled_terms"] for row in keyword_rows)
        matched_terms = sum(row["matched_terms"] for row in keyword_rows)
        by_model[model] = {
            "overall": metric_summary(selected),
            "slices": slices,
            "semantic": semantic_summary(selected),
            "keywords": {
                "labelled_terms": labelled_terms,
                "matched_terms": matched_terms,
                "exact_recovery": matched_terms / labelled_terms if labelled_terms else None,
            },
            "gates": {
                "any_suspicious_flag": gate_summary(
                    selected, lambda row: bool(row["suspicious_flags"])
                ),
                "low_confidence_flag": gate_summary(
                    selected, lambda row: "low_confidence" in row["suspicious_flags"]
                ),
                "no_speech_probability_gt_0_6": gate_summary(
                    selected,
                    lambda row: (row["maximum_no_speech_probability"] or 0) > 0.6,
                ),
            },
            "diagnostic_medians": {
                "average_log_probability_material": statistics.median(
                    row["average_log_probability"]
                    for row in selected
                    if row["semantic_label"] == "MATERIAL_ERROR"
                ),
                "average_log_probability_non_material": statistics.median(
                    row["average_log_probability"]
                    for row in selected
                    if row["semantic_label"] != "MATERIAL_ERROR"
                ),
                "maximum_no_speech_probability_material": statistics.median(
                    row["maximum_no_speech_probability"]
                    for row in selected
                    if row["semantic_label"] == "MATERIAL_ERROR"
                ),
                "maximum_no_speech_probability_non_material": statistics.median(
                    row["maximum_no_speech_probability"]
                    for row in selected
                    if row["semantic_label"] != "MATERIAL_ERROR"
                ),
            },
        }

    comparison = {row["record_id"]: row for row in phase12b["comparison"]["clips"]}
    disagreement = {}
    for label, expected in (("meaningful", True), ("not_meaningful", False)):
        clip_ids = {
            clip_id
            for clip_id, row in comparison.items()
            if row["meaningful_disagreement"] is expected
        }
        review_rows = [row for row in rows if row["clip_id"] in clip_ids]
        by_clip = {
            clip_id: any(
                row["semantic_label"] == "MATERIAL_ERROR"
                for row in review_rows
                if row["clip_id"] == clip_id
            )
            for clip_id in clip_ids
        }
        disagreement[label] = {
            "clips": len(clip_ids),
            "review_rows": len(review_rows),
            "material_error_review_rate": (
                sum(row["semantic_label"] == "MATERIAL_ERROR" for row in review_rows)
                / len(review_rows)
            ),
            "clips_with_any_material_error": sum(by_clip.values()),
            "clip_any_material_error_rate": sum(by_clip.values()) / len(by_clip),
        }

    performance = {row["model_id"]: row for row in phase12b["model_summaries"]}
    return {
        "schema_version": "1.0",
        "generated_at": datetime.now(UTC).isoformat(),
        "selection_hash": evaluation["selection_hash"],
        "integrity": {name: file_sha256(path) for name, path in paths.items()},
        "normalization": evaluation["scoring"],
        "reviews": {
            "total": len(rows),
            "overall": semantic_summary(rows),
            "models": by_model,
        },
        "disagreement": {
            "threshold": "Phase 12B token disagreement > 0.25",
            **disagreement,
        },
        "short_clip_rows": [row for row in rows if row["short_clip"]],
        "unusable_rows": [row for row in rows if row["usability"] == "UNUSABLE"],
        "phase12b_performance": performance,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--evaluation", type=Path, default=Path("docs/phase12c-radio-evaluation.json")
    )
    parser.add_argument("--phase12b", type=Path, default=Path("docs/phase12b-radio-asr.json"))
    parser.add_argument("--output", type=Path, default=Path("docs/phase12c-radio-summary.json"))
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    evaluation = json.loads(args.evaluation.read_text(encoding="utf-8"))
    phase12b = json.loads(args.phase12b.read_text(encoding="utf-8"))
    paths = {
        "manifest_sha256": Path("docs/phase12c-radio-annotation-manifest.json"),
        "phase12b_sha256": args.phase12b,
        "human_references_sha256": Path("docs/phase12c-radio-human-references.json"),
        "semantic_reviews_sha256": Path("docs/phase12c-radio-semantic-review.json"),
    }
    summary = build_summary(evaluation, phase12b, paths)
    atomic_write(args.output, summary)
    print(f"Phase 12C summary written to {args.output}")


if __name__ == "__main__":
    main()
