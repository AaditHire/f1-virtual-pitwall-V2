"""Generate Phase 12C metrics only after all human-owned labels are complete."""

import argparse
import json
from pathlib import Path

from scripts.phase12c_benchmark import (
    FrozenManifest,
    IncompleteAnnotations,
    ReferenceDataset,
    SemanticReviewDataset,
    atomic_write,
    evaluate_dataset,
)


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--manifest", type=Path, default=Path("docs/phase12c-radio-annotation-manifest.json")
    )
    parser.add_argument(
        "--references", type=Path, default=Path("docs/phase12c-radio-human-references.json")
    )
    parser.add_argument(
        "--semantic", type=Path, default=Path("docs/phase12c-radio-semantic-review.json")
    )
    parser.add_argument("--predictions", type=Path, default=Path("docs/phase12b-radio-asr.json"))
    parser.add_argument("--output", type=Path, default=Path("docs/phase12c-radio-evaluation.json"))
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    manifest = FrozenManifest.model_validate_json(args.manifest.read_text(encoding="utf-8"))
    references = ReferenceDataset.model_validate_json(args.references.read_text(encoding="utf-8"))
    semantic = SemanticReviewDataset.model_validate_json(args.semantic.read_text(encoding="utf-8"))
    predictions = json.loads(args.predictions.read_text(encoding="utf-8"))
    try:
        evaluation = evaluate_dataset(manifest, references, semantic, predictions)
    except IncompleteAnnotations as exc:
        print(f"Evaluation blocked: {exc}")
        return 2
    atomic_write(args.output, evaluation)
    print(f"Evaluation written to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
