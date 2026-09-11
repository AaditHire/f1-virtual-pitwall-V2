"use client";

import { AlertTriangle, FlaskConical, LoaderCircle } from "lucide-react";
import type { Freshness, PairedOutcome, PitWallDriver } from "@/lib/api/types";

function label(value: string) {
  return value.replaceAll("_", " ");
}

function recommendation(strategy: PitWallDriver) {
  if (strategy.recommendation) return strategy.recommendation;
  return strategy.decision_state === "INSUFFICIENT_DATA" ? "INSUFFICIENT_DATA" : "UNAVAILABLE";
}

function recommendationClass(value: string) {
  if (value.startsWith("PIT_NOW")) return "pit";
  if (value.startsWith("EXTEND")) return "extend";
  if (value.startsWith("HOLD")) return "hold";
  return "insufficient";
}

function seconds(value?: number | null) {
  if (value == null) return "Unavailable";
  return `${value > 0 ? "+" : ""}${value.toFixed(2)}s`;
}

function interval(value?: [number, number] | null) {
  return value ? `${seconds(value[0])} to ${seconds(value[1])}` : "Unavailable";
}

function positions(value?: [number, number] | null) {
  if (!value) return "Unavailable";
  return value[0] === value[1] ? `P${value[0]}` : `P${value[0]}–P${value[1]}`;
}

function Horizon({ outcome }: { outcome: PairedOutcome }) {
  return <div className="strategy-horizon">
    <div className="strategy-horizon-head"><strong>+{outcome.horizon_laps}</strong><span>{outcome.applicability ? label(outcome.applicability) : "TACTICAL"}</span></div>
    <dl><div><dt>PIT − EXTEND median</dt><dd>{seconds(outcome.median_time_delta_seconds)}</dd></div><div><dt>80% interval</dt><dd>{interval(outcome.interval_80)}</dd></div><div><dt>PIT position range</dt><dd>{positions(outcome.pit_net_position_range_80)}</dd></div><div><dt>EXTEND position range</dt><dd>{positions(outcome.extend_net_position_range_80)}</dd></div></dl>
  </div>;
}

interface Props {
  strategy: PitWallDriver | null;
  loading: boolean;
  error: string | null;
  lap: number;
  context?: "historical" | "live";
  freshness?: Freshness | null;
  outdated?: boolean;
}

export function ReplayStrategy({ strategy, loading, error, lap, context = "historical", freshness = null, outdated = false }: Props) {
  const output = strategy ? recommendation(strategy) : "";
  const outcomes = strategy?.paired_comparison?.outcomes ?? [];
  const contextState = outdated ? "STALE" : freshness?.state;
  const contextClass = contextState ? ` strategy-context-${contextState.toLowerCase()}` : "";
  const contextCopy = context === "live"
    ? `Re-anchored to observed live lap ${lap}. RaceState freshness · ${contextState ?? "UNKNOWN"}${freshness?.data_age_seconds != null ? ` · ${Math.round(freshness.data_age_seconds)}s old` : ""}.`
    : `Model output anchored only to information available through leader lap ${lap}. Each replay lap re-anchors to observed race state.`;

  return <section className={`replay-strategy${contextClass}`} aria-label={`Experimental ${context} strategy`}>
    <div className="strategy-title"><span><FlaskConical aria-hidden="true"/><b>Experimental strategy</b></span><strong>TACTICAL 1–5 LAP HORIZON</strong></div>
    <p className="strategy-helper">{contextCopy}</p>

    {loading ? <div className="strategy-loading" role="status"><LoaderCircle aria-hidden="true"/><span><b>Evaluating historical strategy</b><small>RaceState and engineering analysis remain available.</small></span></div> : null}
    {error ? <div className="strategy-error" role="alert"><AlertTriangle aria-hidden="true"/><span><b>Strategy evaluation unavailable</b><small>{error}</small></span></div> : null}

    {strategy ? <>
      <div className="strategy-decision">
        <div><span>Recommendation</span><strong className={`strategy-output output-${recommendationClass(output)}`}>{label(output)}</strong>{strategy.best_pit_compound ? <small>Candidate compound · {strategy.best_pit_compound}</small> : null}</div>
        <dl><div><dt>Confidence</dt><dd className={`strategy-confidence strategy-confidence-${strategy.decision_state.toLowerCase()}`}>{label(strategy.decision_state)}</dd></div><div><dt>Pit window</dt><dd>{strategy.pit_window ? label(strategy.pit_window.state) : "Unavailable"}</dd></div><div><dt>Alternative</dt><dd>{strategy.alternative ? label(strategy.alternative) : "Unavailable"}</dd></div></dl>
      </div>

      {strategy.pit_window?.reason || strategy.main_opportunity || strategy.main_risk ? <div className="strategy-evidence"><b>Backend evidence</b><p>{strategy.pit_window?.reason ?? strategy.main_opportunity ?? strategy.main_risk}</p>{strategy.model_disagreement ? <span>Policy and paired model disagree · confidence reduced by backend</span> : null}</div> : null}

      <section className="strategy-paired" aria-label="Paired PIT versus EXTEND evaluation">
        <div className="strategy-subtitle"><h4>Paired PIT vs EXTEND</h4><span>{strategy.paired_comparison ? `${label(strategy.paired_comparison.pit_action)} · ${label(strategy.paired_comparison.extend_action)}` : "UNAVAILABLE"}</span></div>
        {strategy.paired_comparison ? <>
          <div className="strategy-pair-status"><span>Shared race uncertainty across both actions</span><b>{strategy.uncertainty_overlap ? "OVERLAPPING OUTCOMES" : strategy.uncertainty_overlap === false ? "SEPARATED BY BACKEND" : "OVERLAP UNAVAILABLE"}</b></div>
          <div className="strategy-horizons">{outcomes.map((outcome) => <Horizon outcome={outcome} key={outcome.horizon_laps}/>)}</div>
        </> : <p className="strategy-unavailable">Paired simulation is unavailable for this driver and lap.</p>}
        <p className="strategy-limit">Time delta is PIT minus EXTEND; negative favors PIT. Tactical consequences only. +5 is not a complete stint or race optimization.</p>
      </section>

      <section className="strategy-alerts" aria-label="Strategy alerts">
        <div className="strategy-subtitle"><h4>Strategy alerts</h4><span>{strategy.alerts.length} RETURNED</span></div>
        {strategy.alerts.length ? <ul>{strategy.alerts.map((alert, index) => <li key={`${alert.kind}-${alert.rival_id ?? "field"}-${index}`}><b>{label(alert.kind)}</b><span>{alert.detail}</span></li>)}</ul> : <p className="strategy-unavailable">No strategy alerts were returned.</p>}
      </section>
    </> : null}
  </section>;
}
