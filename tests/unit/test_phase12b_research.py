from types import SimpleNamespace

import httpx

from f1_pitwall.domain.replay import RadioRecord
from scripts.research_phase12b import (
    AudioFetchError,
    SelectedClip,
    TemporaryAudioCache,
    TranscriptResult,
    normalize_transcript,
    select_event_clips,
    suspicious_flags,
    token_disagreement,
    transcribe_one,
)


def records():
    return [
        RadioRecord(
            session_id="f1:2024:1:race",
            driver_id=f"driver-{index % 4}",
            available_at=100 + index * 10,
            leader_lap=index or None,
            audio_url=f"https://livetiming.formula1.com/static/race/TeamRadio/{index}.mp3",
        )
        for index in range(12)
    ]


def clip():
    return SelectedClip(
        record_id="stable-record",
        year=2024,
        round=1,
        event="Bahrain Grand Prix",
        session_id="f1:2024:1:race",
        driver_id="f1:TEST01",
        available_at=123.4,
        leader_lap=7,
        audio_url="https://livetiming.formula1.com/static/race/TeamRadio/test.mp3",
    )


def test_clip_selection_is_deterministic_spread_and_retains_causal_metadata():
    first = select_event_clips(2024, 1, "Bahrain Grand Prix", records(), 6)
    second = select_event_clips(2024, 1, "Bahrain Grand Prix", list(reversed(records())), 6)
    assert first == second
    assert len(first) == 6 and len({row.driver_id for row in first}) == 4
    assert first[0].available_at == 100 and first[-1].available_at == 210
    assert first[1].session_id == "f1:2024:1:race" and first[1].leader_lap is not None


def test_temporary_audio_cache_validates_content_and_reuses_download():
    requests = 0

    def handler(request):
        nonlocal requests
        requests += 1
        return httpx.Response(200, headers={"content-type": "audio/mpeg"}, content=b"audio")

    with (
        TemporaryAudioCache() as cache,
        httpx.Client(transport=httpx.MockTransport(handler)) as client,
    ):
        first = cache.fetch(client, clip())
        assert first.read_bytes() == b"audio"
        assert cache.fetch(client, clip()) == first
        directory = first.parent
    assert requests == 1 and not directory.exists()


def test_audio_fetch_failure_and_non_audio_are_explicit():
    for response in (
        httpx.Response(503),
        httpx.Response(200, headers={"content-type": "text/html"}, content=b"no"),
        httpx.Response(200, headers={"content-type": "audio/mpeg"}, content=b""),
    ):
        with (
            TemporaryAudioCache() as cache,
            httpx.Client(
                transport=httpx.MockTransport(lambda request, value=response: value)
            ) as client,
        ):
            try:
                cache.fetch(client, clip())
            except AudioFetchError:
                pass
            else:
                raise AssertionError("invalid audio response was accepted")


def test_transcript_preserves_raw_text_and_serializes_research_metadata():
    segment = SimpleNamespace(text="  Box, box.  ", avg_logprob=-0.2, no_speech_prob=0.1)
    info = SimpleNamespace(duration=2.5, language="en", language_probability=1.0)
    model = SimpleNamespace(transcribe=lambda *args, **kwargs: ([segment], info))
    result = transcribe_one(model, clip(), "unused.mp3", "small.en", "cpu", "int8")
    assert result.status == "success"
    assert result.raw_transcript == "  Box, box.  "
    assert result.normalized_transcript == "Box, box."
    assert result.session_id == "f1:2024:1:race" and result.leader_lap == 7
    assert TranscriptResult.model_validate_json(result.model_dump_json()) == result


def test_asr_failure_is_isolated_and_keeps_radio_linkage():
    def fail(*args, **kwargs):
        raise RuntimeError("decoder failed")

    result = transcribe_one(
        SimpleNamespace(transcribe=fail), clip(), "unused.mp3", "small.en", "cpu", "int8"
    )
    assert result.status == "asr_error" and "decoder failed" in result.failure_reason
    assert result.record_id == "stable-record" and result.available_at == 123.4


def test_diagnostic_rules_do_not_claim_accuracy():
    assert normalize_transcript(" a  b\n") == "a b"
    assert "repetition" in suspicious_flags(
        "box box box box box box box box box", 10, "en", 1, -0.2, 0.1
    )
    assert "low_confidence" in suspicious_flags("pit", 2, "en", 1, -1.2, 0.8)
    assert token_disagreement("box this lap", "stay out") > 0.25
