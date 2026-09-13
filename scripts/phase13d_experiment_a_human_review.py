"""Build the frozen Phase 13D Experiment A human-review packet."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
from collections import Counter
from pathlib import Path

from f1_pitwall.knowledge.reliability import PHASE13D_BENCHMARK_SHA256, load_benchmark

ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs"
TEMPLATE_PATH = DOCS / "phase13d-experiment-a-human-review-template.json"
MAP_PATH = DOCS / "phase13d-experiment-a-human-review-map.json"
OUTPUT_DIR = ROOT / "outputs" / "phase13d_experiment_a_human_review"
WORKBOOK_PATH = OUTPUT_DIR / "phase13d_experiment_a_human_review.xlsx"
MANIFEST_PATH = OUTPUT_DIR / "phase13d_experiment_a_human_review_manifest.json"
CANONICAL_REVIEW_PATH = DOCS / "phase13d-experiment-a-human-review-results.json"
SCHEMA_VERSION = "phase13d-experiment-a-human-review-v1"
EXPECTED_ROWS = 169
TEMPLATE_SHA256 = "7609da225597e6713675acd1a2199e3b8270f4cb53b454d4f77225eeaefb00c6"
MAP_SHA256 = "ecf0a608dcff875f3737bef16b1e5bc19e8a02f4adecb1cc4d13656a2391b947"
GROUNDING_VALUES = {"PASS", "MINOR", "FAIL"}
USEFULNESS_VALUES = {"GOOD", "ACCEPTABLE", "POOR"}
MISLEADING_VALUES = {"YES", "NO"}
HEADERS = [
    "Review ID",
    "Question ID",
    "Route",
    "Blinded Arm",
    "Question",
    "Expected / Required Facts",
    "Supplied Evidence",
    "Generated Answer",
    "Cited Source IDs",
    "Parser / Failure Status",
    "Grounding",
    "Usefulness",
    "Misleading",
    "Reviewer Notes",
]


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _normalize(value: object) -> object:
    return None if value in (None, "") else value


def _facts_text(question) -> str:
    if question.expected_refusal:
        return "Expected refusal: supplied evidence does not establish the requested information."
    facts = []
    for fact in question.expected_facts:
        sources = ", ".join(fact.source_keys)
        facts.append(f"{fact.name}: {fact.value} | {fact.kind} | sources: {sources}")
    return "\n".join(facts) or "No expected facts."


def _evidence_text(template_row: dict) -> str:
    bundle = template_row["controlled_evidence"]
    sections = []
    if bundle["structured_facts"]:
        facts = "; ".join(
            f"{fact['name']}: {fact['value']} [{fact['source_id']}]"
            for fact in bundle["structured_facts"]
        )
        sections.append(f"Authoritative structured facts\n{facts}")
    for source in bundle["sources"]:
        sections.append(
            f"[{source['source_id']}] {source['source_name']}\n"
            f"{source['text']}\n"
            f"Source: {source['source_url']}\n"
            f"Provenance: {source['provenance']}"
        )
    if bundle["conflicts"]:
        sections.append(
            "Declared conflicts\n" + json.dumps(bundle["conflicts"], ensure_ascii=False)
        )
    if bundle["insufficiency_reason"]:
        sections.append(f"Insufficiency reason\n{bundle['insufficiency_reason']}")
    return "\n\n".join(sections) or "No supplied evidence."


def build_payload() -> dict:
    if sha256(TEMPLATE_PATH) != TEMPLATE_SHA256:
        raise ValueError("frozen Phase 13D human-review template hash changed")
    if sha256(MAP_PATH) != MAP_SHA256:
        raise ValueError("frozen Phase 13D human-review map hash changed")
    questions = load_benchmark(DOCS / "phase13d-answer-benchmark.json")
    template = json.loads(TEMPLATE_PATH.read_text(encoding="utf-8"))
    mapping = json.loads(MAP_PATH.read_text(encoding="utf-8"))
    template_rows = template["rows"]
    map_rows = mapping["rows"]
    if len(template_rows) != EXPECTED_ROWS or len(map_rows) != EXPECTED_ROWS:
        raise ValueError("Phase 13D human-review population must remain exactly 169 rows")
    if [row["review_id"] for row in template_rows] != [row["review_id"] for row in map_rows]:
        raise ValueError("frozen template/map review ordering mismatch")
    if len({row["review_id"] for row in template_rows}) != EXPECTED_ROWS:
        raise ValueError("duplicate review IDs in frozen template")
    question_by_id = {row.question_id: row for row in questions}
    packet_rows = []
    for template_row, map_row in zip(template_rows, map_rows, strict=True):
        question = question_by_id[map_row["question_id"]]
        answer = template_row["answer"] or {}
        packet_rows.append(
            [
                template_row["review_id"],
                question.question_id,
                question.route_type.value,
                "ARM_A" if map_row["arm"] == "CONTROL_320" else "ARM_B",
                question.question,
                _facts_text(question),
                _evidence_text(template_row),
                answer.get("answer", f"[No valid parsed answer: {map_row['classification']}]"),
                "; ".join(answer.get("citations", [])),
                map_row["classification"],
                None,
                None,
                None,
                None,
            ]
        )
    immutable_hash = hashlib.sha256(
        json.dumps(
            [[_normalize(value) for value in row[:10]] for row in packet_rows],
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    return {
        "schema_version": SCHEMA_VERSION,
        "benchmark_sha256": PHASE13D_BENCHMARK_SHA256,
        "template_sha256": TEMPLATE_SHA256,
        "map_sha256": MAP_SHA256,
        "review_rows": EXPECTED_ROWS,
        "sampling_shortfall": template["metadata"]["sampler_shortfall"],
        "available_additional_valid_pairs": template["metadata"][
            "available_additional_valid_pairs"
        ],
        "headers": HEADERS,
        "rows": packet_rows,
        "immutable_hash": immutable_hash,
        "grounding_values": sorted(GROUNDING_VALUES),
        "usefulness_values": sorted(USEFULNESS_VALUES),
        "misleading_values": sorted(MISLEADING_VALUES),
        "composition": {
            "routes": dict(Counter(row[2] for row in packet_rows)),
            "blinded_arms": dict(Counter(row[3] for row in packet_rows)),
            "mechanical_status": dict(Counter(row[9] for row in packet_rows)),
        },
    }


def artifact_runtime() -> tuple[Path, Path]:
    runtime_root = ROOT / ".cache" / "phase13d-review-runtime"
    runtime_root.mkdir(parents=True, exist_ok=True)
    dependencies = Path(
        os.environ.get(
            "PHASE13D_ARTIFACT_DEPENDENCIES",
            r"C:\Users\Admin\.cache\codex-runtimes\codex-primary-runtime\dependencies",
        )
    )
    node = dependencies / "node" / "bin" / ("node.exe" if os.name == "nt" else "node")
    modules = dependencies / "node" / "node_modules"
    if not node.exists() or not (modules / "@oai" / "artifact-tool").exists():
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
    builder = runtime_root / "phase13d_experiment_a_human_review_artifact.mjs"
    shutil.copy2(Path(__file__).with_name(builder.name), builder)
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
    inspection = ROOT / ".cache" / "phase13d-review-runtime" / "inspection.json"
    run_artifact("inspect", path, inspection)
    return json.loads(inspection.read_text(encoding="utf-8"))
