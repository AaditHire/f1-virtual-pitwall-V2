"""Build the Phase 12C external-reference artifact from metadata-only web research.

The one-time public-web research was performed through Codex browser search because the
repository has no stable search provider/API. This script deliberately parses no ASR artifact;
it hashes that immutable file only for regression provenance.
"""

from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

from scripts.phase12c_benchmark import FrozenManifest, atomic_write
from scripts.phase12c_recovery import (
    EXPECTED_SELECTION_HASH,
    ExternalReferenceDataset,
    ExternalReferenceRecord,
    MatchEvidence,
    PromotionDataset,
    RecoveryReviewDataset,
    SearchAttempt,
    file_sha256,
    validate_against_manifest,
)

MANIFEST_PATH = Path("docs/phase12c-radio-annotation-manifest.json")
PREDICTION_PATH = Path("docs/phase12b-radio-asr.json")
OUTPUT_PATH = Path("docs/phase12c-radio-external-references.json")
REVIEWS_PATH = Path("docs/phase12c-radio-recovery-reviews.json")
PROMOTIONS_PATH = Path("docs/phase12c-radio-reference-promotions.json")
RESEARCHED_AT = datetime.fromisoformat("2026-09-11T15:30:00+00:00")

RACEFANS_2025 = (
    "https://www.racefans.net/2025/12/08/"
    "norris-vs-verstappen-vs-piastri-full-radio-transcript-from-their-championship-showdown/"
)
OFFICIAL = {
    2021: "https://www.formula1.com/en/latest/article/exclusive-listen-to-the-best-team-radio-moments-from-the-2021-bahrain-grand.4207IFvuYONd9CIrHFUJDX.4207IFvuYONd9CIrHFUJDX",
    2024: "https://www.formula1.com/en/video/say-what-bahrain-grand-prix-the-best-team-radio.1792526106683763518",
    2025: "https://www.formula1.com/en/latest/article/what-the-teams-said-race-day-in-abu-dhabi-2025.53LuGvgWY8FgAcxWXLZyN9",
}
DRIVERS = {
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

# These four excerpts were selected only by event/driver/lap/source context. They were
# never compared with ASR text. Each remains VERIFIED_LIKELY because RaceFans does not
# expose provider-message timestamps and the exact MP3 association cannot be proven.
LIKELY = {
    "a10902306b8f99f9c26a": (
        "Okay, we are now within Piastri’s Safety Car windows, Max.",
        "Lap 33/58; one Max-column message appears at the causal lap.",
        "Same event, race session, driver and lap, but no source timestamp "
        "links the quote to the archived MP3.",
    ),
    "270e9276bb1642362d98": (
        "Careful into the box, it’s bumpy.",
        "Lap 42/58; Piastri pit sequence contains several messages.",
        "Strong temporal context but multiple Piastri-channel messages make "
        "full-clip identity and coverage uncertain.",
    ),
    "58873bfeacf456d1c406": (
        "And Leclerc starting to struggle with graining.",
        "Lap 54/58; one Norris-column message appears at the causal lap.",
        "Unique lap-table entry is promising, but the article provides no "
        "provider timestamp or audio identifier.",
    ),
    "dbb3be6dae4048ad9e94": (
        "Final lap.",
        "Lap 58/58; source also contains a separate chequered-flag exchange.",
        "Same driver and lap, but the provider clip could correspond to the "
        "final-lap or subsequent flag exchange.",
    ),
}


def recording_timestamp(audio_url: str) -> str:
    stem = Path(urlparse(audio_url).path).stem
    return "_".join(stem.rsplit("_", 2)[-2:])


def query_for(clip, driver: str, source: str) -> str:
    lap = f" lap {clip.leader_lap}" if clip.leader_lap else ""
    return f"{source} {clip.year} {clip.event} {driver}{lap} team radio transcript"


def searches_for(clip, driver: str, likely: bool) -> list[SearchAttempt]:
    metadata = ["season", "event", "session", "driver", "causal leader lap", "recording timestamp"]
    racefans_url = (
        RACEFANS_2025
        if clip.year == 2025 and clip.driver_id in {"f1:LANNOR01", "f1:OSCPIA01", "f1:MAXVER01"}
        else None
    )
    racefans_outcome = "CANDIDATE_FOUND" if likely else "NO_MATCH"
    racefans_notes = (
        "Editorial lap table contains a literal candidate; exact provider-clip "
        "association is unresolved."
        if likely
        else "No complete literal quote could be tied to this exact archived clip."
    )
    return [
        SearchAttempt(
            source_family="RACEFANS",
            query=query_for(clip, driver, "site:racefans.net"),
            metadata_used=metadata,
            outcome=racefans_outcome,
            candidate_url=racefans_url if likely else None,
            notes=racefans_notes,
        ),
        SearchAttempt(
            source_family="OFFICIAL_F1",
            query=query_for(clip, driver, "site:formula1.com"),
            metadata_used=metadata,
            outcome="NO_MATCH",
            candidate_url=OFFICIAL[clip.year],
            notes=(
                "Official event material was found, but it did not publish a "
                "complete human-written literal transcript for this clip."
            ),
        ),
        SearchAttempt(
            source_family="OTHER_REPUTABLE",
            query=query_for(clip, driver, "reputable motorsport source"),
            metadata_used=metadata,
            outcome="DISALLOWED" if clip.year == 2025 else "NO_MATCH",
            candidate_url=(
                "https://www.formuladream.app/f1-interactive/tools/team-radio/2025/abu-dhabi-grand-prix/race"
                if clip.year == 2025
                else None
            ),
            notes=(
                "A matching archive explicitly described its transcripts as "
                "speech-recognition output; rejected."
                if clip.year == 2025
                else "No reputable human-editorial literal transcript was tied to the exact clip."
            ),
        ),
    ]


def build_record(clip) -> ExternalReferenceRecord:
    driver = DRIVERS[clip.driver_id]
    candidate = LIKELY.get(clip.clip_id)
    common = dict(
        clip_id=clip.clip_id,
        season=clip.year,
        event=clip.event,
        session=clip.session_id,
        driver_id=clip.driver_id,
        driver_display_name=driver,
        causal_leader_lap=clip.leader_lap,
        provider_timestamp=clip.available_at,
        recording_timestamp=recording_timestamp(clip.audio_url),
        audio_identifier=Path(urlparse(clip.audio_url).path).stem,
        searches=searches_for(clip, driver, candidate is not None),
    )
    if candidate:
        transcript, context, reason = candidate
        return ExternalReferenceRecord(
            **common,
            source_name="RaceFans",
            source_url=RACEFANS_2025,
            source_publication_date="2025-12-08",
            source_retrieved_at=RESEARCHED_AT,
            source_authorship="HUMAN_EDITORIAL",
            quote_kind="LITERAL",
            recovered_transcript=transcript,
            source_lap_context=context,
            clip_coverage="UNKNOWN",
            verification_status="VERIFIED_LIKELY",
            matching_evidence=MatchEvidence(
                event_match=True,
                session_match=True,
                driver_match=True,
                temporal_match=True,
                unique_message_match=False,
                evidence=["same event", "race session", "same driver", "same causal/near lap"],
            ),
            verification_reason=reason,
            confidence_notes="Requires listening to the original frozen audio before acceptance.",
            review_status="PENDING",
        )
    return ExternalReferenceRecord(
        **common,
        verification_status="NO_REFERENCE",
        matching_evidence=MatchEvidence(
            event_match=False,
            session_match=False,
            driver_match=False,
            temporal_match=False,
            unique_message_match=False,
            evidence=[],
        ),
        verification_reason=(
            "Targeted metadata-only searches found no complete human-written "
            "transcript that could be associated with the exact archived clip."
        ),
        confidence_notes="Manual blind listening/transcription remains authoritative.",
        review_status="NEEDS_MANUAL_TRANSCRIPTION",
    )


def main() -> None:
    manifest = FrozenManifest.model_validate_json(MANIFEST_PATH.read_text(encoding="utf-8"))
    if manifest.selection_hash != EXPECTED_SELECTION_HASH:
        raise RuntimeError("refusing recovery: frozen selection hash changed")
    dataset = ExternalReferenceDataset(
        selection_hash=manifest.selection_hash,
        manifest_sha256_before_recovery=file_sha256(MANIFEST_PATH),
        phase12b_predictions_sha256_before_recovery=file_sha256(PREDICTION_PATH),
        research_method=(
            "One-time targeted Codex web search using frozen metadata only; "
            "repository workflow performs deterministic validation/import and "
            "never parses ASR predictions."
        ),
        researched_at=RESEARCHED_AT,
        records=[build_record(clip) for clip in manifest.clips],
    )
    validate_against_manifest(dataset, manifest, MANIFEST_PATH)
    atomic_write(OUTPUT_PATH, dataset)
    if not REVIEWS_PATH.exists():
        atomic_write(REVIEWS_PATH, RecoveryReviewDataset(selection_hash=manifest.selection_hash))
    if not PROMOTIONS_PATH.exists():
        atomic_write(PROMOTIONS_PATH, PromotionDataset(selection_hash=manifest.selection_hash))
    counts = {
        status: sum(record.verification_status == status for record in dataset.records)
        for status in ("VERIFIED_EXACT", "VERIFIED_LIKELY", "NO_REFERENCE", "REFERENCE_CONFLICT")
    }
    print(f"Researched {len(dataset.records)} frozen clips: {counts}")


if __name__ == "__main__":
    main()
