import type { AnalysisConfidence, DriverAnalysis, DriverRaceState } from "@/lib/api/types";
import { driverCode, fmtNumber } from "@/lib/format";

function formatLapTime(value?: number | null) {
  if (value == null) return "Unavailable";
  const minutes = Math.floor(value / 60);
  return `${minutes}:${(value - minutes * 60).toFixed(3).padStart(6, "0")}`;
}

function formatSeconds(value?: number | null) {
  return value == null ? "Unavailable" : `${value.toFixed(2)}s`;
}

function displayState(value: string) {
  return value.replaceAll("_", " ");
}

export function ConfidenceBadge({ value }: { value: AnalysisConfidence }) {
  return <span className={`analysis-confidence confidence-${value.toLowerCase()}`}>{value === "INSUFFICIENT" ? "INSUFFICIENT DATA" : value}</span>;
}

export function EvidenceLabel({ confidence, observed = false }: { confidence?: AnalysisConfidence; observed?: boolean }) {
  return <span className="analysis-evidence"><b>{observed ? "OBSERVED" : "ESTIMATED"}</b>{confidence ? <ConfidenceBadge value={confidence}/> : null}</span>;
}

function driverLabel(id: string | null | undefined, byId: Map<string, DriverRaceState>) {
  if (!id) return "Unavailable";
  const driver = byId.get(id);
  return driver ? `${driverCode(driver.driver)} · ${driver.driver.last_name}` : id.toUpperCase();
}

function rejoinPosition(analysis: DriverAnalysis) {
  const { position_range: range, projected_position: position } = analysis.rejoin;
  if (range) return range[0] === range[1] ? `P${range[0]}` : `P${range[0]}–P${range[1]}`;
  return position == null ? "Unavailable" : `P${position}`;
}

function BooleanState({ value }: { value?: boolean | null }) {
  return <>{value == null ? "Unavailable" : value ? "Yes" : "No"}</>;
}

interface Props {
  analysis: DriverAnalysis;
  drivers: DriverRaceState[];
  outdated?: boolean;
}

export function DriverEngineeringAnalysis({ analysis, drivers, outdated = false }: Props) {
  const byId = new Map(drivers.map((driver) => [driver.driver.id, driver]));
  return <>
    {outdated ? <p className="analysis-note analysis-warning" role="status">Previous engineering snapshot retained while current driver detail is unavailable.</p> : null}
    <section aria-label="Pace analysis">
      <div className="analysis-heading"><h3>Pace</h3><EvidenceLabel confidence={analysis.recent_pace.confidence}/></div>
      <div className="analysis-primary"><span>Recent clean pace</span><strong>{formatLapTime(analysis.recent_pace.seconds)}</strong></div>
      <dl className="analysis-rows"><div><dt>Usable samples</dt><dd>{analysis.recent_pace.sample_count}</dd></div><div><dt>Observed laps used</dt><dd>{analysis.recent_pace.lap_numbers.length ? analysis.recent_pace.lap_numbers.join(", ") : "None"}</dd></div></dl>
      {analysis.recent_pace.confidence === "INSUFFICIENT" ? <p className="analysis-note">Insufficient clean laps are available at this cutoff.</p> : null}
    </section>

    <section aria-label="Tyre and stint analysis">
      <div className="analysis-heading"><h3>Tyres / stint</h3><EvidenceLabel confidence={analysis.tyres.confidence}/></div>
      <dl className="analysis-rows"><div><dt>Compound</dt><dd>{analysis.tyres.compound ?? "Unavailable"}</dd></div><div><dt>Current stint</dt><dd>{analysis.tyres.stint_number ?? "Unavailable"}</dd></div><div><dt>Tyre age</dt><dd>{analysis.tyres.tyre_age == null ? "Unavailable" : `${analysis.tyres.tyre_age} laps`}</dd></div><div><dt>Clean references</dt><dd>{analysis.tyres.sample_count}</dd></div></dl>
      <p className="analysis-note">Diagnostic evidence only · no tyre-life or degradation forecast.</p>
    </section>

    <section className="analysis-pit" aria-label="Pit and rejoin analysis">
      <div className="analysis-heading"><h3>Pit / rejoin</h3><EvidenceLabel confidence={analysis.rejoin.confidence}/></div>
      <div className="analysis-pit-grid"><div><span>Estimated pit loss</span><strong>{formatSeconds(analysis.pit_loss.total_seconds)}</strong><small>{analysis.pit_loss.sample_count} completed stop sample{analysis.pit_loss.sample_count === 1 ? "" : "s"} · {analysis.pit_loss.confidence}</small></div><div><span>Estimated rejoin</span><strong>{rejoinPosition(analysis)}</strong><small>{displayState(analysis.rejoin.traffic)} · range is an estimate</small></div></div>
      <dl className="analysis-rows"><div><dt>Car ahead on rejoin</dt><dd>{driverLabel(analysis.rejoin.ahead_id, byId)} {analysis.rejoin.gap_ahead == null ? "" : `· ${fmtNumber(analysis.rejoin.gap_ahead, "s")}`}</dd></div><div><dt>Car behind on rejoin</dt><dd>{driverLabel(analysis.rejoin.behind_id, byId)} {analysis.rejoin.gap_behind == null ? "" : `· ${fmtNumber(analysis.rejoin.gap_behind, "s")}`}</dd></div></dl>
    </section>

    <section aria-label="Traffic analysis">
      <div className="analysis-heading"><h3>Traffic</h3><EvidenceLabel confidence={analysis.traffic.confidence}/></div>
      <div className={`analysis-traffic traffic-${analysis.traffic.status.toLowerCase()}`}><strong>{displayState(analysis.traffic.status)}</strong><span>{analysis.traffic.clear_air === true ? "Clean air" : analysis.traffic.clear_air === false ? "Cars within backend traffic window" : "Clean-air state unavailable"}</span></div>
      <dl className="analysis-rows"><div><dt>Ahead</dt><dd>{driverLabel(analysis.traffic.ahead_id, byId)} {analysis.traffic.gap_ahead == null ? "" : `· ${fmtNumber(analysis.traffic.gap_ahead, "s")}`}</dd></div><div><dt>Behind</dt><dd>{driverLabel(analysis.traffic.behind_id, byId)} {analysis.traffic.gap_behind == null ? "" : `· ${fmtNumber(analysis.traffic.gap_behind, "s")}`}</dd></div><div><dt>Slower-car blockage</dt><dd><BooleanState value={analysis.traffic.slower_car_blockage}/></dd></div></dl>
    </section>
  </>;
}
