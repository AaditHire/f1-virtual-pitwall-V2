"""Validate and explicitly import a completed Phase 12C annotation workbook."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from scripts.phase12c_xlsx import import_completed_workbook


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("workbook", type=Path)
    parser.add_argument(
        "--confirm-import",
        action="store_true",
        help="write a fully valid 30-row workbook to the protected canonical reference file",
    )
    parser.add_argument(
        "--override-existing",
        action="store_true",
        help="explicitly replace existing canonical references after human review",
    )
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
        print("Validation passed. Re-run with --confirm-import to write canonical references.")
    else:
        print(f"Imported {len(report.imported)} protected human references.")


if __name__ == "__main__":
    main()
