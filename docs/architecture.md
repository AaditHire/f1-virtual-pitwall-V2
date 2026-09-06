# Architecture

```text
Public HTTP/RSS → provider adapters → normalization → Pydantic domain models
                                                   ↓
                                          services → FastAPI routes
```

`providers/` owns external JSON/XML shapes. `ProviderHTTP` supplies bounded TTL caching, timeouts, paced requests, transient GET retries and observable errors. Jolpica pagination counts nested results/standings correctly. RSS caches only normalized metadata, never article bodies. `domain/` contains focused models and session enums; driver identity uses the Jolpica ID, not a car number.

`services/` handles provider-supported seasons, enrichment, current/next decisions, results, standings and news deduplication. `Hub` wires services once per application lifespan and assembles `/home` through them. It identifies the event associated with a fallback grid so a previous race cannot masquerade as the upcoming grid. Failed sections appear in `errors`; news feed failures remain visible in `provider_status`.

`api/` validates paths, limits and IANA timezone names and formats responses. A reusable serializer preserves UTC fields and adds optional local-time companions. FastAPI lifespan owns/closes the shared `httpx.AsyncClient`. Domain timestamps reject naive datetimes and normalize supplied offsets to UTC.

## Temporal semantics

- `active` is Jolpica's `current` season. `latest` is the maximum published supported season; these can differ when next year's calendar is announced.
- Session ordering uses provider timestamps, not a fixed weekend format. Unknown starts stay null; cancelled sessions/events cannot become the next session.
- `current_event` spans the first scheduled start to the provider's race end or published race results. Between sessions it still represents the ongoing weekend.
- `next_event` means the next unfinished event, so it can equal the current event before/during its race. Once finished it advances to the following event.
- `next_session` is the earliest non-cancelled known start strictly after the request clock. Published future seasons are considered, including the preceding year at the New Year boundary.
- If a race has started but neither end nor results are available, status is `end_unknown`. Current-event candidate selection is limited to the provider weekend dates through the following UTC day, so missing results do not leave an old event current indefinitely. This calendar window is not an inferred finish time. Postponements beyond it require updated provider data.
- Date-only future events can be `upcoming`, but no start is invented. `null` next session means no known future start, not necessarily that the season has ended.
- `/home` reports `upcoming`, `in_progress`, `end_unknown`, `schedule_unknown`, `off_season` or `unavailable` as applicable. A status based on a supplied end is schedule-based, not live race-control confirmation.

## Failure behavior

HTTP timeouts/network failures, 408, 429 and selected 5xx responses are retried (two retries by default). Other failures are not. `Retry-After` is respected within ten seconds; longer throttles fail cleanly for a later request. Errors are not cached as successful data. A specific OpenF1 `404 {"detail":"No results found."}` is an empty collection, recorded in `unavailable_resources`; a generic 404 remains a failure.

Status is observed health, not a background probe: `not_checked`, `ok`, or `degraded`, with checked/success timestamps and outstanding resource errors. Cache hits do not refresh observation timestamps. Successful requests clear only the relevant previous error. No countdown loops or background polling jobs.

Cache and request pacing are per process. Multiple workers multiply provider traffic; use one worker initially. No expired cache entries are silently served. `/health` indicates process liveness, not upstream readiness.
