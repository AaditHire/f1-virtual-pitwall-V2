"""Export the frozen Phase 13D Experiment A human-review workbook."""

from __future__ import annotations

import json

from scripts.phase13d_experiment_a_human_review import (
    EXPECTED_ROWS,
    MANIFEST_PATH,
    OUTPUT_DIR,
    WORKBOOK_PATH,
    build_payload,
    inspect_workbook,
    run_artifact,
)


def main() -> None:
    payload = build_payload()
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    MANIFEST_PATH.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    preview_dir = OUTPUT_DIR / "preview"
    result = run_artifact("build", MANIFEST_PATH, WORKBOOK_PATH, preview_dir)
    inspected = inspect_workbook(WORKBOOK_PATH)
    rows = [
        row
        for row in inspected["review_values"][1:]
        if any(value not in (None, "") for value in row)
    ]
    if len(rows) != EXPECTED_ROWS:
        raise RuntimeError(f"exported workbook contains {len(rows)} review rows")
    if any(any(value not in (None, "") for value in row[10:14]) for row in rows):
        raise RuntimeError("exported workbook unexpectedly contains human verdicts")
    print(result.stdout)
    print(WORKBOOK_PATH.resolve())


if __name__ == "__main__":
    main()
