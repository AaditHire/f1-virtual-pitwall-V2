"""Bounded local-ASR experiment over causally aligned Phase 12A radio records."""

import argparse
import asyncio
import csv
import hashlib
import json
import re
import shutil
import statistics
import subprocess
import tempfile
import time
from collections import Counter, defaultdict
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Literal

import httpx
from pydantic import BaseModel, Field

from f1_pitwall.core.config import Settings
from f1_pitwall.domain.replay import RadioRecord
from f1_pitwall.services.hub import Hub

EVENTS = ((2021, 1), (2024, 1), (2025, 24))
VOCABULARY = (
    "box",
    "pit",
    "soft",
    "medium",
    "hard",
    "tyre",
    "tire",
    "delta",
    "brake balance",
    "differential",
    "deployment",
    "strat",
    "safety car",
    "virtual safety car",
    "undercut",
    "overcut",
    "front wing",
    "temperature",
)


class SelectedClip(BaseModel):
    record_id: str
    year: int
    round: int
    event: str
    session_id: str
    driver_id: str
    available_at: float
    leader_lap: int | None = None
    audio_url: str


class TranscriptResult(BaseModel):
    record_id: str
    year: int
    round: int
    event: str
    session_id: str
    driver_id: str
    available_at: float
    leader_lap: int | None = None
    audio_url: str
    audio_duration_seconds: float | None = None
    model_id: str
    library: str
    library_version: str
    device: str
    compute_type: str
    language_setting: str | None = None
    detected_language: str | None = None
    language_probability: float | None = None
    beam_size: int
    vad_filter: bool
    temperature: float
    condition_on_previous_text: bool
    status: Literal["success", "empty", "fetch_error", "asr_error"]
    failure_reason: str | None = None
    raw_transcript: str = ""
    normalized_transcript: str = ""
    segment_count: int = 0
    average_log_probability: float | None = None
    maximum_no_speech_probability: float | None = None
    processing_seconds: float | None = None
    rss_mb_after: float | None = None
    suspicious_flags: list[str] = Field(default_factory=list)


class AudioFetchError(RuntimeError):
    pass


def record_id(record: RadioRecord) -> str:
    value = f"{record.session_id}|{record.driver_id}|{record.available_at:.3f}|{record.audio_url}"
    return hashlib.sha256(value.encode()).hexdigest()[:20]


def select_event_clips(
    year: int, round_number: int, event: str, records: list[RadioRecord], count: int
) -> list[SelectedClip]:
    """Select a deterministic, time-spread set while preferring new drivers."""
    ordered = sorted(records, key=lambda row: (row.available_at, row.driver_id, row.audio_url))
    if not ordered or count <= 0:
        return []
    count = min(count, len(ordered))
    targets = [round(index * (len(ordered) - 1) / max(1, count - 1)) for index in range(count)]
    unused = set(range(len(ordered)))
    selected, used_drivers = [], set()
    for target in targets:
        candidates = sorted(unused, key=lambda index: (abs(index - target), index))
        novel = [index for index in candidates if ordered[index].driver_id not in used_drivers]
        chosen = (novel or candidates)[0]
        unused.remove(chosen)
        row = ordered[chosen]
        used_drivers.add(row.driver_id)
        selected.append(
            SelectedClip(
                record_id=record_id(row),
                year=year,
                round=round_number,
                event=event,
                session_id=row.session_id,
                driver_id=row.driver_id,
                available_at=row.available_at,
                leader_lap=row.leader_lap,
                audio_url=row.audio_url,
            )
        )
    return sorted(selected, key=lambda clip: (clip.year, clip.round, clip.available_at))


class TemporaryAudioCache:
    def __init__(self, root: Path | None = None, max_bytes: int = 5_000_000):
        self.parent = root
        self.max_bytes = max_bytes
        self._temporary: tempfile.TemporaryDirectory | None = None
        self.path: Path | None = None

    def __enter__(self):
        parent = self.parent or Path(".cache")
        parent.mkdir(parents=True, exist_ok=True)
        self._temporary = tempfile.TemporaryDirectory(prefix="phase12b-", dir=parent)
        self.path = Path(self._temporary.name)
        return self

    def __exit__(self, exc_type, exc, traceback):
        if self._temporary:
            self._temporary.cleanup()
        self.path = None

    def fetch(self, client: httpx.Client, clip: SelectedClip) -> Path:
        if self.path is None:
            raise RuntimeError("audio cache is not active")
        target = self.path / f"{clip.record_id}.mp3"
        if target.exists():
            return target
        try:
            with client.stream("GET", clip.audio_url) as response:
                response.raise_for_status()
                content_type = response.headers.get("content-type", "").split(";", 1)[0].lower()
                if not content_type.startswith("audio/"):
                    raise AudioFetchError(f"unexpected content type: {content_type or 'missing'}")
                length = response.headers.get("content-length")
                if length and int(length) > self.max_bytes:
                    raise AudioFetchError("audio response exceeds per-clip limit")
                data = bytearray()
                for chunk in response.iter_bytes():
                    data.extend(chunk)
                    if len(data) > self.max_bytes:
                        raise AudioFetchError("audio response exceeds per-clip limit")
        except (httpx.HTTPError, ValueError) as exc:
            raise AudioFetchError(type(exc).__name__) from exc
        if not data:
            raise AudioFetchError("empty audio response")
        target.write_bytes(data)
        return target


def normalize_transcript(value: str) -> str:
    return " ".join(value.split())


def repeated_phrase(words: list[str]) -> bool:
    if len(words) < 9:
        return False
    phrases = Counter(tuple(words[index : index + 3]) for index in range(len(words) - 2))
    return max(phrases.values(), default=0) >= 3


def suspicious_flags(
    text: str,
    duration: float | None,
    language: str | None,
    language_probability: float | None,
    average_log_probability: float | None,
    no_speech_probability: float | None,
) -> list[str]:
    words = re.findall(r"[a-z0-9']+", text.casefold())
    flags = []
    if not words:
        flags.append("empty")
    if repeated_phrase(words):
        flags.append("repetition")
    if duration and (len(words) / duration > 5 or (duration < 3 and len(words) > 15)):
        flags.append("too_many_words_for_duration")
    if language and language != "en" and (language_probability or 0) >= 0.6:
        flags.append("language_mismatch")
    if (average_log_probability is not None and average_log_probability < -1) or (
        no_speech_probability is not None and no_speech_probability > 0.6
    ):
        flags.append("low_confidence")
    return flags


def percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round((len(ordered) - 1) * fraction)))
    return ordered[index]


def package_version(name: str) -> str:
    try:
        return version(name)
    except PackageNotFoundError:
        return "unavailable"


def model_revision(model_id: str) -> str | None:
    try:
        from huggingface_hub import try_to_load_from_cache

        cached = try_to_load_from_cache(f"Systran/faster-whisper-{model_id}", "config.json")
        if isinstance(cached, str):
            return Path(cached).parent.name
    except ImportError:
        pass
    return None


def token_disagreement(left: str, right: str) -> float:
    a = normalize_transcript(left).casefold().split()
    b = normalize_transcript(right).casefold().split()
    if not a and not b:
        return 0
    previous = list(range(len(b) + 1))
    for index, word in enumerate(a, 1):
        current = [index]
        for other_index, other in enumerate(b, 1):
            current.append(
                min(
                    current[-1] + 1,
                    previous[other_index] + 1,
                    previous[other_index - 1] + (word != other),
                )
            )
        previous = current
    return previous[-1] / max(len(a), len(b), 1)


def gpu_memory_used_mb() -> int | None:
    executable = shutil.which("nvidia-smi")
    if not executable:
        return None
    try:
        output = subprocess.run(
            [executable, "--query-gpu=memory.used", "--format=csv,noheader,nounits"],
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        ).stdout.splitlines()[0]
        return int(output.strip())
    except (OSError, ValueError, subprocess.SubprocessError, IndexError):
        return None


def result_for_failure(
    clip: SelectedClip,
    model_id: str,
    status: Literal["fetch_error", "asr_error"],
    reason: str,
    device: str,
    compute_type: str,
    vad_filter: bool = False,
) -> TranscriptResult:
    return TranscriptResult(
        **clip.model_dump(),
        model_id=model_id,
        library="faster-whisper",
        library_version=package_version("faster-whisper"),
        device=device,
        compute_type=compute_type,
        language_setting="en",
        beam_size=5,
        vad_filter=vad_filter,
        temperature=0,
        condition_on_previous_text=False,
        status=status,
        failure_reason=reason[:240],
    )


def transcribe_one(model, clip, path, model_id, device, compute_type, vad_filter=False):
    started = time.perf_counter()
    try:
        segments, info = model.transcribe(
            str(path),
            language="en",
            beam_size=5,
            temperature=0,
            vad_filter=vad_filter,
            condition_on_previous_text=False,
            word_timestamps=False,
        )
        rows = list(segments)
        raw = "".join(segment.text for segment in rows)
        normalized = normalize_transcript(raw)
        log_probabilities = [segment.avg_logprob for segment in rows]
        no_speech = [segment.no_speech_prob for segment in rows]
        average_log_probability = statistics.fmean(log_probabilities) if log_probabilities else None
        maximum_no_speech_probability = max(no_speech, default=None)
        duration = float(info.duration) if info.duration is not None else None
        flags = suspicious_flags(
            normalized,
            duration,
            info.language,
            info.language_probability,
            average_log_probability,
            maximum_no_speech_probability,
        )
        return TranscriptResult(
            **clip.model_dump(),
            audio_duration_seconds=duration,
            model_id=model_id,
            library="faster-whisper",
            library_version=package_version("faster-whisper"),
            device=device,
            compute_type=compute_type,
            language_setting="en",
            detected_language=info.language,
            language_probability=info.language_probability,
            beam_size=5,
            vad_filter=vad_filter,
            temperature=0,
            condition_on_previous_text=False,
            status="success" if normalized else "empty",
            raw_transcript=raw,
            normalized_transcript=normalized,
            segment_count=len(rows),
            average_log_probability=average_log_probability,
            maximum_no_speech_probability=maximum_no_speech_probability,
            processing_seconds=time.perf_counter() - started,
            rss_mb_after=process_rss_mb(),
            suspicious_flags=flags,
        )
    except Exception as exc:
        result = result_for_failure(
            clip,
            model_id,
            "asr_error",
            f"{type(exc).__name__}: {exc}",
            device,
            compute_type,
            vad_filter,
        )
        result.processing_seconds = time.perf_counter() - started
        return result


def load_model(model_id: str, device: str, compute_type: str):
    from faster_whisper import WhisperModel

    return WhisperModel(model_id, device=device, compute_type=compute_type)


def process_rss_mb() -> float | None:
    try:
        import psutil

        return psutil.Process().memory_info().rss / 1024**2
    except ImportError:
        return None


def run_model(model_id, clips, audio_paths, device, compute_type, vad_subset=0):
    before_gpu = gpu_memory_used_mb()
    model = load_model(model_id, device, compute_type)
    after_load_gpu = gpu_memory_used_mb()
    gpu_samples = [value for value in (before_gpu, after_load_gpu) if value is not None]
    results, completed = [], set()
    first = next((clip for clip in clips if clip.record_id in audio_paths), None)
    if first is not None:
        preflight = transcribe_one(
            model, first, audio_paths[first.record_id], model_id, device, compute_type
        )
        if device == "cuda" and preflight.status == "asr_error":
            raise RuntimeError(preflight.failure_reason)
        results.append(preflight)
        completed.add(first.record_id)
        memory = gpu_memory_used_mb()
        if memory is not None:
            gpu_samples.append(memory)
    for clip in clips:
        if clip.record_id in completed:
            continue
        if clip.record_id not in audio_paths:
            results.append(
                result_for_failure(
                    clip,
                    model_id,
                    "fetch_error",
                    "audio fetch failed",
                    device,
                    compute_type,
                )
            )
            continue
        results.append(
            transcribe_one(model, clip, audio_paths[clip.record_id], model_id, device, compute_type)
        )
        memory = gpu_memory_used_mb()
        if memory is not None:
            gpu_samples.append(memory)
    vad_results = []
    if vad_subset and model_id == "small.en":
        for clip in clips[:vad_subset]:
            if clip.record_id in audio_paths:
                vad_results.append(
                    transcribe_one(
                        model,
                        clip,
                        audio_paths[clip.record_id],
                        model_id,
                        device,
                        compute_type,
                        vad_filter=True,
                    )
                )
    return (
        results,
        vad_results,
        {
            "gpu_used_mb_before": before_gpu,
            "gpu_used_mb_after_load": after_load_gpu,
            "peak_observed_total_gpu_used_mb": max(gpu_samples, default=None),
        },
    )


async def collect_clips(per_event: int) -> list[SelectedClip]:
    settings = Settings.from_env()
    async with httpx.AsyncClient(
        follow_redirects=True,
        headers={"User-Agent": "F1Pitwall/0.1 (Phase 12B research)"},
    ) as client:
        hub = Hub(client, settings)
        selected = []
        for year, round_number in EVENTS:
            race = await hub.replay.load_race(year, round_number)
            selected.extend(
                select_event_clips(year, round_number, race.event.name, race.radio, per_event)
            )
        return selected


def summarize_model(rows: list[TranscriptResult]) -> dict:
    successful = [row for row in rows if row.status in {"success", "empty"}]
    latencies = [row.processing_seconds for row in successful if row.processing_seconds is not None]
    return {
        "model_id": rows[0].model_id if rows else None,
        "clips": len(rows),
        "decode_success_rate": len(successful) / len(rows) if rows else 0,
        "empty_rate": sum(row.status == "empty" for row in rows) / len(rows) if rows else 0,
        "low_confidence_rate": sum("low_confidence" in row.suspicious_flags for row in rows)
        / len(rows)
        if rows
        else 0,
        "suspicious_output_rate": sum(bool(row.suspicious_flags) for row in rows) / len(rows)
        if rows
        else 0,
        "median_processing_seconds": statistics.median(latencies) if latencies else None,
        "p90_processing_seconds": percentile(latencies, 0.9),
        "total_processing_seconds": sum(latencies),
        "device": rows[0].device if rows else None,
        "compute_type": rows[0].compute_type if rows else None,
        "max_rss_mb_after": max(
            (row.rss_mb_after for row in rows if row.rss_mb_after is not None), default=None
        ),
    }


def comparison_summary(results: list[TranscriptResult], models: list[str]) -> dict:
    grouped = defaultdict(dict)
    for row in results:
        if not row.vad_filter:
            grouped[row.record_id][row.model_id] = row
    comparisons, vocabulary = [], Counter()
    for record, values in grouped.items():
        if not all(model in values for model in models):
            continue
        left, right = values[models[0]], values[models[1]]
        disagreement = token_disagreement(left.raw_transcript, right.raw_transcript)
        for term in VOCABULARY:
            present = [term in values[model].normalized_transcript.casefold() for model in models]
            if len(set(present)) > 1:
                vocabulary[term] += 1
        comparisons.append(
            {
                "record_id": record,
                "token_disagreement": disagreement,
                "meaningful_disagreement": disagreement > 0.25,
            }
        )
    return {
        "comparable_clips": len(comparisons),
        "meaningful_disagreement_rate": (
            sum(row["meaningful_disagreement"] for row in comparisons) / len(comparisons)
            if comparisons
            else None
        ),
        "median_token_disagreement": statistics.median(
            row["token_disagreement"] for row in comparisons
        )
        if comparisons
        else None,
        "vocabulary_presence_disagreements": dict(vocabulary),
        "clips": comparisons,
        "warning": "Cross-model agreement is diagnostic and is not transcription accuracy.",
    }


def write_artifacts(path: Path, csv_path: Path, payload: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    rows = payload["results"]
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        fields = list(rows[0]) if rows else []
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {
                    **row,
                    "suspicious_flags": "|".join(row["suspicious_flags"]),
                }
            )


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--per-event", type=int, default=10)
    parser.add_argument("--models", nargs="+", default=["small.en", "medium.en"])
    parser.add_argument("--device", choices=["auto", "cuda", "cpu"], default="auto")
    parser.add_argument("--vad-subset", type=int, default=6)
    parser.add_argument("--output", type=Path, default=Path("docs/phase12b-radio-asr.json"))
    parser.add_argument("--csv", type=Path, default=Path("docs/phase12b-radio-asr-review.csv"))
    return parser.parse_args()


def main():
    args = parse_args()
    clips = asyncio.run(collect_clips(args.per_event))
    import ctranslate2

    device = args.device
    if device == "auto":
        device = "cuda" if ctranslate2.get_cuda_device_count() else "cpu"
    compute_type = "float16" if device == "cuda" else "int8"
    audio_paths, fetch_failures = {}, {}
    results, vad_results, memory = [], [], {}
    with (
        TemporaryAudioCache() as cache,
        httpx.Client(
            follow_redirects=True,
            timeout=30,
            headers={"User-Agent": "F1Pitwall/0.1 (Phase 12B research)"},
        ) as client,
    ):
        for clip in clips:
            try:
                audio_paths[clip.record_id] = cache.fetch(client, clip)
            except AudioFetchError as exc:
                fetch_failures[clip.record_id] = str(exc)
        for model_id in args.models:
            try:
                model_rows, model_vad, model_memory = run_model(
                    model_id,
                    clips,
                    audio_paths,
                    device,
                    compute_type,
                    args.vad_subset,
                )
            except Exception as exc:
                if device != "cuda":
                    raise
                device, compute_type = "cpu", "int8"
                model_rows, model_vad, model_memory = run_model(
                    model_id,
                    clips,
                    audio_paths,
                    device,
                    compute_type,
                    args.vad_subset,
                )
                model_memory["cuda_fallback_reason"] = f"{type(exc).__name__}: {exc}"
            results.extend(model_rows)
            vad_results.extend(model_vad)
            memory[model_id] = model_memory
    for row in results:
        if row.status == "fetch_error":
            row.failure_reason = fetch_failures.get(row.record_id, row.failure_reason)
    model_summaries = [
        summarize_model([row for row in results if row.model_id == model]) for model in args.models
    ]
    vad_pairs = []
    baseline = {row.record_id: row for row in results if row.model_id == "small.en"}
    for row in vad_results:
        if row.record_id in baseline:
            vad_pairs.append(
                {
                    "record_id": row.record_id,
                    "off_status": baseline[row.record_id].status,
                    "on_status": row.status,
                    "token_disagreement": token_disagreement(
                        baseline[row.record_id].raw_transcript, row.raw_transcript
                    ),
                }
            )
    durations = {
        row.record_id: row.audio_duration_seconds
        for row in results
        if row.audio_duration_seconds is not None
    }
    payload = {
        "experiment": {
            "phase": "12B",
            "created_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "models": args.models,
            "model_revisions": {model: model_revision(model) for model in args.models},
            "library": "faster-whisper",
            "library_version": package_version("faster-whisper"),
            "ctranslate2_version": package_version("ctranslate2"),
            "beam_size": 5,
            "language": "en",
            "temperature": 0,
            "condition_on_previous_text": False,
            "cpu_threads": "CTranslate2 auto",
            "workers": 1,
            "vad_default": False,
            "selection": "time-spread deterministic selection preferring unused drivers",
            "ground_truth_transcripts": False,
            "wer_cer_valid": False,
        },
        "sample": {
            "clips": len(clips),
            "events": dict(Counter(f"{clip.year}-{clip.round}" for clip in clips)),
            "drivers": len({clip.driver_id for clip in clips}),
            "fetched": len(audio_paths),
            "fetch_failures": fetch_failures,
            "total_duration_seconds": sum(durations.values()),
        },
        "model_summaries": model_summaries,
        "comparison": comparison_summary(results, args.models[:2]),
        "vad_comparison": {
            "clips": vad_pairs,
            "warning": "VAD comparison is diagnostic and has no human reference transcripts.",
        },
        "memory": memory,
        "results": [row.model_dump(mode="json") for row in results + vad_results],
    }
    write_artifacts(args.output, args.csv, payload)
    print(json.dumps({key: value for key, value in payload.items() if key != "results"}, indent=2))


if __name__ == "__main__":
    main()
