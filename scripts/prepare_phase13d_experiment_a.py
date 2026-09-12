"""Freeze paired Phase 13D Experiment A inputs without making provider calls."""

from __future__ import annotations

import argparse
import hashlib
import json
import random
from collections import Counter
from pathlib import Path

from f1_pitwall.knowledge.chunking import chunk_documents
from f1_pitwall.knowledge.generation import build_generation_messages
from f1_pitwall.knowledge.models import KnowledgeDocument
from f1_pitwall.knowledge.reliability import (
    PHASE13D_BENCHMARK_SHA256,
    Phase13DQuestion,
    assemble_bundle,
    load_benchmark,
    sha256,
)
from f1_pitwall.knowledge.retrieval import BM25Retriever

ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs"
BENCHMARK_PATH = DOCS / "phase13d-answer-benchmark.json"
PROTOCOL_PATH = DOCS / "phase13d-grounded-answer-reliability-protocol.md"
ORIGINAL_MANIFEST_PATH = DOCS / "phase13d-prospective-run-manifest.json"
SCHEDULE_PATH = DOCS / "phase13d-experiment-a-schedule.json"
INPUTS_PATH = DOCS / "phase13d-experiment-a-inputs.json"
RUN_MANIFEST_PATH = DOCS / "phase13d-experiment-a-run-manifest.json"
FREEZE_PATH = DOCS / "phase13d-experiment-a-freeze.json"
CORPUS_PATH = DOCS / "phase13b-knowledge-corpus.json"
RUNNER_PATH = ROOT / "scripts" / "run_phase13d_experiment_a.py"
PAIRED_HELPERS_PATH = ROOT / "src" / "f1_pitwall" / "knowledge" / "paired_reliability.py"

ORIGINAL_PROTOCOL_SHA256 = "d6aefdfa930733e9ba1cac5cf5a948ecd6d3b8f1d61db2149e58a0e360e5c8be"
ORIGINAL_MANIFEST_SHA256 = "54331623b9f45ed67bd901263cca191ed960dbf4f8b4de3eefeb7e273ad62eef"
SCHEDULE_SEED = 130320640
BOOTSTRAP_SEED = 130640320
HUMAN_SAMPLE_SEED = 131313
FROZEN_AT = "2026-09-12T22:39:00+05:30"
ARMS = {
    "CONTROL_320": 320,
    "TREATMENT_640": 640,
}


def canonical_hash(value: object) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    return hashlib.sha256(encoded).hexdigest()


def prepare_retriever() -> BM25Retriever:
    documents = [
        KnowledgeDocument.model_validate(row)
        for row in json.loads(CORPUS_PATH.read_text(encoding="utf-8"))
    ]
    return BM25Retriever(chunk_documents(documents, "section"))


def build_inputs(questions: list[Phase13DQuestion]) -> list[dict]:
    retriever = prepare_retriever()
    rows = []
    for question in questions:
        if not question.generation_required:
            continue
        bundle = assemble_bundle(question, retriever)
        if question.evidence_available:
            required = set(question.required_source_keys)
            found = {source.source_key for source in bundle.sources}
            if not required <= found:
                raise ValueError(f"incomplete frozen evidence for {question.question_id}")
        payload = bundle.prompt_payload()
        messages = build_generation_messages(bundle)
        rows.append(
            {
                "question_id": question.question_id,
                "route_type": question.route_type.value,
                "bundle": payload,
                "bundle_sha256": canonical_hash(payload),
                "messages": messages,
                "messages_sha256": canonical_hash(messages),
            }
        )
    if len(rows) != 100:
        raise ValueError(f"expected 100 generation inputs, got {len(rows)}")
    return rows


def build_schedule(questions: list[Phase13DQuestion]) -> dict:
    ids = [row.question_id for row in questions if row.generation_required]
    rng = random.Random(SCHEDULE_SEED)
    rng.shuffle(ids)
    first_arms = ["CONTROL_320"] * 50 + ["TREATMENT_640"] * 50
    rng.shuffle(first_arms)
    calls = []
    call_order = 0
    for pair_index, (question_id, first_arm) in enumerate(zip(ids, first_arms, strict=True), 1):
        second_arm = "TREATMENT_640" if first_arm == "CONTROL_320" else "CONTROL_320"
        for within_pair, arm in enumerate((first_arm, second_arm), 1):
            call_order += 1
            calls.append(
                {
                    "call_order": call_order,
                    "pair_index": pair_index,
                    "within_pair": within_pair,
                    "question_id": question_id,
                    "arm": arm,
                    "max_output_tokens": ARMS[arm],
                }
            )
    return {
        "schema_version": "phase13d-paired-schedule-v1",
        "seed": SCHEDULE_SEED,
        "algorithm": (
            "Python random.Random; shuffle question IDs; shuffle 50/50 first-arm "
            "labels; consecutive pairs"
        ),
        "calls": calls,
    }


def validate_schedule(schedule: dict, questions: list[Phase13DQuestion]) -> None:
    calls = schedule["calls"]
    expected_ids = {row.question_id for row in questions if row.generation_required}
    if len(calls) != 200 or [row["call_order"] for row in calls] != list(range(1, 201)):
        raise ValueError("paired schedule must contain ordered calls 1..200")
    by_question: dict[str, list[dict]] = {}
    for row in calls:
        by_question.setdefault(row["question_id"], []).append(row)
    if set(by_question) != expected_ids:
        raise ValueError("paired schedule question set differs from frozen benchmark")
    for question_id, pair in by_question.items():
        if len(pair) != 2 or {row["arm"] for row in pair} != set(ARMS):
            raise ValueError(f"invalid arm pair for {question_id}")
        if pair[1]["call_order"] != pair[0]["call_order"] + 1:
            raise ValueError(f"non-consecutive pair for {question_id}")
    first_arm_counts = Counter(pair[0]["arm"] for pair in by_question.values())
    if first_arm_counts != {"CONTROL_320": 50, "TREATMENT_640": 50}:
        raise ValueError(f"unbalanced first-arm order: {dict(first_arm_counts)}")


def build_additional_review_candidate_order(
    questions: list[Phase13DQuestion],
) -> list[str]:
    """Freeze a category-stratified candidate order before answers exist."""
    candidates = [
        row
        for row in questions
        if row.route_type.value == "RAG_ONLY"
        and not row.expected_refusal
        and row.adversarial_kind == "NONE"
    ]
    by_category: dict[str, list[str]] = {}
    for row in candidates:
        by_category.setdefault(row.category, []).append(row.question_id)
    rng = random.Random(HUMAN_SAMPLE_SEED)
    for ids in by_category.values():
        ids.sort()
        rng.shuffle(ids)
    categories = sorted(by_category)
    selected: list[str] = []
    candidate_count = len(candidates)
    while len(selected) < candidate_count:
        progressed = False
        for category in categories:
            if by_category[category]:
                selected.append(by_category[category].pop())
                progressed = True
        if not progressed:
            raise ValueError("human-review candidate ordering stalled")
    return selected


def build_manifest(
    protocol_hash: str,
    schedule_hash: str,
    inputs_hash: str,
    additional_review_candidate_order: list[str],
) -> dict:
    return {
        "schema_version": "phase13d-experiment-a-paired-manifest-v1",
        "phase": "13D-B_EXPERIMENT_A",
        "status": "FROZEN_NOT_RUN",
        "frozen_at": FROZEN_AT,
        "base_commit": "6624d3a40a254581f7a45fbc83293322293bc196",
        "pre_run_amendment": {
            "reason": (
                "A 640-only run on a new benchmark confounds token allowance with "
                "benchmark and time/provider effects."
            ),
            "phase13d_outputs_existed": False,
            "original_protocol_sha256": ORIGINAL_PROTOCOL_SHA256,
            "original_manifest_sha256": ORIGINAL_MANIFEST_SHA256,
            "amended_protocol_sha256": protocol_hash,
        },
        "benchmark": {
            "path": str(BENCHMARK_PATH.relative_to(ROOT)).replace("\\", "/"),
            "sha256": PHASE13D_BENCHMARK_SHA256,
            "unchanged": True,
            "generation_questions": 100,
            "structured_only_questions": 30,
        },
        "schedule": {
            "path": str(SCHEDULE_PATH.relative_to(ROOT)).replace("\\", "/"),
            "sha256": schedule_hash,
            "seed": SCHEDULE_SEED,
            "expected_calls": 200,
            "control_first_pairs": 50,
            "treatment_first_pairs": 50,
        },
        "inputs": {
            "path": str(INPUTS_PATH.relative_to(ROOT)).replace("\\", "/"),
            "sha256": inputs_hash,
            "rows": 100,
            "identical_between_arms": True,
        },
        "configuration": {
            "provider": "AgentRouter",
            "model_id": "claude-opus-4-8",
            "protocol": "Anthropic-compatible Messages via official Anthropic SDK",
            "base_url": "https://agentrouter.org",
            "authentication": "auth_token / Bearer via AGENTROUTER_API_KEY",
            "system_prompt_sha256": (
                "d272ed62b715c9b49eb2769cae0366542f5642f8f47b54d6ffc8ae652d70c7d9"
            ),
            "temperature": "SDK default (not overridden)",
            "output_contract": (
                "forced non-executing grounded_answer tool using GeneratedAnswer schema"
            ),
            "control_max_tokens": 320,
            "treatment_max_tokens": 640,
            "sdk_retries": 0,
            "transport_retries": 0,
            "schema_retries": 0,
            "content_retries": 0,
            "web_search": False,
            "model_side_retrieval": False,
            "agents": False,
            "runner_sha256": sha256(RUNNER_PATH),
            "paired_helpers_sha256": sha256(PAIRED_HELPERS_PATH),
        },
        "analysis": {
            "paired_table": [
                "both_succeed",
                "control_fails_treatment_succeeds",
                "control_succeeds_treatment_fails",
                "both_fail",
            ],
            "primary_test": "two-sided exact McNemar/binomial test over discordant pairs",
            "paired_interval": "100000-resample paired percentile bootstrap",
            "bootstrap_seed": BOOTSTRAP_SEED,
            "candidate_absolute_target": "treatment malformed/unrecovered <=2/100; 0/100 preferred",
            "guardrails": "all Phase 13D-A conjunctive thresholds remain active",
        },
        "human_review": {
            "labels": {
                "grounding": ["PASS", "MINOR", "FAIL"],
                "usefulness": ["GOOD", "ACCEPTABLE", "POOR"],
                "misleading": ["YES", "NO"],
            },
            "mandatory": (
                "both arms of every MIXED, refusal, injection and conflict row, plus "
                "every malformed/unrecovered arm result"
            ),
            "additional": (
                "both arms for 10 valid non-mandatory RAG_ONLY questions sampled "
                "across category strata"
            ),
            "sampling_seed": HUMAN_SAMPLE_SEED,
            "additional_review_candidate_order": additional_review_candidate_order,
            "preferred_first_ten_question_ids": additional_review_candidate_order[:10],
            "baseline_rows": 90,
            "blind_fields": [
                "arm",
                "token_allowance",
                "call_order",
                "automated_scores",
                "pair_identity",
            ],
            "status": "PENDING_GENUINE_HUMAN_REVIEW",
        },
        "raw_artifact": "docs/phase13d-experiment-a-raw.jsonl",
        "derived_artifacts": [
            "docs/phase13d-experiment-a-outputs.json",
            "docs/phase13d-experiment-a-evaluation.json",
            "docs/phase13d-experiment-a-usage.json",
            "docs/phase13d-experiment-a-human-review-template.json",
            "docs/phase13d-experiment-a-human-review-map.json",
        ],
        "production_decision": "NO_GO_UNCHANGED",
    }


def write_json_once(path: Path, value: object) -> None:
    if path.exists():
        raise FileExistsError(f"refusing to replace frozen pre-run artifact: {path}")
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()
    questions = load_benchmark(BENCHMARK_PATH)
    inputs = build_inputs(questions)
    schedule = build_schedule(questions)
    validate_schedule(schedule, questions)
    additional_review_candidate_order = build_additional_review_candidate_order(questions)
    if not args.write:
        print("Validated paired pre-run design; use --write to freeze artifacts.")
        return
    if sha256(ORIGINAL_MANIFEST_PATH) != ORIGINAL_MANIFEST_SHA256:
        raise ValueError("original Phase 13D-A manifest changed")
    protocol_hash = sha256(PROTOCOL_PATH)
    write_json_once(SCHEDULE_PATH, schedule)
    write_json_once(INPUTS_PATH, inputs)
    manifest = build_manifest(
        protocol_hash,
        sha256(SCHEDULE_PATH),
        sha256(INPUTS_PATH),
        additional_review_candidate_order,
    )
    write_json_once(RUN_MANIFEST_PATH, manifest)
    freeze = {
        "schema_version": "phase13d-experiment-a-freeze-v1",
        "frozen_at": FROZEN_AT,
        "benchmark_sha256": PHASE13D_BENCHMARK_SHA256,
        "amended_protocol_sha256": protocol_hash,
        "schedule_sha256": sha256(SCHEDULE_PATH),
        "inputs_sha256": sha256(INPUTS_PATH),
        "run_manifest_sha256": sha256(RUN_MANIFEST_PATH),
        "provider_requests_observed_before_freeze": 0,
    }
    write_json_once(FREEZE_PATH, freeze)
    print(json.dumps(freeze, indent=2))


if __name__ == "__main__":
    main()
