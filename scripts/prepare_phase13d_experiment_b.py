"""Freeze the Phase 13D-C Experiment B retry population before provider calls."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

from f1_pitwall.knowledge.agentrouter import AGENTROUTER_MODEL
from f1_pitwall.knowledge.generation import SYSTEM_PROMPT
from f1_pitwall.knowledge.reliability import PHASE13D_BENCHMARK_SHA256, sha256

ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs"
EXPERIMENT_A_RAW = DOCS / "phase13d-experiment-a-raw.jsonl"
EXPERIMENT_A_OUTPUTS = DOCS / "phase13d-experiment-a-outputs.json"
EXPERIMENT_A_EVALUATION = DOCS / "phase13d-experiment-a-evaluation-corrected.json"
EXPERIMENT_A_INPUTS = DOCS / "phase13d-experiment-a-inputs.json"
EXPERIMENT_A_REVIEW = DOCS / "phase13d-experiment-a-human-review-results.json"
BENCHMARK = DOCS / "phase13d-answer-benchmark.json"
PHASE13C_BENCHMARK = DOCS / "phase13c-answer-benchmark.json"
PROTOCOL = DOCS / "phase13d-experiment-b-protocol.md"
MANIFEST = DOCS / "phase13d-experiment-b-retry-manifest.json"
FREEZE = DOCS / "phase13d-experiment-b-freeze.json"
RUNNER = ROOT / "scripts" / "run_phase13d_experiment_b.py"

SOURCE_HASHES = {
    "experiment_a_raw_sha256": "75a66fba6105de5c3999fda2aa0dcc8647ab956d54a22c8966727f564bcf4ade",
    "experiment_a_outputs_sha256": (
        "55a1d41c4ea6edfe5875274be66a283a11b0e9e9ebd8a2efc5ff6f6a36c6de28"
    ),
    "experiment_a_evaluation_sha256": (
        "139a4cd5223ee897912f14269e2841696a3f4c9595f08799db65eb7be69ddb81"
    ),
    "experiment_a_inputs_sha256": (
        "c965a986e9161a08170ac3441cda18d6d112810711f25edbc973abf78caa12d5"
    ),
    "experiment_a_review_sha256": (
        "a9a11226a1f3bc528bb9eee4df17b5e3a0d2578cd14cb04d9b90cc0fccb82826"
    ),
    "benchmark_sha256": PHASE13D_BENCHMARK_SHA256,
    "phase13c_benchmark_sha256": "969ff9d522a4f047493c1c1fc2ba9eaeddeb80f10988bd271f87078c7112809e",
}
ELIGIBLE = {"TRUNCATED_RESPONSE", "SCHEMA_FAILURE", "TIMEOUT"}
EXPECTED_COUNTS = Counter({"TRUNCATED_RESPONSE": 22, "SCHEMA_FAILURE": 4, "TIMEOUT": 4})


def write_json_once(path: Path, value: object) -> None:
    if path.exists():
        raise FileExistsError(f"refusing to replace frozen Experiment B artifact: {path}")
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False), encoding="utf-8")


def source_checks() -> dict[str, str]:
    paths = {
        "experiment_a_raw_sha256": EXPERIMENT_A_RAW,
        "experiment_a_outputs_sha256": EXPERIMENT_A_OUTPUTS,
        "experiment_a_evaluation_sha256": EXPERIMENT_A_EVALUATION,
        "experiment_a_inputs_sha256": EXPERIMENT_A_INPUTS,
        "experiment_a_review_sha256": EXPERIMENT_A_REVIEW,
        "benchmark_sha256": BENCHMARK,
        "phase13c_benchmark_sha256": PHASE13C_BENCHMARK,
    }
    actual = {key: sha256(path) for key, path in paths.items()}
    if actual != SOURCE_HASHES:
        raise ValueError(f"frozen source hash mismatch: {actual}")
    return actual


def eligible_rows() -> list[dict]:
    raw = [json.loads(line) for line in EXPERIMENT_A_RAW.read_text("utf-8").splitlines()]
    treatment = [row for row in raw if row["arm"] == "TREATMENT_640"]
    if len(treatment) != 100 or any(row["retry_count"] != 0 for row in treatment):
        raise ValueError("Experiment A treatment arm is not 100 frozen zero-retry rows")
    rows = [row for row in treatment if row["classification"] in ELIGIBLE]
    counts = Counter(row["classification"] for row in rows)
    if len(rows) != 30 or counts != EXPECTED_COUNTS:
        raise ValueError(f"unexpected retry population: rows={len(rows)}, counts={dict(counts)}")
    if any(row["classification"] == "SUCCESS" for row in rows):
        raise ValueError("successful Experiment A answer entered retry population")
    return sorted(rows, key=lambda row: row["question_id"])


def build_manifest() -> dict:
    hashes = source_checks()
    inputs = {
        row["question_id"]: row
        for row in json.loads(EXPERIMENT_A_INPUTS.read_text(encoding="utf-8"))
    }
    rows = eligible_rows()
    schedule = []
    for sequence, row in enumerate(rows, 1):
        frozen_input = inputs[row["question_id"]]
        if row["bundle_sha256"] != frozen_input["bundle_sha256"]:
            raise ValueError(f"bundle mismatch for {row['question_id']}")
        if row["messages_sha256"] != frozen_input["messages_sha256"]:
            raise ValueError(f"message mismatch for {row['question_id']}")
        schedule.append(
            {
                "retry_sequence": sequence,
                "question_id": row["question_id"],
                "route_type": row["route_type"],
                "experiment_a_result_id": f"TREATMENT_640:{row['question_id']}",
                "experiment_a_call_order": row["call_order"],
                "original_failure_category": row["classification"],
                "original_stop_reason": row["stop_reason"],
                "bundle_sha256": row["bundle_sha256"],
                "messages_sha256": row["messages_sha256"],
            }
        )
    base_commit = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, check=True, capture_output=True, text=True
    ).stdout.strip()
    return {
        "schema_version": "phase13d-experiment-b-retry-manifest-v1",
        "phase": "13D-C_EXPERIMENT_B",
        "status": "FROZEN_NOT_RUN",
        "frozen_at": datetime.now(UTC).isoformat(),
        "base_commit": base_commit,
        "hypothesis": (
            "One identical retry after an explicitly detected 640-token mechanical failure will "
            "materially reduce final unrecovered failures without introducing misleading or "
            "ungrounded recovered answers."
        ),
        "design": (
            "policy recovery over original Experiment A first attempts; not a fresh "
            "100-question prospective run"
        ),
        "source_hashes": hashes,
        "protocol_sha256": sha256(PROTOCOL),
        "runner_sha256": sha256(RUNNER),
        "eligibility": {
            "allowed_original_statuses": sorted(ELIGIBLE),
            "eligible_rows": 30,
            "category_counts": dict(sorted(EXPECTED_COUNTS.items())),
            "original_successes_retained": 70,
            "successful_answers_retried": 0,
            "maximum_new_provider_calls": 30,
            "maximum_attempts_per_eligible_question": 1,
        },
        "configuration": {
            "provider": "AgentRouter",
            "model_id": AGENTROUTER_MODEL,
            "protocol": "Anthropic-compatible Messages via official Anthropic SDK",
            "base_url": "https://agentrouter.org",
            "system_prompt_sha256": hashlib.sha256(SYSTEM_PROMPT.encode()).hexdigest(),
            "max_output_tokens": 640,
            "temperature": "SDK default (not overridden)",
            "output_contract": (
                "forced non-executing grounded_answer tool using GeneratedAnswer schema"
            ),
            "sdk_retries": 0,
            "transport_retries": 0,
            "schema_retries": 0,
            "content_retries": 0,
            "third_attempts": 0,
            "retrieval_changed": False,
            "prompts_changed": False,
            "evidence_changed": False,
        },
        "retry_schedule": schedule,
        "human_review": {
            "population": "all successfully parsed retry answers",
            "sampling": "none",
            "status": "PENDING_GENUINE_HUMAN_REVIEW",
        },
        "known_diagnostic": {
            "question_id": "q13d_8fd5589debc7a772c1ed",
            "issue": "expected FIA porpoising safeguards are absent from supplied evidence",
            "frozen_artifacts_modified": False,
        },
        "production_decision": "NO_GO_UNCHANGED",
    }


def validate_manifest(manifest: dict) -> None:
    if len(manifest["retry_schedule"]) != 30:
        raise ValueError("Experiment B requires exactly 30 retry rows")
    if [row["retry_sequence"] for row in manifest["retry_schedule"]] != list(range(1, 31)):
        raise ValueError("retry ordering is not exact")
    if len({row["question_id"] for row in manifest["retry_schedule"]}) != 30:
        raise ValueError("duplicate retry question")
    if (
        Counter(row["original_failure_category"] for row in manifest["retry_schedule"])
        != EXPECTED_COUNTS
    ):
        raise ValueError("retry category counts changed")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()
    manifest = build_manifest()
    validate_manifest(manifest)
    if not args.write:
        print(json.dumps({"validated": True, "eligibility": manifest["eligibility"]}, indent=2))
        return
    write_json_once(MANIFEST, manifest)
    freeze = {
        "schema_version": "phase13d-experiment-b-freeze-v1",
        "frozen_at": manifest["frozen_at"],
        "manifest_sha256": sha256(MANIFEST),
        "protocol_sha256": manifest["protocol_sha256"],
        "runner_sha256": manifest["runner_sha256"],
        "provider_requests_observed_before_freeze": 0,
    }
    write_json_once(FREEZE, freeze)
    print(
        json.dumps(
            {"manifest": str(MANIFEST), "freeze": freeze, "eligibility": manifest["eligibility"]},
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
