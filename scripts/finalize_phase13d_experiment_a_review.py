"""Finalize blank Phase 13D review artifacts after the frozen sampler shortfall."""

from __future__ import annotations

import json
import random
from pathlib import Path

from f1_pitwall.knowledge.reliability import PHASE13D_BENCHMARK_SHA256, load_benchmark, sha256

ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs"
RAW_PATH = DOCS / "phase13d-experiment-a-raw.jsonl"
OUTPUTS_PATH = DOCS / "phase13d-experiment-a-outputs.json"
MANIFEST_PATH = DOCS / "phase13d-experiment-a-run-manifest.json"
TEMPLATE_PATH = DOCS / "phase13d-experiment-a-human-review-template.json"
MAP_PATH = DOCS / "phase13d-experiment-a-human-review-map.json"


def write_json_once(path: Path, value: object) -> None:
    if path.exists():
        raise FileExistsError(f"refusing to replace review artifact: {path}")
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False), encoding="utf-8")


def main() -> None:
    questions = load_benchmark(DOCS / "phase13d-answer-benchmark.json")
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    raw = [json.loads(line) for line in RAW_PATH.read_text(encoding="utf-8").splitlines()]
    outputs = json.loads(OUTPUTS_PATH.read_text(encoding="utf-8"))["arms"]
    if len(raw) != 200 or sum(row["retry_count"] for row in raw) != 0:
        raise ValueError("expected exactly 200 frozen zero-retry raw rows")
    if manifest["benchmark"]["sha256"] != PHASE13D_BENCHMARK_SHA256:
        raise ValueError("manifest benchmark hash changed")
    raw_by_key = {(row["question_id"], row["arm"]): row for row in raw}
    output_by_key = {
        (row["question_id"], arm): row for arm, rows in outputs.items() for row in rows
    }
    mandatory_ids = {
        row.question_id
        for row in questions
        if row.route_type.value == "MIXED" or row.expected_refusal or row.adversarial_kind != "NONE"
    }
    valid_candidate_ids = []
    for question_id in manifest["human_review"]["additional_review_candidate_order"]:
        if all(
            raw_by_key[(question_id, arm)]["classification"] == "SUCCESS"
            for arm in ("CONTROL_320", "TREATMENT_640")
        ):
            valid_candidate_ids.append(question_id)
    selected = {
        (question_id, arm)
        for question_id in mandatory_ids | set(valid_candidate_ids)
        for arm in ("CONTROL_320", "TREATMENT_640")
    }
    selected |= {
        (row["question_id"], row["arm"]) for row in raw if row["classification"] != "SUCCESS"
    }
    ordered = sorted(selected)
    random.Random(manifest["human_review"]["sampling_seed"] + 1).shuffle(ordered)
    by_question = {row.question_id: row for row in questions}
    template_rows = []
    map_rows = []
    for index, (question_id, arm) in enumerate(ordered, 1):
        review_id = f"R{index:03d}"
        question = by_question[question_id]
        output = output_by_key[(question_id, arm)]
        template_rows.append(
            {
                "review_id": review_id,
                "question": question.question,
                "controlled_evidence": output["bundle"],
                "answer": output["answer"],
                "grounding": "",
                "usefulness": "",
                "misleading": "",
                "notes": "",
            }
        )
        raw_row = raw_by_key[(question_id, arm)]
        map_rows.append(
            {
                "review_id": review_id,
                "question_id": question_id,
                "arm": arm,
                "max_output_tokens": raw_row["max_output_tokens"],
                "call_order": raw_row["call_order"],
                "classification": raw_row["classification"],
            }
        )
    metadata = {
        "status": "PENDING_GENUINE_HUMAN_REVIEW",
        "selection_frozen": True,
        "labels_fabricated": False,
        "requested_additional_valid_pairs": 10,
        "available_additional_valid_pairs": len(valid_candidate_ids),
        "sampler_shortfall": len(valid_candidate_ids) < 10,
        "disposition": (
            "Included every available valid non-mandatory paired RAG candidate, every "
            "mandatory arm row, and every terminal failure. No provider call or output changed."
        ),
        "raw_sha256": sha256(RAW_PATH),
        "outputs_sha256": sha256(OUTPUTS_PATH),
    }
    write_json_once(TEMPLATE_PATH, {"metadata": metadata, "rows": template_rows})
    write_json_once(MAP_PATH, {"metadata": metadata, "rows": map_rows})
    print(json.dumps({**metadata, "review_rows": len(template_rows)}, indent=2))


if __name__ == "__main__":
    main()
