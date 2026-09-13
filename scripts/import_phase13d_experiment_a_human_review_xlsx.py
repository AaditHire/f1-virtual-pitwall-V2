"""Validate and explicitly import completed Phase 13D Experiment A human review."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from scripts.phase13d_experiment_a_human_review import (
    CANONICAL_REVIEW_PATH,
    GROUNDING_VALUES,
    HEADERS,
    MISLEADING_VALUES,
    SCHEMA_VERSION,
    USEFULNESS_VALUES,
    build_payload,
    inspect_workbook,
)


def _normalize(value: object) -> object:
    return None if value in (None, "") else value


def validate(path: Path) -> list[dict]:
    expected = build_payload()
    inspected = inspect_workbook(path)
    values = inspected["review_values"]
    if not values or values[0] != HEADERS:
        raise ValueError("unexpected Phase 13D human-review workbook schema")
    rows = [row for row in values[1:] if any(value not in (None, "") for value in row)]
    if len(rows) != len(expected["rows"]):
        raise ValueError(f"expected {len(expected['rows'])} review rows, got {len(rows)}")
    actual_ids = [row[0] for row in rows]
    expected_ids = [row[0] for row in expected["rows"]]
    if len(set(actual_ids)) != len(actual_ids):
        raise ValueError("duplicate Phase 13D review ID")
    if set(actual_ids) != set(expected_ids):
        missing = sorted(set(expected_ids) - set(actual_ids))
        extra = sorted(set(actual_ids) - set(expected_ids))
        raise ValueError(f"unauthorized review population; missing={missing}, extra={extra}")
    if actual_ids != expected_ids:
        raise ValueError("Phase 13D review row ordering changed")
    results = []
    protected_rows = []
    for actual, original in zip(rows, expected["rows"], strict=True):
        actual_protected = [_normalize(value) for value in actual[:10]]
        original_protected = [_normalize(value) for value in original[:10]]
        if actual_protected != original_protected:
            raise ValueError(f"protected review context changed for {original[0]}")
        protected_rows.append(actual_protected)
        grounding, usefulness, misleading, notes = actual[10:14]
        if grounding not in GROUNDING_VALUES:
            raise ValueError(f"invalid or missing grounding verdict for {original[0]}")
        if usefulness not in USEFULNESS_VALUES:
            raise ValueError(f"invalid or missing usefulness verdict for {original[0]}")
        if misleading not in MISLEADING_VALUES:
            raise ValueError(f"invalid or missing misleading verdict for {original[0]}")
        results.append(
            {
                "review_id": original[0],
                "question_id": original[1],
                "blinded_arm": original[3],
                "grounding": grounding,
                "usefulness": usefulness,
                "misleading": misleading,
                "notes": notes or "",
            }
        )
    protected_hash = hashlib.sha256(
        json.dumps(protected_rows, ensure_ascii=False, separators=(",", ":")).encode()
    ).hexdigest()
    if protected_hash != expected["immutable_hash"]:
        raise ValueError("protected human-review packet hash changed")
    return results


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("workbook", type=Path)
    parser.add_argument(
        "--confirm-import",
        action="store_true",
        help="write the validated canonical review artifact",
    )
    args = parser.parse_args()
    results = validate(args.workbook)
    print(f"Validated {len(results)} complete Phase 13D human-review rows.")
    if not args.confirm_import:
        print("Validation only; pass --confirm-import to write the canonical artifact.")
        return
    if CANONICAL_REVIEW_PATH.exists():
        raise SystemExit(f"refusing to overwrite existing review artifact: {CANONICAL_REVIEW_PATH}")
    artifact = {
        "schema_version": SCHEMA_VERSION,
        "status": "COMPLETE_HUMAN_REVIEW",
        "reviews": results,
    }
    CANONICAL_REVIEW_PATH.write_text(json.dumps(artifact, indent=2), encoding="utf-8")
    print(f"Imported review artifact: {CANONICAL_REVIEW_PATH}")


if __name__ == "__main__":
    main()
