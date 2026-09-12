"""Write an explicit correction to the Phase 13D-A derived guardrail projection."""

from __future__ import annotations

import json
import statistics
from collections import Counter
from pathlib import Path

from f1_pitwall.knowledge.reliability import load_benchmark, sha256

ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs"
SOURCE_PATH = DOCS / "phase13d-experiment-a-evaluation.json"
OUTPUT_PATH = DOCS / "phase13d-experiment-a-evaluation-corrected.json"
RAW_PATH = DOCS / "phase13d-experiment-a-raw.jsonl"
OUTPUTS_PATH = DOCS / "phase13d-experiment-a-outputs.json"


def main() -> None:
    if OUTPUT_PATH.exists():
        raise FileExistsError(f"refusing to replace corrected evaluation: {OUTPUT_PATH}")
    evaluation = json.loads(SOURCE_PATH.read_text(encoding="utf-8"))
    questions = load_benchmark(DOCS / "phase13d-answer-benchmark.json")
    outputs = json.loads(OUTPUTS_PATH.read_text(encoding="utf-8"))["arms"]
    raw = [json.loads(line) for line in RAW_PATH.read_text(encoding="utf-8").splitlines()]
    structured_ids = {
        question.question_id
        for question in questions
        if question.route_type.value == "STRUCTURED_ONLY"
    }
    for arm, rows in outputs.items():
        structured = [row for row in rows if row["question_id"] in structured_ids]
        correct = sum(
            all(
                fact.value.casefold() in row["answer"]["answer"].casefold()
                for fact in next(
                    question for question in questions if question.question_id == row["question_id"]
                ).expected_facts
            )
            for row in structured
        )
        arm_raw = [row for row in raw if row["arm"] == arm]
        latencies = sorted(row["latency_ms"] for row in arm_raw)
        evaluation["arm_metrics"][arm].update(
            {
                "structured_only_correct": correct,
                "structured_only_total": len(structured),
                "structured_only_regression_accuracy": correct / len(structured),
                "stop_reason_counts": dict(
                    sorted(Counter(row["stop_reason"] or "NONE" for row in arm_raw).items())
                ),
                "latency_median_ms": statistics.median(latencies),
                "latency_p90_ms": latencies[int(0.9 * (len(latencies) - 1))],
            }
        )
    evaluation["treatment_guardrails"]["structured_facts_30_of_30"] = (
        evaluation["arm_metrics"]["TREATMENT_640"]["structured_only_correct"] == 30
    )
    evaluation.update(
        {
            "schema_version": "phase13d-experiment-a-evaluation-v1-corrected",
            "correction": {
                "supersedes_sha256": sha256(SOURCE_PATH),
                "reason": (
                    "The first derived projection compared the combined 55 STRUCTURED_ONLY+MIXED "
                    "fact numerator to 30. This correction reports the unchanged deterministic "
                    "30-question regression separately; raw and normalized outputs are unchanged."
                ),
                "raw_sha256": sha256(RAW_PATH),
                "outputs_sha256": sha256(OUTPUTS_PATH),
            },
        }
    )
    OUTPUT_PATH.write_text(json.dumps(evaluation, indent=2), encoding="utf-8")
    print(sha256(OUTPUT_PATH))


if __name__ == "__main__":
    main()
