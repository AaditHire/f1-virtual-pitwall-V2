"""Validate and explicitly import a completed Experiment C human review."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from scripts.phase13d_experiment_c_human_review import (
    CANONICAL_REVIEW,
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
    values = inspect_workbook(path)["review_values"]
    if not values or values[0] != HEADERS:
        raise ValueError("unexpected Experiment C human-review workbook schema")
    rows = [row for row in values[1:] if any(value not in (None, "") for value in row)]
    if len(rows) != expected["review_rows"]:
        raise ValueError(f"expected {expected['review_rows']} review rows, got {len(rows)}")
    ids, expected_ids = [row[0] for row in rows], [row[0] for row in expected["rows"]]
    if len(set(ids)) != len(ids):
        raise ValueError("duplicate Experiment C review ID")
    if ids != expected_ids:
        raise ValueError("Experiment C review population or ordering changed")
    results, protected = [], []
    for actual, original in zip(rows, expected["rows"], strict=True):
        actual_context = [_normalize(value) for value in actual[:10]]
        original_context = [_normalize(value) for value in original[:10]]
        if actual_context != original_context:
            raise ValueError(f"protected review context changed for {original[0]}")
        protected.append(actual_context)
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
                "grounding": grounding,
                "usefulness": usefulness,
                "misleading": misleading,
                "notes": notes or "",
            }
        )
    protected_hash = hashlib.sha256(
        json.dumps(protected, ensure_ascii=False, separators=(",", ":")).encode()
    ).hexdigest()
    if protected_hash != expected["protected_hash"]:
        raise ValueError("protected Experiment C review packet hash changed")
    return results


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("workbook", type=Path)
    parser.add_argument("--confirm-import", action="store_true")
    args = parser.parse_args()
    results = validate(args.workbook)
    print(f"Validated {len(results)} complete Experiment C human-review rows.")
    if not args.confirm_import:
        print("Validation only; pass --confirm-import to write the canonical artifact.")
        return
    if CANONICAL_REVIEW.exists():
        raise SystemExit(f"refusing to overwrite existing review artifact: {CANONICAL_REVIEW}")
    CANONICAL_REVIEW.write_text(
        json.dumps(
            {
                "schema_version": SCHEMA_VERSION,
                "status": "COMPLETE_HUMAN_REVIEW",
                "reviews": results,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"Imported review artifact: {CANONICAL_REVIEW}")


if __name__ == "__main__":
    main()
