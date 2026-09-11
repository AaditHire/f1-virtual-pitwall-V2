"""Validate and explicitly import all 60 Phase 12C semantic reviews."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from scripts.phase12c_semantic_xlsx import import_completed_workbook


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("workbook", type=Path)
    parser.add_argument("--confirm-import", action="store_true")
    parser.add_argument("--override-existing", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    report = import_completed_workbook(
        args.workbook,
        confirm_import=args.confirm_import,
        override_existing=args.override_existing,
    )
    print(json.dumps(report.as_dict(), indent=2))
    if not report.valid_for_import:
        raise SystemExit(2)
    if not args.confirm_import:
        print("Validation passed. Re-run with --confirm-import to write semantic reviews.")
    else:
        print(f"Imported {len(report.imported)} protected semantic reviews.")


if __name__ == "__main__":
    main()
