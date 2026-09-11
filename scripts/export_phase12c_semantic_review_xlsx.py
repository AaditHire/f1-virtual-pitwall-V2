"""Export the Phase 12C post-reference semantic review workbook."""

from __future__ import annotations

import argparse
from pathlib import Path

from scripts.phase12c_semantic_xlsx import DEFAULT_OUTPUT_DIR, create_semantic_review_workbook


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--overwrite-empty-workbook", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    result = create_semantic_review_workbook(
        args.output_dir, overwrite_empty_workbook=args.overwrite_empty_workbook
    )
    print(f"Workbook: {result['workbook_path']}")
    print(f"Workbook SHA-256: {result['workbook_sha256']}")


if __name__ == "__main__":
    main()
