# Phase 6D focused PIT opportunity signal

## Method

Phase 6D compared three simple, pit-cycle-independent formulations on a small development
sample. The sample contains N-3, N-2, and N-1 states before selected factual green stops and
controls matched by driver, grid region, and nearest tyre age where no stop occurred within
three laps. Team stops are behavioral labels, not optimal-strategy ground truth.

The six development races were 2023 Bahrain, Barcelona, Monza, and Singapore plus 2024 Bahrain
and Barcelona. Up to eight stops per race were selected with round-robin grid-region coverage.
This produced 135 near-pit and 122 control states. Every production snapshot used only data
available at its causal cutoff; +3/+5 observations were evaluation labels.

## Frozen candidates

All deltas use PIT minus EXTEND, so negative values favor PIT. The five-lap time equivalence band
is the existing 2.482 seconds and the physical-position equivalence is one place.

1. `TIME_ONLY`: normalized value is `-median_time_delta / 2.482`. STRONG requires time delta at
   least one equivalence band in PIT's favor and PIT-better frequency at least 0.70. MARGINAL
   requires a negative time delta and frequency at least 0.55.
2. `TIME_POSITION`: adds `-median_physical_position_delta / 1`. Either dimension can provide its
   explicit STRONG or MARGINAL classification.
3. `FULL_EVIDENCE`: starts with the two normalized values and subtracts the exposed pit downside
   tail, paired-interval width, rejoin-traffic, and weak-applicability penalties. STRONG requires
   strong time or physical-position evidence and a final value of at least 1; MARGINAL requires
   positive evidence and a value above zero.

Pit-cycle position is present in every result only as `pit_cycle_position_delta_diagnostic`, with
`pit_cycle_used_in_signal: false`. There are no learned or hidden weights.

## Development result

| Candidate | Near-pit STRONG | Control STRONG | Separation |
|---|---:|---:|---:|
| TIME_ONLY | 0 / 135 (0.00%) | 0 / 122 (0.00%) | 0.00 pp |
| TIME_POSITION | 2 / 135 (1.48%) | 5 / 122 (4.10%) | -2.62 pp |
| FULL_EVIDENCE | 0 / 135 (0.00%) | 0 / 122 (0.00%) | 0.00 pp |

No candidate produced MARGINAL states. Among states with a paired time value, median PIT-minus-
EXTEND time was +17.401 seconds near stops and +16.577 seconds in controls. None of the 166 timed
states favored PIT. Median physical-position delta was +4 places near stops and +3 in controls;
only two near-pit and five control states favored PIT.

The seven TIME_POSITION signals were isolated. Their median observed evolution was +1 physical
place and +4.411 seconds at +3, then +1 place and +5.264 seconds at +5. Five were non-pit
controls. The two near-stop signals occurred only at N-1 and did not persist.

Grid-region TIME_POSITION STRONG rates were 3.13% near versus 16.67% control in P1-P5, 0% versus
0% in P6-P10, 3.23% versus 0% in P11-P15, and 0% versus 0% in P16+. TIME_ONLY and FULL_EVIDENCE
were zero in every grid region.

## Interpretation

The short horizon pays the complete pit loss but rarely observes enough fresh-tyre running to
recover it. Near factual stops therefore look substantially worse than staying out in both paired
time and simulated physical position. Singapore also frequently lacks a paired time curve.
Traffic and uncertainty cannot responsibly turn this adverse evidence into an opportunity.

The Phase 6D failure rule applies: no simple candidate separates near-stop opportunities from
ordinary continuation states. Candidate development stopped without adding heuristics. The
late-2024 and selected-2025 validation races were not evaluated and remain untouched.
The preserved set is 2024 Monza and Singapore plus 2025 Bahrain, Barcelona, Monza, and
Singapore.

## Integration decision

No candidate was selected and no PIT opportunity signal was wired into the Pit Wall. Existing
Phase 6C recommendation behavior, post-stop cooldown, equivalence bands, and thresholds remain
unchanged. Phase 7 remains NO-GO because the present one-to-five-lap inputs cannot support an
independent PIT_NOW opportunity signal.
