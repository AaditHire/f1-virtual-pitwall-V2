"""Run and evaluate the frozen paired Phase 13D Experiment A exactly once."""

from __future__ import annotations

import argparse
import json
import random
import statistics
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter
from typing import Any

import anthropic
from pydantic import ValidationError

from f1_pitwall.knowledge.agentrouter import (
    AGENTROUTER_MODEL,
    GROUNDED_ANSWER_TOOL,
    GROUNDED_ANSWER_TOOL_NAME,
    AgentRouterConfig,
)
from f1_pitwall.knowledge.generation import (
    GeneratedAnswer,
    GroundedEvidenceBundle,
    deterministic_structured_answer,
    validate_citations,
)
from f1_pitwall.knowledge.paired_reliability import paired_failure_analysis
from f1_pitwall.knowledge.reliability import (
    PHASE13D_BENCHMARK_SHA256,
    FailureCategory,
    Phase13DOutput,
    UsageAccounting,
    assemble_bundle,
    classify_failure,
    evaluate_outputs,
    load_benchmark,
    sha256,
)
from scripts.prepare_phase13d_experiment_a import (
    BOOTSTRAP_SEED,
    FREEZE_PATH,
    INPUTS_PATH,
    PAIRED_HELPERS_PATH,
    PROTOCOL_PATH,
    RUN_MANIFEST_PATH,
    RUNNER_PATH,
    SCHEDULE_PATH,
    prepare_retriever,
)

ROOT = Path(__file__).resolve().parents[1]
RAW_PATH = ROOT / "docs" / "phase13d-experiment-a-raw.jsonl"
OUTPUTS_PATH = ROOT / "docs" / "phase13d-experiment-a-outputs.json"
EVALUATION_PATH = ROOT / "docs" / "phase13d-experiment-a-evaluation.json"
USAGE_PATH = ROOT / "docs" / "phase13d-experiment-a-usage.json"
REVIEW_TEMPLATE_PATH = ROOT / "docs" / "phase13d-experiment-a-human-review-template.json"
REVIEW_MAP_PATH = ROOT / "docs" / "phase13d-experiment-a-human-review-map.json"
SYSTEMIC_ERRORS = {"AUTH_FAILURE", "AUTHORIZATION_FAILURE", "MISSING_MODEL"}


def write_json_once(path: Path, value: object) -> None:
    if path.exists():
        raise FileExistsError(f"refusing to replace prospective artifact: {path}")
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False), encoding="utf-8")


def verify_freeze() -> tuple[dict, list[dict], dict]:
    freeze = json.loads(FREEZE_PATH.read_text(encoding="utf-8"))
    manifest = json.loads(RUN_MANIFEST_PATH.read_text(encoding="utf-8"))
    checks = {
        "benchmark_sha256": sha256(ROOT / "docs" / "phase13d-answer-benchmark.json"),
        "amended_protocol_sha256": sha256(PROTOCOL_PATH),
        "schedule_sha256": sha256(SCHEDULE_PATH),
        "inputs_sha256": sha256(INPUTS_PATH),
        "run_manifest_sha256": sha256(RUN_MANIFEST_PATH),
    }
    for key, actual in checks.items():
        if freeze[key] != actual:
            raise ValueError(f"frozen {key} mismatch: {actual}")
    if checks["benchmark_sha256"] != PHASE13D_BENCHMARK_SHA256:
        raise ValueError("Phase 13D benchmark changed")
    if manifest["configuration"]["runner_sha256"] != sha256(RUNNER_PATH):
        raise ValueError("frozen runner changed")
    if manifest["configuration"]["paired_helpers_sha256"] != sha256(PAIRED_HELPERS_PATH):
        raise ValueError("frozen paired analysis helpers changed")
    schedule = json.loads(SCHEDULE_PATH.read_text(encoding="utf-8"))["calls"]
    inputs = json.loads(INPUTS_PATH.read_text(encoding="utf-8"))
    if len(schedule) != 200 or len(inputs) != 100:
        raise ValueError("frozen run cardinality mismatch")
    return manifest, inputs, freeze


def safe_raw_response(message: Any) -> dict:
    if hasattr(message, "model_dump"):
        return message.model_dump(mode="json")
    raise TypeError("official SDK response cannot be serialized")


def usage_from_message(message: Any) -> dict[str, int | None]:
    usage = getattr(message, "usage", None)
    prompt = getattr(usage, "input_tokens", None)
    completion = getattr(usage, "output_tokens", None)
    return {
        "prompt_tokens": prompt,
        "completion_tokens": completion,
        "total_tokens": (
            prompt + completion if isinstance(prompt, int) and isinstance(completion, int) else None
        ),
        "reasoning_tokens": None,
        "cached_tokens": getattr(usage, "cache_read_input_tokens", None),
    }


def parse_message(
    message: Any, bundle: GroundedEvidenceBundle
) -> tuple[GeneratedAnswer | None, str]:
    if getattr(message, "stop_reason", None) == "max_tokens":
        return None, FailureCategory.TRUNCATED_RESPONSE.value
    content = getattr(message, "content", None)
    if not isinstance(content, list):
        return None, FailureCategory.MALFORMED_RESPONSE.value
    blocks = [block for block in content if getattr(block, "type", None) == "tool_use"]
    if len(blocks) != 1 or getattr(blocks[0], "name", None) != GROUNDED_ANSWER_TOOL_NAME:
        return None, FailureCategory.MALFORMED_RESPONSE.value
    try:
        answer = GeneratedAnswer.model_validate(getattr(blocks[0], "input", None))
    except ValidationError:
        return None, FailureCategory.SCHEMA_FAILURE.value
    validation = validate_citations(answer, bundle)
    if validation.unknown or validation.malformed:
        return answer, FailureCategory.INVALID_CITATION.value
    return answer, FailureCategory.SUCCESS.value


def exception_category(exc: Exception) -> tuple[str, int | None]:
    if isinstance(exc, anthropic.AuthenticationError):
        return "AUTH_FAILURE", getattr(exc, "status_code", None)
    if isinstance(exc, anthropic.PermissionDeniedError):
        return "AUTHORIZATION_FAILURE", getattr(exc, "status_code", None)
    if isinstance(exc, anthropic.NotFoundError):
        return "MISSING_MODEL", getattr(exc, "status_code", None)
    if isinstance(exc, anthropic.APITimeoutError):
        return "TIMEOUT", getattr(exc, "status_code", None)
    if isinstance(exc, anthropic.RateLimitError):
        return "RATE_LIMIT", getattr(exc, "status_code", None)
    if isinstance(exc, anthropic.InternalServerError):
        return "SERVER_ERROR", getattr(exc, "status_code", None)
    if isinstance(exc, anthropic.APIConnectionError):
        return "CONNECTION_FAILURE", None
    if isinstance(exc, anthropic.APIResponseValidationError):
        return "MALFORMED_RESPONSE", getattr(exc, "status_code", None)
    if isinstance(exc, anthropic.APIStatusError):
        return "REQUEST_FAILURE", getattr(exc, "status_code", None)
    return "CLIENT_FAILURE", None


def execute_calls(inputs: list[dict]) -> list[dict]:
    if RAW_PATH.exists():
        raise FileExistsError(f"refusing to resume or replace raw run: {RAW_PATH}")
    config = AgentRouterConfig.from_env()
    client = anthropic.Anthropic(
        auth_token=config.api_key,
        base_url=config.base_url,
        timeout=config.timeout_seconds,
        max_retries=0,
    )
    by_id = {row["question_id"]: row for row in inputs}
    schedule = json.loads(SCHEDULE_PATH.read_text(encoding="utf-8"))["calls"]
    records: list[dict] = []
    with RAW_PATH.open("x", encoding="utf-8", newline="\n") as raw_file:
        for call in schedule:
            frozen_input = by_id[call["question_id"]]
            bundle = GroundedEvidenceBundle.model_validate(frozen_input["bundle"])
            messages = frozen_input["messages"]
            started_at = datetime.now(UTC).isoformat()
            started = perf_counter()
            message = None
            answer = None
            category = None
            status_code = None
            safe_error = None
            try:
                message = client.messages.create(
                    model=AGENTROUTER_MODEL,
                    system=messages[0]["content"],
                    messages=messages[1:],
                    max_tokens=call["max_output_tokens"],
                    tools=[GROUNDED_ANSWER_TOOL],
                    tool_choice={
                        "type": "tool",
                        "name": GROUNDED_ANSWER_TOOL_NAME,
                        "disable_parallel_tool_use": True,
                    },
                )
                answer, category = parse_message(message, bundle)
            except Exception as exc:  # provider failures must be recorded once
                category, status_code = exception_category(exc)
                safe_error = f"{type(exc).__name__}: provider request failed"
            latency_ms = (perf_counter() - started) * 1000
            record = {
                **call,
                "route_type": frozen_input["route_type"],
                "started_at": started_at,
                "completed_at": datetime.now(UTC).isoformat(),
                "latency_ms": latency_ms,
                "bundle_sha256": frozen_input["bundle_sha256"],
                "messages_sha256": frozen_input["messages_sha256"],
                "request_count": 1,
                "retry_count": 0,
                "stop_reason": getattr(message, "stop_reason", None),
                "request_id": getattr(message, "_request_id", None),
                "usage": usage_from_message(message) if message is not None else {},
                "classification": category,
                "parsed_answer": answer.model_dump(mode="json") if answer else None,
                "http_status": status_code,
                "provider_error": safe_error,
                "raw_provider_response": safe_raw_response(message)
                if message is not None
                else None,
            }
            raw_file.write(json.dumps(record, ensure_ascii=False) + "\n")
            raw_file.flush()
            records.append(record)
            if call["call_order"] % 10 == 0:
                failures = sum(row["classification"] != "SUCCESS" for row in records)
                print(
                    f"progress {call['call_order']}/200; terminal_failures={failures}", flush=True
                )
            if category in SYSTEMIC_ERRORS:
                raise RuntimeError(
                    f"systemic provider stop after call {call['call_order']}: {category}"
                )
    return records


def output_from_raw(question, frozen_input: dict, raw: dict) -> Phase13DOutput:
    return Phase13DOutput(
        question_id=question.question_id,
        route_type=question.route_type,
        provider="AgentRouter",
        model_id=AGENTROUTER_MODEL,
        answer=raw["parsed_answer"],
        bundle=frozen_input["bundle"],
        provider_error_category=(
            None if raw["classification"] == "SUCCESS" else raw["classification"]
        ),
        stop_reason=raw["stop_reason"],
        raw_response_present=raw["raw_provider_response"] is not None,
        recovered=False,
        request_count=1,
        retry_count=0,
        latency_ms=raw["latency_ms"],
        usage=UsageAccounting(**raw["usage"]),
    )


def candidate_guardrails(metrics: dict) -> dict[str, bool | None]:
    return {
        "malformed_unrecovered_le_2_of_100": metrics["unrecovered_failures"] <= 2,
        "citation_validity_ge_99pct": (metrics["citation_validity"] or 0) >= 0.99,
        "citation_completeness_ge_90pct": (metrics["citation_completeness"] or 0) >= 0.90,
        "citation_support_ge_90pct": (metrics["citation_support"] or 0) >= 0.90,
        "uncited_sentence_proxy_le_25_25pct": (metrics["unsupported_uncited_sentence_proxy"] or 0)
        <= 0.2525,
        "required_fact_coverage_ge_90pct": (metrics["required_fact_coverage"] or 0) >= 0.90,
        "refusal_correctness_5_of_5": metrics["correct_refusals"] == 5,
        "false_refusals_zero": metrics["false_refusals"] == 0,
        "multi_document_ge_95pct": (metrics["multi_document_synthesis"] or 0) >= 0.95,
        "conflict_handling_2_of_2": metrics["conflict_handling_passes"] == 2,
        "structured_facts_30_of_30": metrics["structured_facts_correct"] == 30,
        "mixed_exact_facts_25_of_25": metrics["mixed_exact_facts_correct"] == 25,
        "route_compliance_130_of_130": metrics["route_compliant"] == 130,
        "prompt_injection_3_of_3": metrics["prompt_injection_passes"] == 3,
        "human_misleading_zero": None,
        "human_semantic_safety": None,
    }


def derive_artifacts(manifest: dict, inputs: list[dict], raw: list[dict]) -> dict:
    questions = load_benchmark(ROOT / "docs" / "phase13d-answer-benchmark.json")
    retriever = prepare_retriever()
    input_by_id = {row["question_id"]: row for row in inputs}
    raw_by_key = {(row["question_id"], row["arm"]): row for row in raw}
    outputs_by_arm: dict[str, list[Phase13DOutput]] = {}
    metrics_by_arm = {}
    for arm in ("CONTROL_320", "TREATMENT_640"):
        outputs = []
        for question in questions:
            if not question.generation_required:
                bundle = assemble_bundle(question, retriever)
                outputs.append(
                    Phase13DOutput(
                        question_id=question.question_id,
                        route_type=question.route_type,
                        answer=deterministic_structured_answer(bundle),
                        bundle=bundle,
                    )
                )
            else:
                outputs.append(
                    output_from_raw(
                        question,
                        input_by_id[question.question_id],
                        raw_by_key[(question.question_id, arm)],
                    )
                )
        outputs_by_arm[arm] = outputs
        metrics_by_arm[arm] = evaluate_outputs(questions, outputs)
    generation_ids = [row.question_id for row in questions if row.generation_required]
    pairs = [
        (
            classify_failure(raw_output(outputs_by_arm["CONTROL_320"], question_id))
            != FailureCategory.SUCCESS,
            classify_failure(raw_output(outputs_by_arm["TREATMENT_640"], question_id))
            != FailureCategory.SUCCESS,
        )
        for question_id in generation_ids
    ]
    paired = paired_failure_analysis(pairs, bootstrap_seed=BOOTSTRAP_SEED)
    guardrails = candidate_guardrails(metrics_by_arm["TREATMENT_640"])
    automated_guardrails = [value for value in guardrails.values() if value is not None]
    if paired["treatment_failures"] >= paired["control_failures"] or not all(automated_guardrails):
        h1 = "NOT_SUPPORTED"
    elif paired["exact_mcnemar_two_sided_p"] <= 0.05:
        h1 = "PENDING_HUMAN_REVIEW"
    else:
        h1 = "WEAK_INCONCLUSIVE"
    output_artifact = {
        "schema_version": "phase13d-experiment-a-outputs-v1",
        "arms": {
            arm: [row.model_dump(mode="json") for row in outputs]
            for arm, outputs in outputs_by_arm.items()
        },
    }
    evaluation = {
        "schema_version": "phase13d-experiment-a-evaluation-v1",
        "arm_metrics": metrics_by_arm,
        "paired_primary": paired,
        "treatment_guardrails": guardrails,
        "h1_status": h1,
        "human_review_status": "PENDING_GENUINE_HUMAN_REVIEW",
        "production_decision": "NO_GO_UNCHANGED",
    }
    write_json_once(OUTPUTS_PATH, output_artifact)
    write_json_once(EVALUATION_PATH, evaluation)
    write_json_once(USAGE_PATH, build_usage(raw))
    build_review_artifacts(manifest, questions, outputs_by_arm, raw_by_key)
    return evaluation


def raw_output(outputs: list[Phase13DOutput], question_id: str) -> Phase13DOutput:
    return next(row for row in outputs if row.question_id == question_id)


def build_usage(raw: list[dict]) -> dict:
    latencies = [row["latency_ms"] for row in raw]
    return {
        "provider": "AgentRouter",
        "model_id": AGENTROUTER_MODEL,
        "requests": len(raw),
        "retries": sum(row["retry_count"] for row in raw),
        "prompt_tokens": sum((row["usage"].get("prompt_tokens") or 0) for row in raw),
        "completion_tokens": sum((row["usage"].get("completion_tokens") or 0) for row in raw),
        "total_tokens": sum((row["usage"].get("total_tokens") or 0) for row in raw),
        "latency_median_ms": statistics.median(latencies),
        "latency_p90_ms": sorted(latencies)[int(0.9 * (len(latencies) - 1))],
        "new_external_cash_spent": 0.0,
        "provider_credit_cost": None,
        "balance_api_available": False,
    }


def build_review_artifacts(manifest, questions, outputs_by_arm, raw_by_key) -> None:
    mandatory_ids = {
        row.question_id
        for row in questions
        if row.route_type.value == "MIXED" or row.expected_refusal or row.adversarial_kind != "NONE"
    }
    additional_ids = []
    for question_id in manifest["human_review"]["additional_review_candidate_order"]:
        if all(
            raw_by_key[(question_id, arm)]["classification"] == "SUCCESS"
            for arm in ("CONTROL_320", "TREATMENT_640")
        ):
            additional_ids.append(question_id)
        if len(additional_ids) == 10:
            break
    if len(additional_ids) != 10:
        raise ValueError("fewer than ten valid paired RAG human-review candidates")
    selected = {
        (question_id, arm)
        for question_id in mandatory_ids | set(additional_ids)
        for arm in ("CONTROL_320", "TREATMENT_640")
    }
    selected |= {
        (row["question_id"], row["arm"])
        for row in raw_by_key.values()
        if row["classification"] != "SUCCESS"
    }
    rows = sorted(selected)
    rng = random.Random(manifest["human_review"]["sampling_seed"] + 1)
    rng.shuffle(rows)
    question_by_id = {row.question_id: row for row in questions}
    template = []
    mapping = []
    for index, (question_id, arm) in enumerate(rows, 1):
        review_id = f"R{index:03d}"
        question = question_by_id[question_id]
        output = raw_output(outputs_by_arm[arm], question_id)
        template.append(
            {
                "review_id": review_id,
                "question": question.question,
                "controlled_evidence": output.bundle.model_dump(mode="json"),
                "answer": output.answer.model_dump(mode="json") if output.answer else None,
                "grounding": "",
                "usefulness": "",
                "misleading": "",
                "notes": "",
            }
        )
        mapping.append(
            {
                "review_id": review_id,
                "question_id": question_id,
                "arm": arm,
                "max_output_tokens": raw_by_key[(question_id, arm)]["max_output_tokens"],
                "call_order": raw_by_key[(question_id, arm)]["call_order"],
                "classification": raw_by_key[(question_id, arm)]["classification"],
            }
        )
    write_json_once(REVIEW_TEMPLATE_PATH, template)
    write_json_once(REVIEW_MAP_PATH, mapping)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", help="execute exactly 200 provider calls")
    args = parser.parse_args()
    manifest, inputs, freeze = verify_freeze()
    if not args.live:
        print(json.dumps({"validated": True, "freeze": freeze}, indent=2))
        return
    raw = execute_calls(inputs)
    evaluation = derive_artifacts(manifest, inputs, raw)
    print(json.dumps(evaluation, indent=2))


if __name__ == "__main__":
    main()
