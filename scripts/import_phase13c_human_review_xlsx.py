"""Validate and explicitly import a completed Phase 13C review workbook."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from scripts.phase13c_human_review import (
    CANONICAL_REVIEW_PATH,
    GROUNDING_VALUES,
    HEADERS,
    MISLEADING_VALUES,
    SCHEMA_VERSION,
    USEFULNESS_VALUES,
    build_payload,
    inspect_workbook,
)


def validate(path: Path) -> list[dict]:
    expected = build_payload()
    inspected = inspect_workbook(path)
    values = inspected["review_values"]
    if not values or values[0] != HEADERS:
        raise ValueError("unexpected Phase 13C review workbook schema")
    rows = [row for row in values[1:] if any(value not in (None, "") for value in row)]
    if len(rows) != len(expected["rows"]):
        raise ValueError(f"expected {len(expected['rows'])} review rows, got {len(rows)}")
    expected_ids = [row[1] for row in expected["rows"]]
    actual_ids = [row[1] for row in rows]
    if len(set(actual_ids)) != len(actual_ids):
        raise ValueError("duplicate Phase 13C review question ID")
    unknown = sorted(set(actual_ids) - set(expected_ids))
    if unknown:
        raise ValueError(f"unknown Phase 13C review question IDs: {unknown}")
    if actual_ids != expected_ids:
        raise ValueError("Phase 13C review row ordering changed")
    results = []
    for actual, original in zip(rows, expected["rows"], strict=True):
        actual_protected = [None if value in (None, "") else value for value in actual[:9]]
        original_protected = [None if value in (None, "") else value for value in original[:9]]
        if actual_protected != original_protected:
            raise ValueError(f"protected review evidence changed for {original[1]}")
        grounding, usefulness, misleading, notes = actual[9:13]
        if grounding not in GROUNDING_VALUES:
            raise ValueError(f"invalid or missing grounding verdict for {original[1]}")
        if usefulness not in USEFULNESS_VALUES:
            raise ValueError(f"invalid or missing usefulness for {original[1]}")
        if misleading not in MISLEADING_VALUES:
            raise ValueError(f"invalid or missing misleading verdict for {original[1]}")
        results.append(
            {
                "review_number": original[0],
                "question_id": original[1],
                "grounding": grounding,
                "usefulness": usefulness,
                "misleading": misleading,
                "notes": notes or "",
            }
        )
    return results


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("workbook", type=Path)
    parser.add_argument(
        "--confirm", action="store_true", help="write the validated review artifact"
    )
    parser.add_argument(
        "--allow-overwrite",
        action="store_true",
        help="replace an existing review artifact after explicit confirmation",
    )
    args = parser.parse_args()
    reviews = validate(args.workbook)
    print(f"Validated {len(reviews)} complete Phase 13C human reviews.")
    if not args.confirm:
        print("Validation only; pass --confirm to import.")
        return
    if CANONICAL_REVIEW_PATH.exists() and not args.allow_overwrite:
        raise SystemExit(
            "review artifact exists; pass --allow-overwrite only after explicit review"
        )
    artifact = {"schema_version": SCHEMA_VERSION, "reviews": reviews}
    CANONICAL_REVIEW_PATH.write_text(json.dumps(artifact, indent=2), encoding="utf-8")
    print(f"Imported review artifact: {CANONICAL_REVIEW_PATH}")


if __name__ == "__main__":
    main()
