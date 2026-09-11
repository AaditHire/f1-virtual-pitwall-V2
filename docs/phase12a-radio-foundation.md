# Phase 12A — Radio Intelligence Foundation

## Outcome

Phase 12A establishes a deterministic, historical-only radio metadata source layer. It
normalizes provider-published clip metadata into the existing cached `HistoricalRace`, maps
each clip to the latest causally available leader lap, and exposes cutoff-safe replay feeds.
It does not download audio or provide transcripts, classifications, summaries, or frontend UI.

## Provider inventory

### FastF1 live-timing archive — implemented

The existing FastF1 adapter can retrieve the archived `TeamRadio.jsonStream` alongside the
other race streams. The observed payload consists of:

- the stream envelope time (seconds from the archive origin), used as `available_at`;
- `Captures`, supplied as either a list or keyed object;
- `Utc`, retained as the provider timestamp when valid and timezone-aware;
- `RacingNumber`, joined through the session-specific pre-race roster to the canonical driver ID;
- `Path`, a relative MP3 reference resolved only under the official F1 live-timing host.

There is no transcript, duration, language, speaker identity, semantic label, or independently
verified recording time. Some clips arrive in batches, so neither the MP3 filename nor `Utc` is
used as the causal publication clock. The optional radio stream can be absent without making the
RaceState archive unavailable.

### OpenF1 — inspected, not integrated for current/live radio

[OpenF1's team-radio endpoint](https://openf1.org/docs/#team-radio) exposes `date`,
`driver_number`, `meeting_key`, `recording_url`, and `session_key`. It explicitly describes the
feed as a limited selection rather than a complete radio record. OpenF1 documents free historical
coverage from 2023 onward, authenticated paid access for real-time data, and substantially reduced
F1-provided radio coverage beginning in 2026. No transcript is supplied.

The repository's OpenF1 client has no authenticated streaming capability or radio freshness model.
Adding a current endpoint would therefore risk presenting delayed or missing material as live.
Phase 12A deliberately exposes no current/live radio route. The public documentation inspected did
not publish a numeric unauthenticated rate limit; the existing provider client still applies bounded
retry, 350 ms request pacing, and TTL caching.

## Normalized model

`RadioRecord` contains:

- stable session identity (`f1:{year}:{round}:race`);
- canonical session-roster `driver_id`;
- archive publication time (`available_at`);
- optional provider UTC timestamp;
- optional race elapsed seconds, only after the race-start observation;
- latest causally available leader lap, or `null` before leader lap 1;
- sanitized official MP3 URL;
- source identifier.

`RadioFeed` adds the selected leader lap, its causal cutoff, optional driver filter,
`total_available`, returned records, source, and an explicit mapping definition. Results are
chronological. The API returns the most recent 50 by default and accepts `limit=1..100`.

## Causal mapping rule

For leader lap `N`, the authoritative cutoff remains the first archive publication reporting a
completion of lap `N`. For a radio packet published at archive time `T`:

1. find every leader-lap cutoff less than or equal to `T`;
2. assign the clip to the latest such leader lap;
3. leave it unassigned if no leader-lap completion was available yet.

A Replay feed at lap `N` includes only clips with `available_at <= cutoff(N)`. A clip immediately
before or exactly on the cutoff is visible; one immediately after is not. Provider timestamps and
filenames never override this rule.

## API

- `GET /api/v1/replay/{year}/{round}/{lap}/radio?limit=50`
- `GET /api/v1/replay/{year}/{round}/{lap}/drivers/{driver_id}/radio?limit=50`

Both routes reuse the existing bounded per-event replay cache. Missing radio returns an empty feed;
an invalid lap or session driver remains a 404.

## Real archive audit

Audit run on 2026-09-11 using `scripts/audit_phase12a_radio.py`:

| Event | Messages | Drivers with radio | Before lap 1 | Lap-aligned | Official MP3 refs |
| --- | ---: | ---: | ---: | ---: | ---: |
| 2021 Bahrain | 138 | 20/20 | 20 | 118 | 138/138 |
| 2024 Bahrain | 146 | 20/20 | 17 | 129 | 146/146 |
| 2025 Abu Dhabi | 22 | 6/20 | 2 | 20 | 22/22 |

All 306 records had parseable provider timestamps and official F1 audio references. Sample MP3
metadata requests for Sergio Perez (2021 Bahrain), Yuki Tsunoda (2024 Bahrain), and George Russell
(2025 Abu Dhabi) returned HTTP 200 with `audio/mpeg`; audio content was not downloaded. Driver IDs,
chronological ordering, cutoff assignment, and representative URLs were manually inspected across
multiple drivers.

The recent event demonstrates the main missing-data pattern: radio selection can be sparse and
driver coverage incomplete even when the stream is technically available. Early-session clips are
also common and intentionally have no leader-lap association.

## Performance

With the FastF1 disk cache already populated, first process loads were 4.380 s (2021 Bahrain),
3.351 s (2024 Bahrain), and 5.591 s (2025 Abu Dhabi). Feed filtering after event preparation took
0.130–0.181 ms on the first query and 0.096–0.120 ms on repeated queries. Radio adds one small
optional archive stream to a cold event preparation and no separate replay loader or infrastructure.
An end-to-end ASGI request for 2024 Bahrain lap 25 took 2.297 s with a process-cold/disk-warm event
load; the next per-driver radio request from the shared cache took 2.02 ms. The feed contained 70
causally available records at that cutoff and returned the requested latest five.

## Tests and limitations

Focused tests cover both FastF1 `Captures` shapes, stable roster identity, session identity,
ordering, causal lap association, inclusive/exclusive cutoff boundaries, per-driver filtering,
limits, empty feeds, missing optional streams, malformed timestamps/records, unsafe media paths,
API validation, and future-message mutation/removal leakage.

Known limitations:

- F1 publishes only selected radio clips, not the complete conversation.
- Coverage varies sharply by event, driver, year, and especially from 2026 onward.
- The archive packet time proves availability, not the moment speech occurred.
- Provider `Utc` and filename timestamps are retained/not inferred respectively, but neither is
  treated as a stronger causal clock.
- FastF1's private `_api` topic mapping is isolated in the adapter but remains an upstream schema
  risk.
- Audio URLs are remote references and can later expire or change availability.
- There is no trustworthy current/live availability or delay contract in the present repository.

## Phase 12B recommendation

**Conditional GO for a narrow historical audio/transcription research pilot only.** Historical
coverage, roster association, causal alignment, and accessible audio are sufficient to test whether
speech-to-text is useful on selected well-covered races. This is **not** a GO for live radio
intelligence or complete-grid product claims. Any Phase 12B proposal should begin with a coverage
gate, preserve `available_at` as the causal boundary, report transcription quality by audio sample,
and stop if the sparse recent coverage cannot support the intended product.
