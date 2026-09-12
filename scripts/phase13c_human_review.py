"""Deterministic selection and validation for the Phase 13C review workbook."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path

from scripts.research_phase13c import BENCHMARK_PATH, DOCS, OUTPUTS_PATH, sha256

BENCHMARK_SHA256 = "969ff9d522a4f047493c1c1fc2ba9eaeddeb80f10988bd271f87078c7112809e"
SCHEMA_VERSION = "phase13c-human-review-v1"
SAMPLE_SIZE = 25
OUTPUT_DIR = Path("outputs/phase13c_human_review")
WORKBOOK_PATH = OUTPUT_DIR / "phase13c_human_review.xlsx"
MANIFEST_PATH = OUTPUT_DIR / "phase13c_human_review_manifest.json"
CANONICAL_REVIEW_PATH = DOCS / "phase13c-human-review-results.json"
HEADERS = [
    "Review Number",
    "Benchmark Question ID",
    "Route Type",
    "Question",
    "Structured Facts",
    "Retrieved Evidence",
    "Generated Answer",
    "Citations",
    "Deterministic Evaluation Summary",
    "Grounding Verdict",
    "Usefulness",
    "Misleading",
    "Notes",
]
GROUNDING_VALUES = {"PASS", "MINOR_ISSUE", "FAIL"}
USEFULNESS_VALUES = {"GOOD", "ACCEPTABLE", "POOR"}
MISLEADING_VALUES = {"NO", "YES"}


def _load() -> tuple[list[dict], list[dict]]:
    if sha256(BENCHMARK_PATH) != BENCHMARK_SHA256:
        raise ValueError("frozen Phase 13C benchmark hash changed")
    records = json.loads(BENCHMARK_PATH.read_text(encoding="utf-8"))
    outputs = json.loads(OUTPUTS_PATH.read_text(encoding="utf-8"))
    if len(records) != 60 or len(outputs) != 60:
        raise ValueError("Phase 13C review requires all 60 benchmark outputs")
    if [row["question_id"] for row in records] != [row["question_id"] for row in outputs]:
        raise ValueError("benchmark/output question ordering mismatch")
    return records, outputs


def _selected_indexes(records: list[dict], outputs: list[dict]) -> list[int]:
    selected: list[int] = []

    def add(predicate) -> None:
        for index, (record, output) in enumerate(zip(records, outputs, strict=True)):
            if predicate(record, output) and index not in selected and len(selected) < SAMPLE_SIZE:
                selected.append(index)

    add(lambda _record, output: bool(output.get("provider_error")))
    add(lambda record, _output: record["category"] in {"PROMPT_INJECTION", "SOURCE_CONFLICT"})
    add(lambda record, _output: record["expected_refusal"])
    add(lambda record, _output: record["route_type"] == "MIXED")
    add(lambda record, output: record["route_type"] == "STRUCTURED_ONLY" and bool(output["answer"]))
    add(lambda record, _output: record["requires_all_evidence"])
    add(lambda _record, _output: True)
    if len(selected) != SAMPLE_SIZE:
        raise ValueError(f"expected {SAMPLE_SIZE} review rows, got {len(selected)}")
    return selected


def _evidence_text(output: dict) -> str:
    blocks = []
    for source in output["bundle"]["sources"]:
        blocks.append(
            f"[{source['source_id']}] {source['source_name']}\n"
            f"{source['text']}\nSource: {source['source_url']}"
        )
    return "\n\n".join(blocks) or "No retrieved evidence."


def _summary(record: dict, output: dict) -> str:
    answer = output.get("answer") or {}
    text = str(answer.get("answer", "")).casefold()
    found = sum(str(fact).casefold() in text for fact in record["required_facts"])
    validation = output["citation_validation"]
    parts = [
        f"Category: {record['category']}",
        f"Required facts: {found}/{len(record['required_facts'])}",
        f"Citation validation: {'PASS' if validation['valid'] else 'FAIL'}",
    ]
    if record["expected_refusal"]:
        refused = answer.get("status") == "INSUFFICIENT_EVIDENCE"
        parts.append(f"Expected refusal: {'PASS' if refused else 'FAIL'}")
    if output.get("provider_error"):
        parts.append(f"Provider output: {output['provider_error']['category']}")
    return "; ".join(parts)


def build_payload() -> dict:
    records, outputs = _load()
    indexes = _selected_indexes(records, outputs)
    rows = []
    for review_number, index in enumerate(indexes, 1):
        record, output = records[index], outputs[index]
        answer = output.get("answer") or {}
        rows.append(
            [
                review_number,
                record["question_id"],
                record["route_type"],
                record["question"],
                json.dumps(output["bundle"]["structured_facts"], ensure_ascii=False),
                _evidence_text(output),
                answer.get("answer", "[No valid generated answer]"),
                "; ".join(answer.get("citations", [])),
                _summary(record, output),
                None,
                None,
                None,
                None,
            ]
        )
    immutable_hash = hashlib.sha256(
        json.dumps([row[:9] for row in rows], ensure_ascii=False, separators=(",", ":")).encode()
    ).hexdigest()
    return {
        "schema_version": SCHEMA_VERSION,
        "benchmark_sha256": BENCHMARK_SHA256,
        "outputs_sha256": sha256(OUTPUTS_PATH),
        "headers": HEADERS,
        "rows": rows,
        "immutable_hash": immutable_hash,
        "grounding_values": sorted(GROUNDING_VALUES),
        "usefulness_values": sorted(USEFULNESS_VALUES),
        "misleading_values": sorted(MISLEADING_VALUES),
    }


def artifact_runtime() -> tuple[Path, Path]:
    runtime_root = Path(".cache/phase13c-review-runtime").resolve()
    runtime_root.mkdir(parents=True, exist_ok=True)
    dependencies = Path(
        os.environ.get(
            "PHASE13C_ARTIFACT_DEPENDENCIES",
            str(Path.home() / ".cache/codex-runtimes/codex-primary-runtime/dependencies"),
        )
    )
    node = dependencies / "node/bin/node.exe"
    if not node.exists():
        node = dependencies / "node/bin/node"
    modules = dependencies / "node/node_modules"
    if not node.exists() or not (modules / "@oai/artifact-tool").exists():
        raise RuntimeError("bundled spreadsheet runtime is unavailable")
    junction = runtime_root / "node_modules"
    if not junction.exists():
        if os.name == "nt":
            subprocess.run(
                ["cmd", "/c", "mklink", "/J", str(junction), str(modules)],
                check=True,
                capture_output=True,
                text=True,
            )
        else:
            junction.symlink_to(modules, target_is_directory=True)
    builder = runtime_root / "phase13c_human_review_artifact.mjs"
    shutil.copy2(Path(__file__).with_name("phase13c_human_review_artifact.mjs"), builder)
    return node, builder


def run_artifact(command: str, *paths: Path) -> subprocess.CompletedProcess[str]:
    node, builder = artifact_runtime()
    return subprocess.run(
        [str(node), str(builder), command, *(str(path.resolve()) for path in paths)],
        check=True,
        capture_output=True,
        text=True,
    )


def inspect_workbook(path: Path) -> dict:
    inspection = Path(".cache/phase13c-review-runtime/inspection.json")
    run_artifact("inspect", path, inspection)
    return json.loads(inspection.read_text(encoding="utf-8"))
