# Data sources and priority

| Category | Priority and behavior |
| --- | --- |
| Seasons, participants, constructors, calendar identity | Jolpica authoritative; all pages fetched, supported years discovered from `/seasons` |
| Session starts/ends and cancellation | OpenF1 when safely matched to Jolpica; retain Jolpica if unavailable or ambiguous |
| Qualifying, final race classification, standings | Jolpica; preserve optional missing values and supplied classifications/points |
| Completed-event grid | Jolpica race-result `grid`, the published actual starting slot including penalties |
| Upcoming grid | OpenF1 `starting_grid`, race key then qualifying key when needed; never derive from qualifying results |
| News | BBC Sport F1 + Autosport F1 RSS, merged by publication time and deduplicated |

References: [Jolpica](https://github.com/jolpica/jolpica-f1/blob/main/docs/README.md), [OpenF1](https://openf1.org/docs/), [BBC RSS](https://feeds.bbci.co.uk/sport/formula1/rss.xml), [Autosport RSS](https://www.autosport.com/rss/f1/news/).

## Matching and disagreements

Match events using a race date within one day AND normalized circuit name, circuit ID or locality (case, punctuation and diacritics ignored; substring comparison accepts common short forms). Exactly one candidate is required. Ambiguous/different identities retain Jolpica and produce an event warning plus a structured log. Different starts produce warnings and use OpenF1's explicit session time. No race-name equality requirement or hand-maintained circuit mapping.

OpenF1 numbers join only to that session's driver profiles; profiles must uniquely match a Jolpica full name or code before a canonical ID is assigned. Unmatched identities raise an observable provider error. Season lists may include reserves, substitutes and practice participants; their count is not the race-grid count. Team affiliations live on results and standings, rather than assuming one team per driver for the year.

## Availability and limitations

- Jolpica supplies historical 2021+ data. OpenF1's older coverage is limited; its explicit no-results response leaves Jolpica schedules intact.
- Live verification found OpenF1 starting grids attached to qualifying keys, including historical and current weekends. Race keys can validly return no records. The adapter checks both; it never substitutes a qualifying classification.
- Race results retain DNS/DNF/DSQ descriptions, position text, laps and optional times. Qualifying drivers with no Q time are retained. No points system or grid size is hard-coded.
- Grid zero can mean pit lane or non-starter upstream. Preserve zero and return `pit_lane: null`; do not infer the distinction. Missing grids return an empty list. Upcoming OpenF1 entries can lack constructor information.
- OpenF1 ends can be scheduled rather than actual. This phase is not a race-control monitor. Cancellations/changes appear after provider refresh and TTL expiry.
- Participant lists, result publication and session times depend on provider availability. Discrepancy warnings are not proof of which provider reflects an official late change.
- RSS stores only headline/source/URL/publication time, a supplied short summary (at most 600 characters), supplied image URL and supplied categories. HTML is removed; full content fields are ignored. No pages are scraped. Missing publication times remain null. Optional query matching covers title/summary; there is no entity extraction or claim that every headline is a major story.
- Deduplication removes tracking parameters/fragments, then compares canonical URLs and normalized title similarity (0.94 threshold). Different reporting on one topic can remain; similar titles can occasionally merge.

FastF1 is unnecessary for Phase 1 and is not installed. Telemetry and detailed historical session analysis remain out of scope.
