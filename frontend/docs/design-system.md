# Virtual Pit Wall design system

Phase 8B uses one premium motorsport system in two modes. Home, Weekend, News, and Standings are editorial and image-led. Live Pit Wall is denser and engineering-focused, but retains the same typography, rules, angular geometry, and semantic colors.

## Foundations

- Display: Barlow Condensed, weights 500–700
- Interface: IBM Plex Sans, weights 400–500
- Telemetry and time: IBM Plex Mono, weight 400
- Base canvas: `#080b0e`
- Primary surface: `#0c1216`
- Elevated data surface: `#111a20`
- Rules: `#26323a`; strong rules: `#46545d`
- Text: `#f5f7f8`; muted text: `#9daab3`
- Accent/selection: `#f04444`
- Healthy/live: `#51dd84`
- Warning/experimental: `#ffc32e`
- Wet compound: `#57a2ef`

The desktop grid uses fluid 20–38 px gutters and open horizontal bands. Corners remain square or use 2–4 px radii. Shadows are avoided; hierarchy comes from scale, spacing, rules, and background depth.

## Component language

- Navigation uses a slim sticky graphite bar, an original text-based VPW mark, uppercase condensed labels, and a red underline for the active route.
- Event features use asymmetric image crops, a single red diagonal, oversized round numerals, and a code-native circuit-line motif.
- Session timelines are continuous broadcast rails, not card collections. Sprint sessions use the same dynamic component.
- News uses a lead-story image and compact secondary rows. Story headlines, sources, timestamps, and links always come from the backend.
- Standings use open tables with restrained separators. Mobile shows a concise leading subset while the full Standings route remains available.
- Live timing rows emphasize selection with a red rail and position block. Numeric timing stays monospace; driver and status labels use the display face.
- Tyre compounds always include a letter. Soft is red, Medium yellow, Hard white, Intermediate green, and Wet blue.
- Strategy recommendations always show `EXPERIMENTAL`. Decision quality remains text-first: `ACTIONABLE`, `CAUTION`, `COARSE ONLY`, or `INSUFFICIENT DATA`.
- PIT and EXTEND outcomes use paired, equal-width comparison rails and report only values supplied by the backend.

## States and behavior

Loading, empty, error, partial, stale, offline, and live states retain explicit text and semantic color. Controls expose visible keyboard focus. Mobile Pit Wall provides Overview, Timing, Strategy, and Feed views without removing any live data. The session rail is the only intentional horizontal scrolling region on the Home page. Motion is limited to loading feedback and respects `prefers-reduced-motion`.

## Historical replay

Historical replay uses the engineering mode rather than the editorial card language. The archive header always identifies the selected event, leader lap, and the historical-data boundary with an explicit `NO FUTURE DATA` label. Season, event, step, direct-lap, and scrubber controls occupy one continuous instrument rail.

The replay workspace pairs a complete dynamic timing field with a selected-driver state surface. Adjacent-lap requests retain the current causal snapshot under a compact loading notice; changing race clears the previous field to avoid presenting it under the wrong event. On narrow screens the surfaces stack, while the timing table remains deliberately horizontally scrollable so no backend field or participant is removed.
