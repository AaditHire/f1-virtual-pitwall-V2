"""Export the frozen Phase 12C clips as a blind XLSX annotation pack."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import httpx

from scripts.phase12c_xlsx import (
    DEFAULT_PACK_DIR,
    MANIFEST_PATH,
    PACK_MANIFEST_NAME,
    WORKBOOK_NAME,
    audio_filename,
    export_payload,
    file_sha256,
    inspect_workbook,
    load_frozen_manifest,
    repair_hyperlink_formula_cache,
    run_artifact_tool,
    validate_audio_url,
    validate_workbook_data,
)

MAX_AUDIO_BYTES = 5_000_000


def download_audio_pack(manifest, output_dir: Path) -> tuple[list[dict], list[dict]]:
    audio_dir = output_dir / "audio"
    audio_dir.mkdir(parents=True, exist_ok=True)
    completed: list[dict] = []
    failures: list[dict] = []
    with httpx.Client(follow_redirects=True, timeout=45) as client:
        for clip in manifest.clips:
            filename = audio_filename(clip)
            target = audio_dir / filename
            try:
                validate_audio_url(clip)
                response = client.get(clip.audio_url)
                response.raise_for_status()
                content_type = response.headers.get("content-type", "").split(";", 1)[0].lower()
                if not content_type.startswith("audio/") or not response.content:
                    raise ValueError(f"unexpected content type: {content_type or 'missing'}")
                if len(response.content) > MAX_AUDIO_BYTES:
                    raise ValueError("audio exceeds bounded annotation-pack limit")
                temporary = target.with_suffix(".mp3.tmp")
                temporary.write_bytes(response.content)
                temporary.replace(target)
                completed.append(
                    {
                        "sequence": clip.sequence,
                        "clip_id": clip.clip_id,
                        "filename": f"audio/{filename}",
                        "source_url": clip.audio_url,
                        "sha256": file_sha256(target),
                        "bytes": target.stat().st_size,
                    }
                )
            except (httpx.HTTPError, OSError, ValueError) as exc:
                failures.append(
                    {
                        "sequence": clip.sequence,
                        "clip_id": clip.clip_id,
                        "filename": f"audio/{filename}",
                        "source_url": clip.audio_url,
                        "error": f"{type(exc).__name__}: {exc}",
                    }
                )
    return completed, failures


def create_annotation_pack(
    output_dir: Path = DEFAULT_PACK_DIR,
    *,
    manifest_path: Path = MANIFEST_PATH,
    download_audio: bool = True,
    overwrite_empty_workbook: bool = False,
) -> dict:
    manifest = load_frozen_manifest(manifest_path)
    output_dir = output_dir.resolve()
    workbook_path = output_dir / WORKBOOK_NAME
    if workbook_path.exists() and not overwrite_empty_workbook:
        raise FileExistsError(
            f"refusing to overwrite an existing annotation workbook: {workbook_path}"
        )
    output_dir.mkdir(parents=True, exist_ok=True)
    completed, failures = download_audio_pack(manifest, output_dir) if download_audio else ([], [])
    payload = export_payload(manifest)
    payload_path = Path(".cache/phase12c-xlsx-runtime/export-payload.json").resolve()
    payload_path.parent.mkdir(parents=True, exist_ok=True)
    payload_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    preview_dir = Path(".cache/phase12c-xlsx-previews").resolve()
    build = run_artifact_tool("build", payload_path, workbook_path, preview_dir)
    repair_hyperlink_formula_cache(workbook_path)
    run_artifact_tool("render", workbook_path, preview_dir)
    inspected = inspect_workbook(workbook_path)
    report = validate_workbook_data(inspected, manifest)
    if report.completed or len(report.incomplete) != 30 or report.invalid:
        raise RuntimeError("exported workbook did not preserve 30 blank annotation rows")
    pack_manifest = {
        "schema_version": "1.0",
        "selection_hash": manifest.selection_hash,
        "frozen_manifest_sha256": file_sha256(manifest_path),
        "workbook": WORKBOOK_NAME,
        "workbook_sha256": file_sha256(workbook_path),
        "audio_downloaded": completed,
        "audio_failures": failures,
    }
    (output_dir / PACK_MANIFEST_NAME).write_text(
        json.dumps(pack_manifest, indent=2), encoding="utf-8"
    )
    return {
        "workbook_path": str(workbook_path),
        "audio_dir": str(output_dir / "audio"),
        "downloaded": len(completed),
        "failures": failures,
        "builder_output": build.stdout,
        "workbook_sha256": pack_manifest["workbook_sha256"],
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_PACK_DIR)
    parser.add_argument("--manifest", type=Path, default=MANIFEST_PATH)
    parser.add_argument("--skip-audio", action="store_true")
    parser.add_argument(
        "--overwrite-empty-workbook",
        action="store_true",
        help="explicitly replace an existing workbook with a new blank workbook",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    result = create_annotation_pack(
        args.output_dir,
        manifest_path=args.manifest,
        download_audio=not args.skip_audio,
        overwrite_empty_workbook=args.overwrite_empty_workbook,
    )
    print(f"Workbook: {result['workbook_path']}")
    print(f"Audio: {result['audio_dir']}")
    print(f"Downloaded: {result['downloaded']}/30")
    print(f"Workbook SHA-256: {result['workbook_sha256']}")
    if result["failures"]:
        for failure in result["failures"]:
            print(f"FAILED {failure['clip_id']}: {failure['error']}")
        raise SystemExit(2)


if __name__ == "__main__":
    main()
