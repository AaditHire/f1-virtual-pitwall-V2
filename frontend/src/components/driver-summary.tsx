import type { DriverAnalysis, DriverRaceState, PitWallDriver } from "@/lib/api/types";
import {
  isOut,
  plainCompound,
  plainDecisionState,
  plainRecommendation,
  plainTraffic,
} from "@/lib/plain";

function lapTime(value: number) {
  const minutes = Math.floor(value / 60);
  return `${minutes}:${(value - minutes * 60).toFixed(3).padStart(6, "0")}`;
}

const secs = (value: number) => `${value.toFixed(1)}s`;

function laps(count: number) {
  return `${count} lap${count === 1 ? "" : "s"}`;
}

function nameOf(id: string | null | undefined, drivers: DriverRaceState[]) {
  return drivers.find((driver) => driver.driver.id === id)?.driver.last_name ?? null;
}

function positionLine(selected: DriverRaceState, drivers: DriverRaceState[]) {
  const name = selected.driver.last_name;
  const position = selected.position;
  if (position == null) return `${name}'s position is not known yet.`;
  if (position === 1) {
    const second = drivers.find((driver) => driver.position === 2);
    const lead = selected.gap_to_behind ?? second?.gap_to_ahead;
    return lead == null ? `${name} leads the race.` : `${name} leads by ${secs(lead)}.`;
  }
  if (selected.laps_behind) return `${name} is P${position}, ${laps(selected.laps_behind)} down.`;
  const ahead = drivers.find((driver) => driver.position === position - 1);
  return selected.gap_to_ahead == null || !ahead
    ? `${name} is P${position}.`
    : `${name} is P${position}, ${secs(selected.gap_to_ahead)} behind ${ahead.driver.last_name}.`;
}

/** A few plain sentences that say what the panels below show, for readers new to the data. */
export function summaryLines(
  selected: DriverRaceState,
  drivers: DriverRaceState[],
  analysis: DriverAnalysis | null,
  strategy: PitWallDriver | null,
): string[] {
  const name = selected.driver.last_name;
  if (isOut(selected.status)) {
    const after = selected.laps_completed ? ` after ${laps(selected.laps_completed)}` : "";
    const what =
      selected.status === "dns"
        ? `${name} did not start the race`
        : selected.status === "dsq"
          ? `${name} was disqualified${after}`
          : `${name} retired from the race${after}`;
    return [`${what}, so there is nothing left to analyse.`];
  }
  const lines = [
    selected.status === "in_pit"
      ? `${name} is in the pit lane.`
      : selected.status === "stopped"
        ? `${name} has stopped on track.`
        : positionLine(selected, drivers),
  ];
  const compound = plainCompound(selected.compound);
  if (compound)
    lines.push(
      selected.tyre_age == null
        ? `On ${compound} tyres.`
        : `On ${compound} tyres that are ${laps(selected.tyre_age)} old.`,
    );
  if (analysis) {
    const pace = analysis.recent_pace.seconds;
    lines.push(
      pace == null
        ? "Not enough clean laps yet to judge pace."
        : `Recent laps are around ${lapTime(pace)}.`,
    );
    const loss = analysis.pit_loss.total_seconds;
    if (loss == null) {
      lines.push("Too few pit stops so far to say what a stop would cost.");
    } else {
      const range = analysis.rejoin.position_range;
      const where = range
        ? range[0] === range[1]
          ? ` and would rejoin in P${range[0]}`
          : ` and would rejoin between P${range[0]} and P${range[1]}`
        : "";
      const behind = where ? nameOf(analysis.rejoin.ahead_id, drivers) : null;
      lines.push(
        `A pit stop now costs about ${secs(loss)}${where}${behind ? `, behind ${behind}` : ""}.`,
      );
    }
    const traffic = analysis.traffic;
    const ahead = nameOf(traffic.ahead_id, drivers);
    if (traffic.status === "CLEAR_AIR") lines.push("Running in clean air.");
    else if (traffic.status !== "UNKNOWN")
      lines.push(
        ahead && traffic.gap_ahead != null
          ? `${plainTraffic(traffic.status)}: ${secs(traffic.gap_ahead)} behind ${ahead}.`
          : `${plainTraffic(traffic.status)} around the car.`,
      );
  }
  if (strategy) {
    lines.push(
      strategy.recommendation
        ? `Strategy model: ${plainRecommendation(strategy.recommendation).toLowerCase()} (${plainDecisionState(strategy.decision_state).toLowerCase()}).`
        : "Strategy model: not enough data to make a call yet.",
    );
  }
  return lines;
}

interface Props {
  selected: DriverRaceState;
  drivers: DriverRaceState[];
  analysis: DriverAnalysis | null;
  strategy: PitWallDriver | null;
}

export function DriverSummary({ selected, drivers, analysis, strategy }: Props) {
  return (
    <section className="driver-summary" aria-label="At a glance">
      <h3>At a glance</h3>
      <ul>
        {summaryLines(selected, drivers, analysis, strategy).map((line) => (
          <li key={line}>{line}</li>
        ))}
      </ul>
    </section>
  );
}
