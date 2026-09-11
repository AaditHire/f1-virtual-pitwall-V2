"""Explicitly promote one approved external human reference into the canonical dataset."""

import argparse
from pathlib import Path

from scripts.phase12c_benchmark import FrozenManifest, ReferenceDataset, atomic_write
from scripts.phase12c_recovery import (
    PromotionDataset,
    RecoveryReviewDataset,
    load_external_references,
    promote_reference,
    validate_against_manifest,
)


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("clip_id")
    parser.add_argument(
        "--confirm", action="store_true", help="required explicit promotion consent"
    )
    parser.add_argument("--usability", choices=["CLEAR", "PARTIAL", "POOR", "UNUSABLE"])
    parser.add_argument("--speaker-type", choices=["DRIVER", "ENGINEER", "MIXED", "UNKNOWN"])
    parser.add_argument("--override-existing", action="store_true")
    parser.add_argument(
        "--manifest", type=Path, default=Path("docs/phase12c-radio-annotation-manifest.json")
    )
    parser.add_argument(
        "--external", type=Path, default=Path("docs/phase12c-radio-external-references.json")
    )
    parser.add_argument(
        "--reviews", type=Path, default=Path("docs/phase12c-radio-recovery-reviews.json")
    )
    parser.add_argument(
        "--references", type=Path, default=Path("docs/phase12c-radio-human-references.json")
    )
    parser.add_argument(
        "--provenance", type=Path, default=Path("docs/phase12c-radio-reference-promotions.json")
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    manifest = FrozenManifest.model_validate_json(args.manifest.read_text(encoding="utf-8"))
    external = load_external_references(args.external)
    validate_against_manifest(external, manifest, args.manifest)
    reviews = RecoveryReviewDataset.model_validate_json(args.reviews.read_text(encoding="utf-8"))
    references = ReferenceDataset.model_validate_json(args.references.read_text(encoding="utf-8"))
    provenance = PromotionDataset.model_validate_json(args.provenance.read_text(encoding="utf-8"))
    promoted = promote_reference(
        manifest=manifest,
        references=references,
        external=external,
        reviews=reviews,
        provenance=provenance,
        clip_id=args.clip_id,
        explicit_confirmation=args.confirm,
        usability=args.usability,
        speaker_type=args.speaker_type,
        override_existing=args.override_existing,
    )
    atomic_write(args.references, references)
    atomic_write(args.provenance, provenance)
    print(f"Promoted {promoted.clip_id} from {promoted.source_name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
