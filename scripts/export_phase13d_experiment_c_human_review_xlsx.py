"""Export the frozen blank review workbook for all successful Experiment C fallbacks."""

from __future__ import annotations

import json

from scripts.phase13d_experiment_c_human_review import (
    MANIFEST,
    OUTPUT_DIR,
    WORKBOOK,
    build_payload,
    inspect_workbook,
    run_artifact,
)


def main() -> None:
    payload = build_payload()
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    if MANIFEST.exists() or WORKBOOK.exists():
        raise FileExistsError("refusing to replace Experiment C human-review artifact")
    MANIFEST.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    preview_dir = OUTPUT_DIR / "preview"
    result = run_artifact("build", MANIFEST, WORKBOOK, preview_dir)
    inspected = inspect_workbook(WORKBOOK)
    rows = [
        row
        for row in inspected["review_values"][1:]
        if any(value not in (None, "") for value in row)
    ]
    if len(rows) != payload["review_rows"]:
        raise RuntimeError("exported Experiment C workbook row count changed")
    if any(any(value not in (None, "") for value in row[10:14]) for row in rows):
        raise RuntimeError("exported workbook unexpectedly contains human verdicts")
    print(result.stdout)
    print(WORKBOOK.resolve())


if __name__ == "__main__":
    main()
