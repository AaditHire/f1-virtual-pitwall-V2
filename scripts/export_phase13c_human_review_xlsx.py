"""Export the bounded Phase 13C human-review workbook."""

from __future__ import annotations

import json

from scripts.phase13c_human_review import (
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
    inspection = inspect_workbook(WORKBOOK_PATH)
    populated = [
        row
        for row in inspection["review_values"][1:]
        if any(value not in (None, "") for value in row)
    ]
    if len(populated) != 25:
        raise RuntimeError("exported workbook does not contain 25 review rows")
    print(result.stdout)
    print(WORKBOOK_PATH.resolve())


if __name__ == "__main__":
    main()
