"""Shared safety, mapping, and validation for the Phase 12C XLSX workflow."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse

from scripts.phase12c_benchmark import (
    FrozenClip,
    FrozenManifest,
    ReferenceDataset,
    atomic_write,
    normalize_plain,
    save_reference,
)

EXPECTED_SELECTION_HASH = "111cb5264152d0ba1875363894e9b6c018b4f384cefd75ba03bbca129676c131"
ANNOTATION_SCHEMA_VERSION = "1.0"
MANIFEST_PATH = Path("docs/phase12c-radio-annotation-manifest.json")
REFERENCE_PATH = Path("docs/phase12c-radio-human-references.json")
DEFAULT_PACK_DIR = Path("outputs/phase12c_annotation_pack")
WORKBOOK_NAME = "phase12c_manual_annotations.xlsx"
PACK_MANIFEST_NAME = "phase12c_annotation_pack.json"

HEADERS = [
    "Clip Number",
    "Clip ID",
    "Season",
    "Event",
    "Session",
    "Driver",
    "Driver ID",
    "Leader Lap",
    "Recording Timestamp",
    "Audio",
    "Human Transcript",
    "Usability",
    "Speaker",
    "Critical Terms",
    "Notes",
]
USABILITY_VALUES = {"CLEAR", "PARTIAL", "POOR", "UNUSABLE"}
SPEAKER_VALUES = {"DRIVER", "ENGINEER", "MIXED", "UNKNOWN"}
FORBIDDEN_WORKBOOK_TEXT = {
    "asr",
    "small.en",
    "medium.en",
    "prediction",
    "raw_transcript",
    "confidence",
    "no-speech",
    "average_log_probability",
    "maximum_no_speech_probability",
    "disagreement",
    "model disagreement",
    "suspicious-output",
    "suspicious_flags",
    "racefans",
    "verified_likely",
    "recovered_transcript",
}

DRIVER_NAMES = {
    "f1:DANRIC01": "Daniel Ricciardo",
    "f1:MICSCH02": "Mick Schumacher",
    "f1:VALBOT01": "Valtteri Bottas",
    "f1:CARSAI01": "Carlos Sainz",
    "f1:LANNOR01": "Lando Norris",
    "f1:FERALO01": "Fernando Alonso",
    "f1:MAXVER01": "Max Verstappen",
    "f1:YUKTSU01": "Yuki Tsunoda",
    "f1:ANTGIO01": "Antonio Giovinazzi",
    "f1:SERPER01": "Sergio Perez",
    "f1:PIEGAS01": "Pierre Gasly",
    "f1:OSCPIA01": "Oscar Piastri",
    "f1:GEORUS01": "George Russell",
    "f1:NICHUL01": "Nico Hulkenberg",
    "f1:LEWHAM01": "Lewis Hamilton",
    "f1:ALEALB01": "Alexander Albon",
    "f1:GABBOR01": "Gabriel Bortoleto",
}


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_frozen_manifest(path: Path = MANIFEST_PATH) -> FrozenManifest:
    manifest = FrozenManifest.model_validate_json(path.read_text(encoding="utf-8"))
    if manifest.selection_hash != EXPECTED_SELECTION_HASH:
        raise ValueError("frozen Phase 12C selection hash changed")
    if len(manifest.clips) != 30 or len({clip.clip_id for clip in manifest.clips}) != 30:
        raise ValueError("Phase 12C XLSX export requires exactly 30 unique frozen clips")
    if [clip.sequence for clip in manifest.clips] != list(range(1, 31)):
        raise ValueError("frozen clip ordering or sequence changed")
    return manifest


def audio_filename(clip: FrozenClip) -> str:
    return f"{clip.sequence:03d}_{clip.clip_id}.mp3"


def recording_timestamp(clip: FrozenClip) -> str:
    stem = Path(urlparse(clip.audio_url).path).stem
    parts = stem.rsplit("_", 2)
    if len(parts) != 3 or not all(part.isdigit() for part in parts[-2:]):
        raise ValueError(f"recording timestamp missing from frozen URL: {clip.clip_id}")
    return "_".join(parts[-2:])


def validate_audio_url(clip: FrozenClip) -> None:
    parsed = urlparse(clip.audio_url)
    if (
        parsed.scheme != "https"
        or parsed.hostname != "livetiming.formula1.com"
        or parsed.query
        or parsed.fragment
        or not parsed.path.lower().endswith(".mp3")
        or "/TeamRadio/" not in parsed.path
    ):
        raise ValueError(f"unsafe frozen audio URL for {clip.clip_id}")


def session_label(clip: FrozenClip) -> str:
    value = clip.session_id.rsplit(":", 1)[-1]
    return value.replace("_", " ").title()


def workbook_row(clip: FrozenClip) -> list[object | None]:
    validate_audio_url(clip)
    try:
        driver_name = DRIVER_NAMES[clip.driver_id]
    except KeyError as exc:
        raise ValueError(f"missing independent driver display name: {clip.driver_id}") from exc
    return [
        clip.sequence,
        clip.clip_id,
        clip.year,
        clip.event,
        session_label(clip),
        driver_name,
        clip.driver_id,
        clip.leader_lap,
        recording_timestamp(clip),
        None,
        None,
        None,
        None,
        None,
        None,
    ]


def audio_formula(clip: FrozenClip) -> str:
    return f'=HYPERLINK("audio/{audio_filename(clip)}","Open audio")'


def export_payload(manifest: FrozenManifest) -> dict:
    payload = {
        "schema_version": ANNOTATION_SCHEMA_VERSION,
        "selection_hash": manifest.selection_hash,
        "headers": HEADERS,
        "rows": [workbook_row(clip) for clip in manifest.clips],
        "audio_formulas": [audio_formula(clip) for clip in manifest.clips],
        "usability_values": sorted(USABILITY_VALUES),
        "speaker_values": sorted(SPEAKER_VALUES),
    }
    encoded = json.dumps(payload, ensure_ascii=False).casefold()
    forbidden = [token for token in FORBIDDEN_WORKBOOK_TEXT if token in encoded]
    if forbidden:
        raise ValueError(f"forbidden machine/recovery text in workbook payload: {forbidden}")
    return payload


def artifact_runtime() -> tuple[Path, Path]:
    runtime_root = Path(".cache/phase12c-xlsx-runtime").resolve()
    runtime_root.mkdir(parents=True, exist_ok=True)
    dependency_root = Path(
        os.environ.get(
            "PHASE12C_ARTIFACT_DEPENDENCIES",
            str(Path.home() / ".cache/codex-runtimes/codex-primary-runtime/dependencies"),
        )
    )
    node = dependency_root / "node/bin/node.exe"
    if not node.exists():
        node = dependency_root / "node/bin/node"
    modules = dependency_root / "node/node_modules"
    if not node.exists() or not (modules / "@oai/artifact-tool").exists():
        raise RuntimeError("bundled spreadsheet runtime is unavailable")
    junction = runtime_root / "node_modules"
    if not junction.exists():
        if os.name == "nt":
            subprocess.run(
                ["cmd", "/c", "mklink", "/J", str(junction), str(modules)],
                check=True,
                capture_output=True,
                text=True,
            )
        else:
            junction.symlink_to(modules, target_is_directory=True)
    builder = runtime_root / "phase12c_xlsx_artifact.mjs"
    shutil.copy2(Path(__file__).with_name("phase12c_xlsx_artifact.mjs"), builder)
    return node, builder


def run_artifact_tool(command: str, *arguments: Path) -> subprocess.CompletedProcess[str]:
    node, builder = artifact_runtime()
    return subprocess.run(
        [str(node), str(builder), command, *(str(argument.resolve()) for argument in arguments)],
        check=True,
        capture_output=True,
        text=True,
    )


def repair_hyperlink_formula_cache(workbook_path: Path) -> None:
    """Repair artifact-tool's invalid cached HYPERLINK error cells without changing formulas."""
    sheet_name = "xl/worksheets/sheet1.xml"
    temporary = workbook_path.with_suffix(".xlsx.tmp")
    repaired = 0
    with zipfile.ZipFile(workbook_path, "r") as source, zipfile.ZipFile(temporary, "w") as target:
        for item in source.infolist():
            data = source.read(item.filename)
            if item.filename == sheet_name:
                text = data.decode("utf-8")

                def replace(match: re.Match[str]) -> str:
                    nonlocal repaired
                    repaired += 1
                    return (
                        f'{match.group(1)}t="str"{match.group(2)}'
                        f"{match.group(3)}<x:v>Open audio</x:v>{match.group(4)}"
                    )

                pattern = re.compile(
                    r'(<x:c r="J(?:[2-9]|[12]\d|3[01])" s="\d+" )t="e"([^>]*>)'
                    r"(<x:f>HYPERLINK\([^<]+</x:f>)<x:v>[^<]*</x:v>(</x:c>)"
                )
                text = pattern.sub(replace, text)
                data = text.encode("utf-8")
            target.writestr(item, data)
    if repaired != 30:
        temporary.unlink(missing_ok=True)
        raise RuntimeError(f"expected to repair 30 audio hyperlinks, repaired {repaired}")
    temporary.replace(workbook_path)


def inspect_workbook(workbook_path: Path) -> dict:
    runtime_root = Path(".cache/phase12c-xlsx-runtime").resolve()
    runtime_root.mkdir(parents=True, exist_ok=True)
    inspection_path = runtime_root / "inspection.json"
    run_artifact_tool("inspect", workbook_path, inspection_path)
    return json.loads(inspection_path.read_text(encoding="utf-8"))


def _cell(value: object | None) -> object | None:
    return None if value in (None, "") else value


def _critical_category(term: str) -> str:
    normalized = normalize_plain(term)
    if re.search(r"\b(?:box|pit)\b", normalized):
        return "BOX_PIT"
    if normalized in {"soft", "medium", "hard", "intermediate", "wet"}:
        return "TYRE_COMPOUND"
    if re.search(r"\bsafety car\b", normalized):
        return "SAFETY_CAR"
    if re.search(r"\bvsc\b|virtual safety car", normalized):
        return "VSC"
    if re.search(r"\bstay out\b", normalized):
        return "STAY_OUT"
    if re.search(r"\bdrs\b", normalized):
        return "DRS"
    if re.search(r"\blap\b", normalized):
        return "LAP_NUMBER"
    if re.search(r"\bdelta\b", normalized):
        return "DELTA"
    if re.search(r"\bbrake", normalized):
        return "BRAKE_BALANCE"
    if re.search(r"\bdiff", normalized):
        return "DIFFERENTIAL"
    if re.search(r"\b(?:strat|engine|mode)\b", normalized):
        return "ENGINE_STRAT_MODE"
    if re.search(r"\bwing\b", normalized):
        return "WING_ADJUSTMENT"
    return "OTHER_OPERATIONAL"


def parse_critical_terms(value: object | None, transcript: str) -> list[dict[str, str]]:
    if _cell(value) is None:
        return []
    terms = [part.strip() for part in str(value).split(";") if part.strip()]
    normalized_reference = normalize_plain(transcript)
    result = []
    for term in terms:
        if normalize_plain(term) not in normalized_reference:
            raise ValueError(f"critical term is not present in human transcript: {term}")
        result.append({"text": term, "category": _critical_category(term)})
    return result


@dataclass
class WorkbookImportReport:
    completed: list[str] = field(default_factory=list)
    incomplete: list[str] = field(default_factory=list)
    invalid: dict[str, list[str]] = field(default_factory=dict)
    rows: dict[str, dict] = field(default_factory=dict)
    imported: list[str] = field(default_factory=list)

    @property
    def valid_for_import(self) -> bool:
        return len(self.completed) == 30 and not self.incomplete and not self.invalid

    def add_invalid(self, clip_id: str, message: str) -> None:
        self.invalid.setdefault(clip_id, []).append(message)

    def as_dict(self) -> dict:
        return {
            "completed_count": len(self.completed),
            "incomplete_count": len(self.incomplete),
            "invalid_count": len(self.invalid),
            "completed": self.completed,
            "incomplete": self.incomplete,
            "invalid": self.invalid,
            "imported": self.imported,
            "valid_for_import": self.valid_for_import,
        }


def validate_workbook_data(data: dict, manifest: FrozenManifest) -> WorkbookImportReport:
    report = WorkbookImportReport()
    values = data.get("annotations_values")
    formulas = data.get("annotations_formulas")
    instructions = data.get("instructions_values")
    if not isinstance(values, list) or len(values) < 32 or values[0] != HEADERS:
        raise ValueError("Annotations sheet/schema is missing or malformed")
    if not isinstance(formulas, list) or len(formulas) < 32:
        raise ValueError("Annotations formula map is missing")
    if not isinstance(instructions, list) or len(instructions) < 16:
        raise ValueError("Instructions sheet/schema is missing or malformed")
    if _cell(instructions[13][1]) != len(manifest.clips):
        raise ValueError("workbook frozen dataset size does not match manifest")
    if _cell(instructions[14][1]) != manifest.selection_hash:
        raise ValueError("workbook frozen selection hash does not match manifest")
    if _cell(instructions[15][1]) != ANNOTATION_SCHEMA_VERSION:
        raise ValueError("workbook annotation schema version is unsupported")
    if any(_cell(value) is not None for value in values[31]):
        raise ValueError("Annotations sheet contains rows beyond the 30 frozen clips")

    observed_ids = [str(values[index][1] or "") for index in range(1, 31)]
    if len(set(observed_ids)) != len(observed_ids):
        raise ValueError("duplicate Clip IDs in workbook")
    expected_ids = [clip.clip_id for clip in manifest.clips]
    unknown = sorted(set(observed_ids) - set(expected_ids))
    if unknown:
        raise ValueError(f"unknown Clip IDs in workbook: {unknown}")
    if observed_ids != expected_ids:
        raise ValueError("workbook Clip IDs do not preserve frozen order")

    for row_number, clip in enumerate(manifest.clips, 1):
        row = values[row_number]
        expected = workbook_row(clip)
        for column in range(9):
            if _cell(row[column]) != _cell(expected[column]):
                raise ValueError(
                    f"frozen metadata changed for {clip.clip_id} in column {HEADERS[column]}"
                )
        formula = formulas[row_number][9] if len(formulas[row_number]) > 9 else None
        if formula != audio_formula(clip):
            raise ValueError(f"audio hyperlink does not match frozen clip {clip.clip_id}")

        transcript = str(row[10] or "").strip()
        usability = str(row[11] or "").strip().upper()
        speaker = str(row[12] or "").strip().upper()
        critical_terms = row[13]
        notes = str(row[14] or "").strip()
        if usability and usability not in USABILITY_VALUES:
            report.add_invalid(clip.clip_id, f"invalid usability: {usability}")
        if speaker and speaker not in SPEAKER_VALUES:
            report.add_invalid(clip.clip_id, f"invalid speaker: {speaker}")
        parsed_terms = []
        if transcript:
            try:
                parsed_terms = parse_critical_terms(critical_terms, transcript)
            except ValueError as exc:
                report.add_invalid(clip.clip_id, str(exc))
        elif _cell(critical_terms) is not None:
            report.add_invalid(clip.clip_id, "critical terms require a human transcript")

        if not transcript or not usability or not speaker:
            report.incomplete.append(clip.clip_id)
            continue
        if clip.clip_id in report.invalid:
            continue
        report.completed.append(clip.clip_id)
        report.rows[clip.clip_id] = {
            "reference_transcript": transcript,
            "usability": usability,
            "speaker_type": speaker,
            "critical_terms": parsed_terms,
            "notes": notes,
            "complete": True,
        }
    return report


def import_completed_workbook(
    workbook_path: Path,
    *,
    manifest_path: Path = MANIFEST_PATH,
    reference_path: Path = REFERENCE_PATH,
    confirm_import: bool = False,
    override_existing: bool = False,
) -> WorkbookImportReport:
    manifest = load_frozen_manifest(manifest_path)
    data = inspect_workbook(workbook_path)
    report = validate_workbook_data(data, manifest)
    if not report.valid_for_import or not confirm_import:
        return report
    references = ReferenceDataset.model_validate_json(reference_path.read_text(encoding="utf-8"))
    if references.selection_hash != manifest.selection_hash:
        raise ValueError("canonical reference selection hash differs from frozen manifest")
    existing = sorted(set(report.completed) & set(references.annotations))
    if existing and not override_existing:
        raise ValueError(f"refusing silent canonical overwrite for Clip IDs: {existing}")
    candidate = references.model_copy(deep=True)
    for clip in manifest.clips:
        save_reference(candidate, manifest, clip.clip_id, report.rows[clip.clip_id])
    atomic_write(reference_path, candidate)
    report.imported = report.completed.copy()
    return report
