import type {
  AnalysisConfidence,
  AnalysisEvidence,
  DriverAnalysis,
  DriverRaceState,
} from "@/lib/api/types";
import { driverCode, fmtNumber } from "@/lib/format";
import { plainCompound, plainConfidence, plainTraffic } from "@/lib/plain";

const MISSING = "—";

function formatLapTime(value?: number | null) {
  if (value == null) return MISSING;
  const minutes = Math.floor(value / 60);
  return `${minutes}:${(value - minutes * 60).toFixed(3).padStart(6, "0")}`;
}

function formatSeconds(value?: number | null) {
  return value == null ? MISSING : `${value.toFixed(1)}s`;
}

export function ConfidenceBadge({ value }: { value: AnalysisConfidence }) {
  return (
    <span className={`analysis-confidence confidence-${value.toLowerCase()}`}>
      {plainConfidence(value)}
    </span>
  );
}

export function EvidenceLabel({
  confidence,
  observed = false,
}: {
  confidence?: AnalysisConfidence;
  observed?: boolean;
}) {
  return (
    <span className="analysis-evidence">
      <b>{observed ? "Measured" : "Estimate"}</b>
      {confidence ? <ConfidenceBadge value={confidence} /> : null}
    </span>
  );
}

/** Why an estimate is missing, in the backend's own words when it gives one. */
function MissingReason({ evidence, fallback }: { evidence: AnalysisEvidence; fallback: string }) {
  if (evidence.confidence !== "INSUFFICIENT") return null;
  return <p className="analysis-note">{evidence.warnings[0] ?? fallback}</p>;
}

function driverLabel(id: string | null | undefined, byId: Map<string, DriverRaceState>) {
  if (!id) return MISSING;
  const driver = byId.get(id);
  return driver ? `${driverCode(driver.driver)} · ${driver.driver.last_name}` : id.toUpperCase();
}

function withGap(
  id: string | null | undefined,
  gap: number | null | undefined,
  byId: Map<string, DriverRaceState>,
) {
  const label = driverLabel(id, byId);
  return gap == null || label === MISSING ? label : `${label} · ${fmtNumber(gap, "s")}`;
}

function rejoinPosition(analysis: DriverAnalysis) {
  const { position_range: range, projected_position: position } = analysis.rejoin;
  if (range) return range[0] === range[1] ? `P${range[0]}` : `P${range[0]}–P${range[1]}`;
  return position == null ? MISSING : `P${position}`;
}

function BooleanState({ value }: { value?: boolean | null }) {
  return <>{value == null ? MISSING : value ? "Yes" : "No"}</>;
}

interface Props {
  analysis: DriverAnalysis;
  drivers: DriverRaceState[];
  outdated?: boolean;
}

export function DriverEngineeringAnalysis({ analysis, drivers, outdated = false }: Props) {
  const byId = new Map(drivers.map((driver) => [driver.driver.id, driver]));
  const { recent_pace: pace, tyres, pit_loss: pitLoss, rejoin, traffic } = analysis;
  return (
    <>
      {outdated ? (
        <p className="analysis-note analysis-warning" role="status">
          Showing the previous analysis while the latest one is unavailable.
        </p>
      ) : null}
      <section aria-label="Pace analysis">
        <div className="analysis-heading">
          <h3>Pace</h3>
          <EvidenceLabel confidence={pace.confidence} />
        </div>
        <div className="analysis-primary">
          <span>Typical lap</span>
          <strong>{formatLapTime(pace.seconds)}</strong>
        </div>
        <MissingReason evidence={pace} fallback="Not enough clean laps yet at this point." />
        <details className="analysis-more">
          <summary>Show details</summary>
          <dl className="analysis-rows">
            <div>
              <dt>Clean laps used</dt>
              <dd>{pace.lap_numbers.length ? pace.lap_numbers.join(", ") : "None"}</dd>
            </div>
          </dl>
          <p className="analysis-note">
            Laps with a pit stop, a yellow flag or a safety car are left out.
          </p>
        </details>
      </section>

      <section aria-label="Tyre and stint analysis">
        <div className="analysis-heading">
          <h3>Tyres</h3>
          <EvidenceLabel confidence={tyres.confidence} />
        </div>
        <dl className="analysis-rows">
          <div>
            <dt>Compound</dt>
            <dd className="analysis-capitalize">{plainCompound(tyres.compound) ?? MISSING}</dd>
          </div>
          <div>
            <dt>Tyre age</dt>
            <dd>{tyres.tyre_age == null ? MISSING : `${tyres.tyre_age} laps`}</dd>
          </div>
          <div>
            <dt>Stint</dt>
            <dd>{tyres.stint_number ?? MISSING}</dd>
          </div>
        </dl>
        <details className="analysis-more">
          <summary>Show details</summary>
          <dl className="analysis-rows">
            <div>
              <dt>Comparison laps</dt>
              <dd>{tyres.sample_count}</dd>
            </div>
          </dl>
          <p className="analysis-note">
            Describes the tyres only. It does not predict how long they will last.
          </p>
        </details>
      </section>

      <section className="analysis-pit" aria-label="Pit and rejoin analysis">
        <div className="analysis-heading">
          <h3>If pitting now</h3>
          <EvidenceLabel confidence={rejoin.confidence} />
        </div>
        <div className="analysis-pit-grid">
          <div>
            <span>Time lost in a stop</span>
            <strong>{formatSeconds(pitLoss.total_seconds)}</strong>
            <small>
              {pitLoss.sample_count
                ? `Based on ${pitLoss.sample_count} stop${pitLoss.sample_count === 1 ? "" : "s"} so far`
                : "No stops to learn from yet"}
            </small>
          </div>
          <div>
            <span>Would rejoin</span>
            <strong>{rejoinPosition(analysis)}</strong>
            <small>{plainTraffic(rejoin.traffic)} after the stop</small>
          </div>
        </div>
        <MissingReason evidence={pitLoss} fallback="Too few pit stops so far to estimate one." />
        <dl className="analysis-rows">
          <div>
            <dt>Car ahead after the stop</dt>
            <dd>{withGap(rejoin.ahead_id, rejoin.gap_ahead, byId)}</dd>
          </div>
          <div>
            <dt>Car behind after the stop</dt>
            <dd>{withGap(rejoin.behind_id, rejoin.gap_behind, byId)}</dd>
          </div>
        </dl>
      </section>

      <section className="analysis-wide" aria-label="Traffic analysis">
        <div className="analysis-heading">
          <h3>Traffic</h3>
          <EvidenceLabel confidence={traffic.confidence} />
        </div>
        <div className={`analysis-traffic traffic-${traffic.status.toLowerCase()}`}>
          <strong>{plainTraffic(traffic.status)}</strong>
          <span>
            {traffic.clear_air === true
              ? "Nobody close enough to slow the car"
              : traffic.clear_air === false
                ? "Other cars close by"
                : "Not known"}
          </span>
        </div>
        <dl className="analysis-rows">
          <div>
            <dt>Car ahead</dt>
            <dd>{withGap(traffic.ahead_id, traffic.gap_ahead, byId)}</dd>
          </div>
          <div>
            <dt>Car behind</dt>
            <dd>{withGap(traffic.behind_id, traffic.gap_behind, byId)}</dd>
          </div>
          <div>
            <dt>Held up by a slower car</dt>
            <dd>
              <BooleanState value={traffic.slower_car_blockage} />
            </dd>
          </div>
        </dl>
      </section>
    </>
  );
}
