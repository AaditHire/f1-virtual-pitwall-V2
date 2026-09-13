"""Freeze the Phase 13D-D Experiment C truncation population before provider calls."""

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
BENCHMARK = DOCS / "phase13d-answer-benchmark.json"
PHASE13C_BENCHMARK = DOCS / "phase13c-answer-benchmark.json"
EXPERIMENT_A_RAW = DOCS / "phase13d-experiment-a-raw.jsonl"
EXPERIMENT_A_OUTPUTS = DOCS / "phase13d-experiment-a-outputs.json"
EXPERIMENT_A_INPUTS = DOCS / "phase13d-experiment-a-inputs.json"
EXPERIMENT_A_EVALUATION = DOCS / "phase13d-experiment-a-evaluation-corrected.json"
EXPERIMENT_A_REVIEW = DOCS / "phase13d-experiment-a-human-review-results.json"
EXPERIMENT_B_RAW = DOCS / "phase13d-experiment-b-raw.jsonl"
EXPERIMENT_B_OUTPUTS = DOCS / "phase13d-experiment-b-policy-outputs.json"
EXPERIMENT_B_EVALUATION = DOCS / "phase13d-experiment-b-evaluation.json"
EXPERIMENT_B_REVIEW = DOCS / "phase13d-experiment-b-human-review-results.json"
EXPERIMENT_B_MANIFEST = DOCS / "phase13d-experiment-b-retry-manifest.json"
EXPERIMENT_B_FREEZE = DOCS / "phase13d-experiment-b-freeze.json"
PROTOCOL = DOCS / "phase13d-experiment-c-protocol.md"
MANIFEST = DOCS / "phase13d-experiment-c-fallback-manifest.json"
FREEZE = DOCS / "phase13d-experiment-c-freeze.json"
RUNNER = ROOT / "scripts" / "run_phase13d_experiment_c.py"

SOURCE_HASHES = {
    "benchmark_sha256": PHASE13D_BENCHMARK_SHA256,
    "phase13c_benchmark_sha256": "969ff9d522a4f047493c1c1fc2ba9eaeddeb80f10988bd271f87078c7112809e",
    "experiment_a_raw_sha256": "75a66fba6105de5c3999fda2aa0dcc8647ab956d54a22c8966727f564bcf4ade",
    "experiment_a_outputs_sha256": (
        "55a1d41c4ea6edfe5875274be66a283a11b0e9e9ebd8a2efc5ff6f6a36c6de28"
    ),
    "experiment_a_inputs_sha256": (
        "c965a986e9161a08170ac3441cda18d6d112810711f25edbc973abf78caa12d5"
    ),
    "experiment_a_evaluation_sha256": (
        "139a4cd5223ee897912f14269e2841696a3f4c9595f08799db65eb7be69ddb81"
    ),
    "experiment_a_review_sha256": (
        "a9a11226a1f3bc528bb9eee4df17b5e3a0d2578cd14cb04d9b90cc0fccb82826"
    ),
    "experiment_b_raw_sha256": "2c729c15f406030848f8475a7dbf149ce3b49bf4cfd770f126c506897c77217f",
    "experiment_b_outputs_sha256": (
        "eaa43f15ba29bc34d0730ba6b247928a7db2fa78cedfc89992b7cf913f12ea2b"
    ),
    "experiment_b_evaluation_sha256": (
        "aefbee343cd9dc4ef480047161dbfb67e86dd3d7f7ac8335f2416c813ef34288"
    ),
    "experiment_b_review_sha256": (
        "61f75563b2fc18398352aec7eb40fa0916dc3915ce89e4bdcb854c48b872c006"
    ),
    "experiment_b_manifest_sha256": (
        "99e7473f9e9103aa86d29283ce2210da00b876de11996c484c3692585b4669c7"
    ),
    "experiment_b_freeze_sha256": (
        "77ae04bdc834a34fe85dcc19c44f2d80616b6e4385e012da508f9b734190caa8"
    ),
}


def write_json_once(path: Path, value: object) -> None:
    if path.exists():
        raise FileExistsError(f"refusing to replace frozen Experiment C artifact: {path}")
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False), encoding="utf-8")


def source_checks() -> dict[str, str]:
    paths = {
        "benchmark_sha256": BENCHMARK,
        "phase13c_benchmark_sha256": PHASE13C_BENCHMARK,
        "experiment_a_raw_sha256": EXPERIMENT_A_RAW,
        "experiment_a_outputs_sha256": EXPERIMENT_A_OUTPUTS,
        "experiment_a_inputs_sha256": EXPERIMENT_A_INPUTS,
        "experiment_a_evaluation_sha256": EXPERIMENT_A_EVALUATION,
        "experiment_a_review_sha256": EXPERIMENT_A_REVIEW,
        "experiment_b_raw_sha256": EXPERIMENT_B_RAW,
        "experiment_b_outputs_sha256": EXPERIMENT_B_OUTPUTS,
        "experiment_b_evaluation_sha256": EXPERIMENT_B_EVALUATION,
        "experiment_b_review_sha256": EXPERIMENT_B_REVIEW,
        "experiment_b_manifest_sha256": EXPERIMENT_B_MANIFEST,
        "experiment_b_freeze_sha256": EXPERIMENT_B_FREEZE,
    }
    actual = {key: sha256(path) for key, path in paths.items()}
    if actual != SOURCE_HASHES:
        raise ValueError(f"frozen source hash mismatch: {actual}")
    return actual


def eligible_rows() -> list[dict]:
    rows = [json.loads(line) for line in EXPERIMENT_A_RAW.read_text("utf-8").splitlines()]
    treatment = [row for row in rows if row["arm"] == "TREATMENT_640"]
    eligible = [row for row in treatment if row["classification"] == "TRUNCATED_RESPONSE"]
    if len(treatment) != 100 or len(eligible) != 22:
        raise ValueError(
            f"expected 100 treatment rows and 22 truncations, got {len(treatment)}/{len(eligible)}"
        )
    if Counter(row["classification"] for row in eligible) != {"TRUNCATED_RESPONSE": 22}:
        raise ValueError("Experiment C population contains a non-truncation")
    return sorted(eligible, key=lambda row: row["question_id"])


def build_manifest() -> dict:
    hashes = source_checks()
    inputs = {row["question_id"]: row for row in json.loads(EXPERIMENT_A_INPUTS.read_text("utf-8"))}
    schedule = []
    for sequence, row in enumerate(eligible_rows(), 1):
        frozen_input = inputs[row["question_id"]]
        if (
            row["bundle_sha256"] != frozen_input["bundle_sha256"]
            or row["messages_sha256"] != frozen_input["messages_sha256"]
        ):
            raise ValueError(f"frozen input mismatch for {row['question_id']}")
        schedule.append(
            {
                "fallback_sequence": sequence,
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
        "schema_version": "phase13d-experiment-c-fallback-manifest-v1",
        "phase": "13D-D_EXPERIMENT_C",
        "status": "FROZEN_NOT_RUN",
        "frozen_at": datetime.now(UTC).isoformat(),
        "base_commit": base_commit,
        "methodological_status": "DEVELOPMENT_DIAGNOSTIC_NOT_UNTOUCHED_HOLDOUT",
        "hypothesis": (
            "One otherwise-identical 1280-token fallback will recover a large majority of all "
            "22 original 640-token truncations while preserving frozen guardrails."
        ),
        "source_hashes": hashes,
        "protocol_sha256": sha256(PROTOCOL),
        "runner_sha256": sha256(RUNNER),
        "eligibility": {
            "required_original_status": "TRUNCATED_RESPONSE",
            "eligible_rows": 22,
            "maximum_new_provider_calls": 22,
            "maximum_attempts_per_question": 1,
            "non_truncation_calls": 0,
            "second_fallbacks": 0,
        },
        "configuration": {
            "provider": "AgentRouter",
            "model_id": AGENTROUTER_MODEL,
            "protocol": "Anthropic-compatible Messages via official Anthropic SDK",
            "base_url": "https://agentrouter.org",
            "system_prompt_sha256": hashlib.sha256(SYSTEM_PROMPT.encode()).hexdigest(),
            "max_output_tokens": 1280,
            "temperature": "SDK default (not overridden)",
            "output_contract": (
                "forced non-executing grounded_answer tool using GeneratedAnswer schema"
            ),
            "sdk_retries": 0,
            "transport_retries": 0,
            "schema_retries": 0,
            "content_retries": 0,
            "second_fallbacks": 0,
            "retrieval_changed": False,
            "prompts_changed": False,
            "evidence_changed": False,
            "model_changed": False,
        },
        "fallback_schedule": schedule,
        "candidate_policy": (
            "retain original 640 successes; use Experiment B 640 retry only for original "
            "TIMEOUT/SCHEMA_FAILURE; use Experiment C 1280 fallback for every original "
            "TRUNCATED_RESPONSE"
        ),
        "human_review": {
            "population": "all successfully parsed 1280 fallbacks",
            "sampling": "none",
            "status": "PENDING_GENUINE_HUMAN_REVIEW",
        },
        "known_diagnostic": {
            "question_id": "q13d_8fd5589debc7a772c1ed",
            "missing_expected_facts": 2,
            "frozen_artifacts_modified": False,
        },
        "production_decision": "NO_GO_UNCHANGED",
    }


def validate_manifest(manifest: dict) -> None:
    schedule = manifest["fallback_schedule"]
    if len(schedule) != 22 or [row["fallback_sequence"] for row in schedule] != list(range(1, 23)):
        raise ValueError("Experiment C requires exactly 22 ordered fallback rows")
    if len({row["question_id"] for row in schedule}) != 22:
        raise ValueError("duplicate Experiment C question")
    if any(row["original_failure_category"] != "TRUNCATED_RESPONSE" for row in schedule):
        raise ValueError("non-truncation entered Experiment C")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()
    manifest = build_manifest()
    validate_manifest(manifest)
    if not args.write:
        print(
            json.dumps(
                {
                    "validated": True,
                    "eligibility": manifest["eligibility"],
                    "question_ids": [row["question_id"] for row in manifest["fallback_schedule"]],
                },
                indent=2,
            )
        )
        return
    write_json_once(MANIFEST, manifest)
    freeze = {
        "schema_version": "phase13d-experiment-c-freeze-v1",
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
