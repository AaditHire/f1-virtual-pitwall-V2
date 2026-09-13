"""Build and validate the frozen Experiment B recovered-answer review packet."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path

from f1_pitwall.knowledge.reliability import sha256

ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs"
TEMPLATE = DOCS / "phase13d-experiment-b-human-review-template.json"
MAP = DOCS / "phase13d-experiment-b-human-review-map.json"
OUTPUT_DIR = ROOT / "outputs" / "phase13d_experiment_b_human_review"
WORKBOOK = OUTPUT_DIR / "phase13d_experiment_b_human_review.xlsx"
MANIFEST = OUTPUT_DIR / "phase13d_experiment_b_human_review_manifest.json"
CANONICAL_REVIEW = DOCS / "phase13d-experiment-b-human-review-results.json"
SCHEMA_VERSION = "phase13d-experiment-b-human-review-v1"
GROUNDING_VALUES = {"PASS", "MINOR", "FAIL"}
USEFULNESS_VALUES = {"GOOD", "ACCEPTABLE", "POOR"}
MISLEADING_VALUES = {"YES", "NO"}
HEADERS = [
    "Review ID",
    "Question ID",
    "Route",
    "Question",
    "Expected / Required Facts",
    "Supplied Evidence",
    "Recovered Retry Answer",
    "Cited Source IDs",
    "Original Failure",
    "Retry Parser Status",
    "Grounding",
    "Usefulness",
    "Misleading",
    "Reviewer Notes",
]


def _normalize(value: object) -> object:
    return None if value in (None, "") else value


def build_payload() -> dict:
    template = json.loads(TEMPLATE.read_text(encoding="utf-8"))
    mapping = json.loads(MAP.read_text(encoding="utf-8"))
    rows = template["rows"]
    map_rows = mapping["rows"]
    if not rows or len(rows) != len(map_rows):
        raise ValueError("recovered-answer review population is empty or mismatched")
    if mapping["template_sha256"] != sha256(TEMPLATE):
        raise ValueError("Experiment B review template hash changed")
    if [row["review_id"] for row in rows] != [row["review_id"] for row in map_rows]:
        raise ValueError("Experiment B review template/map ordering changed")
    packet_rows = [
        [
            row["review_id"],
            row["question_id"],
            row["route_type"],
            row["question"],
            row["expected_required_facts"],
            row["supplied_evidence"],
            row["generated_answer"],
            "; ".join(row["cited_source_ids"]),
            row["original_failure_category"],
            row["retry_parser_status"],
            None,
            None,
            None,
            None,
        ]
        for row in rows
    ]
    protected_hash = hashlib.sha256(
        json.dumps(
            [[_normalize(value) for value in row[:10]] for row in packet_rows],
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    return {
        "schema_version": SCHEMA_VERSION,
        "status": "PENDING_GENUINE_HUMAN_REVIEW",
        "review_rows": len(packet_rows),
        "review_all_recovered": True,
        "template_sha256": sha256(TEMPLATE),
        "map_sha256": sha256(MAP),
        "headers": HEADERS,
        "rows": packet_rows,
        "protected_hash": protected_hash,
        "grounding_values": sorted(GROUNDING_VALUES),
        "usefulness_values": sorted(USEFULNESS_VALUES),
        "misleading_values": sorted(MISLEADING_VALUES),
    }


def artifact_runtime() -> tuple[Path, Path]:
    runtime_root = ROOT / ".cache" / "phase13d-b-review-runtime"
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
    builder = runtime_root / "phase13d_experiment_b_human_review_artifact.mjs"
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
    inspection = ROOT / ".cache" / "phase13d-b-review-runtime" / "inspection.json"
    run_artifact("inspect", path, inspection)
    return json.loads(inspection.read_text(encoding="utf-8"))
