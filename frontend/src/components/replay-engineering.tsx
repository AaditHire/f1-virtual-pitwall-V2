"use client";

import { AlertTriangle, LoaderCircle } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { getReplayDriverAnalysis, getReplayOvercut, getReplayStrategy, getReplayUndercut } from "@/lib/api/replay";
import type { AnalysisConfidence, DriverAnalysis, DriverRaceState, PairAnalysis, PitWallDriver } from "@/lib/api/types";
import { driverCode, fmtNumber } from "@/lib/format";
import { CompoundBadge } from "./compound-badge";
import { ReplayStrategy } from "./replay-strategy";
import { Status } from "./status";

export interface ReplayAnalysisLoaders {
  driver: (year: number, round: number, lap: number, driverId: string) => Promise<DriverAnalysis>;
  strategy: (year: number, round: number, lap: number, driverId: string) => Promise<PitWallDriver>;
  undercut: (year: number, round: number, lap: number, driverId: string, targetId: string) => Promise<PairAnalysis>;
  overcut: (year: number, round: number, lap: number, driverId: string, targetId: string) => Promise<PairAnalysis>;
}

export const defaultReplayAnalysisLoaders: ReplayAnalysisLoaders = {
  driver: getReplayDriverAnalysis,
  strategy: getReplayStrategy,
  undercut: getReplayUndercut,
  overcut: getReplayOvercut,
};

interface Props {
  year: number;
  round: number;
  lap: number;
  selected: DriverRaceState;
  drivers: DriverRaceState[];
  loaders?: ReplayAnalysisLoaders;
}

function formatLapTime(value?: number | null) {
  if (value == null) return "Unavailable";
  const minutes = Math.floor(value / 60);
  return `${minutes}:${(value - minutes * 60).toFixed(3).padStart(6, "0")}`;
}

function formatSeconds(value?: number | null, signed = false) {
  if (value == null) return "Unavailable";
  const prefix = signed && value > 0 ? "+" : "";
  return `${prefix}${value.toFixed(2)}s`;
}

function displayState(value: string) {
  return value.replaceAll("_", " ");
}

function ConfidenceBadge({ value }: { value: AnalysisConfidence }) {
  return <span className={`analysis-confidence confidence-${value.toLowerCase()}`}>{value === "INSUFFICIENT" ? "INSUFFICIENT DATA" : value}</span>;
}

function EvidenceLabel({ confidence, observed = false }: { confidence?: AnalysisConfidence; observed?: boolean }) {
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

function TacticalRow({ title, result }: { title: string; result: PairAnalysis | null }) {
  return <div className="analysis-tactical-row">
    <div><b>{title}</b><span>Supporting context only</span></div>
    {result ? <>
      <ConfidenceBadge value={result.confidence}/>
      <dl><div><dt>Evidence outcome</dt><dd>{displayState(result.opportunity)}</dd></div><div><dt>Conditional margin</dt><dd>{formatSeconds(result.estimated_margin, true)}</dd></div></dl>
      {result.conditions_required.length ? <small>{result.conditions_required.slice(0, 2).join(" · ")}</small> : null}
    </> : <span className="analysis-unavailable">Unavailable for this comparison</span>}
  </div>;
}

export function ReplayEngineering({ year, round, lap, selected, drivers, loaders = defaultReplayAnalysisLoaders }: Props) {
  const defaultTarget = drivers.find((driver) => driver.driver.id !== selected.driver.id)?.driver.id ?? "";
  const [targetId, setTargetId] = useState(defaultTarget);
  const [analysis, setAnalysis] = useState<DriverAnalysis | null>(null);
  const [strategy, setStrategy] = useState<PitWallDriver | null>(null);
  const [undercut, setUndercut] = useState<PairAnalysis | null>(null);
  const [overcut, setOvercut] = useState<PairAnalysis | null>(null);
  const [loading, setLoading] = useState(true);
  const [strategyLoading, setStrategyLoading] = useState(true);
  const [pairLoading, setPairLoading] = useState(Boolean(defaultTarget));
  const [error, setError] = useState<string | null>(null);
  const [strategyError, setStrategyError] = useState<string | null>(null);
  const [pairError, setPairError] = useState<string | null>(null);
  const pairRequest = useRef(0);
  const byId = useMemo(() => new Map(drivers.map((driver) => [driver.driver.id, driver])), [drivers]);

  useEffect(() => {
    let active = true;
    const load = async () => {
      let embedded: DriverAnalysis | null = null;
      try {
        const value = await loaders.strategy(year, round, lap, selected.driver.id);
        embedded = value.engineering_analysis ?? null;
        if (active) setStrategy(value);
      } catch (caught) {
        if (active) setStrategyError(caught instanceof Error ? caught.message : "Historical strategy unavailable");
      } finally {
        if (active) setStrategyLoading(false);
      }
      if (embedded) {
        if (active) { setAnalysis(embedded); setLoading(false); }
        return;
      }
      try {
        const value = await loaders.driver(year, round, lap, selected.driver.id);
        if (active) setAnalysis(value);
      } catch (caught) {
        if (active) setError(caught instanceof Error ? caught.message : "Engineering analysis unavailable");
      } finally {
        if (active) setLoading(false);
      }
    };
    void load();
    return () => { active = false; };
  }, [lap, loaders, round, selected.driver.id, year]);

  const loadPairs = async (nextTarget: string) => {
    const request = ++pairRequest.current;
    setTargetId(nextTarget);
    setPairError(null);
    setUndercut(null);
    setOvercut(null);
    if (!nextTarget) { setPairLoading(false); return; }
    setPairLoading(true);
    const [under, over] = await Promise.allSettled([
      loaders.undercut(year, round, lap, selected.driver.id, nextTarget),
      loaders.overcut(year, round, lap, selected.driver.id, nextTarget),
    ]);
    if (request !== pairRequest.current) return;
    if (under.status === "fulfilled") setUndercut(under.value);
    if (over.status === "fulfilled") setOvercut(over.value);
    if (under.status === "rejected" || over.status === "rejected") setPairError("Some tactical context is unavailable.");
    setPairLoading(false);
  };

  useEffect(() => {
    // The keyed workspace performs one bounded pair request for its initial comparison car.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    void loadPairs(defaultTarget);
    return () => { pairRequest.current += 1; };
    // The component is keyed by lap and selected driver; only its initial target belongs here.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return <aside className="replay-driver-panel replay-engineering" aria-live="polite">
    <div className="replay-section-title"><h2>Driver engineering</h2><span>Leader lap {lap} cutoff</span></div>
    <div className="replay-driver-head"><strong>{selected.position ?? "—"}</strong><i/><span><b>{selected.driver.full_name}</b><small>{selected.constructor?.name ?? "Constructor unavailable"}</small></span></div>

    <section className="analysis-observed" aria-label="Observed driver state">
      <div className="analysis-heading"><h3>Race state</h3><EvidenceLabel observed/></div>
      <dl><div><dt>Position</dt><dd>{selected.position ?? "—"}</dd></div><div><dt>Last lap</dt><dd>{formatLapTime(selected.last_lap_time)}</dd></div><div><dt>Compound</dt><dd><CompoundBadge compound={selected.compound} full/></dd></div><div><dt>Tyre age</dt><dd>{selected.tyre_age == null ? "Unavailable" : `${selected.tyre_age} laps`}</dd></div><div><dt>Stint</dt><dd>{selected.stint_number ?? "Unavailable"}</dd></div><div><dt>Stops</dt><dd>{selected.pit_stops_completed}</dd></div></dl>
      <div className="analysis-state-line"><Status value={selected.status}/><span>Only data observed through leader lap {lap}</span></div>
    </section>

    {loading ? <div className="analysis-loading" role="status"><LoaderCircle aria-hidden="true"/><span><b>Loading engineering analysis</b><small>Race timing remains available.</small></span></div> : null}
    {error ? <div className="analysis-error" role="alert"><AlertTriangle aria-hidden="true"/><span><b>Engineering analysis unavailable</b><small>{error}</small></span></div> : null}

    {analysis ? <div className="analysis-derived">
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

      <section className="analysis-tactical" aria-label="Undercut and overcut context">
        <div className="analysis-heading"><h3>Undercut / overcut context</h3><span className="analysis-low-label">LOW CONFIDENCE</span></div>
        <label><span>Comparison car</span><select aria-label="Comparison car" value={targetId} onChange={(event) => void loadPairs(event.target.value)}>{drivers.filter((driver) => driver.driver.id !== selected.driver.id).map((driver) => <option value={driver.driver.id} key={driver.driver.id}>{driverLabel(driver.driver.id, byId)}</option>)}</select></label>
        {pairLoading ? <div className="analysis-pair-loading" role="status">Loading tactical context…</div> : <><TacticalRow title="Undercut" result={undercut}/><TacticalRow title="Overcut" result={overcut}/></>}
        {pairError ? <p className="analysis-note analysis-warning">{pairError}</p> : null}
        <p className="analysis-note">Sparse historical coverage and material margin error. Context, not a recommendation.</p>
      </section>
    </div> : null}
    <ReplayStrategy strategy={strategy} loading={strategyLoading} error={strategyError} lap={lap}/>
  </aside>;
}
