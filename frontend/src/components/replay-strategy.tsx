"use client";

import { AlertTriangle, FlaskConical, LoaderCircle } from "lucide-react";
import type { Freshness, PairedOutcome, PitWallDriver } from "@/lib/api/types";
import {
  plainCompound,
  plainDecisionState,
  plainPitWindow,
  plainRecommendation,
  sentenceCase,
} from "@/lib/plain";

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
  if (value == null) return "—";
  return `${value > 0 ? "+" : ""}${value.toFixed(1)}s`;
}

function interval(value?: [number, number] | null) {
  return value ? `${seconds(value[0])} to ${seconds(value[1])}` : "—";
}

function positions(value?: [number, number] | null) {
  if (!value) return "—";
  return value[0] === value[1] ? `P${value[0]}` : `P${value[0]}–P${value[1]}`;
}

function Horizon({ outcome }: { outcome: PairedOutcome }) {
  return (
    <div className="strategy-horizon">
      <div className="strategy-horizon-head">
        <strong>
          After {outcome.horizon_laps} lap{outcome.horizon_laps === 1 ? "" : "s"}
        </strong>
        <span>
          {outcome.applicability ? `${sentenceCase(outcome.applicability)} fit` : "Short range"}
        </span>
      </div>
      <dl>
        <div>
          <dt>Pitting vs staying out</dt>
          <dd>{seconds(outcome.median_time_delta_seconds)}</dd>
        </div>
        <div>
          <dt>Likely range</dt>
          <dd>{interval(outcome.interval_80)}</dd>
        </div>
        <div>
          <dt>Position if pitting</dt>
          <dd>{positions(outcome.pit_net_position_range_80)}</dd>
        </div>
        <div>
          <dt>Position if staying out</dt>
          <dd>{positions(outcome.extend_net_position_range_80)}</dd>
        </div>
      </dl>
    </div>
  );
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

export function ReplayStrategy({
  strategy,
  loading,
  error,
  lap,
  context = "historical",
  freshness = null,
  outdated = false,
}: Props) {
  const output = strategy ? recommendation(strategy) : "";
  const outcomes = strategy?.paired_comparison?.outcomes ?? [];
  const contextState = outdated ? "STALE" : freshness?.state;
  const contextClass = contextState ? ` strategy-context-${contextState.toLowerCase()}` : "";
  const contextCopy =
    context === "live"
      ? `Re-anchored to observed live lap ${lap}. RaceState freshness · ${contextState ?? "UNKNOWN"}${freshness?.data_age_seconds != null ? ` · ${Math.round(freshness.data_age_seconds)}s old` : ""}.`
      : `What a computer model would do at lap ${lap}, using only what was known then.`;

  return (
    <section
      className={`replay-strategy${contextClass}`}
      aria-label={`Experimental ${context} strategy`}
    >
      <div className="strategy-title">
        <span>
          <FlaskConical aria-hidden="true" />
          <b>Experimental strategy</b>
        </span>
        <strong>Looks 1–5 laps ahead</strong>
      </div>
      <p className="strategy-helper">{contextCopy}</p>

      {loading ? (
        <div className="strategy-loading" role="status">
          <LoaderCircle aria-hidden="true" />
          <span>
            <b>Running the strategy model</b>
            <small>The rest of the page stays usable meanwhile.</small>
          </span>
        </div>
      ) : null}
      {error ? (
        <div className="strategy-error" role="alert">
          <AlertTriangle aria-hidden="true" />
          <span>
            <b>The strategy model could not run</b>
            <small>{error}</small>
          </span>
        </div>
      ) : null}

      {strategy ? (
        <>
          <div className="strategy-decision">
            <div>
              <span>Suggestion</span>
              <strong className={`strategy-output output-${recommendationClass(output)}`}>
                {plainRecommendation(output)}
              </strong>
              {strategy.best_pit_compound ? (
                <small>Best tyre if pitting · {plainCompound(strategy.best_pit_compound)}</small>
              ) : null}
            </div>
            <dl>
              <div>
                <dt>How sure</dt>
                <dd
                  className={`strategy-confidence strategy-confidence-${strategy.decision_state.toLowerCase()}`}
                >
                  {plainDecisionState(strategy.decision_state)}
                </dd>
              </div>
              <div>
                <dt>Pit window</dt>
                <dd>{plainPitWindow(strategy.pit_window?.state)}</dd>
              </div>
              <div>
                <dt>Next best option</dt>
                <dd>{strategy.alternative ? plainRecommendation(strategy.alternative) : "—"}</dd>
              </div>
            </dl>
          </div>

          {strategy.pit_window?.reason || strategy.main_opportunity || strategy.main_risk ? (
            <div className="strategy-evidence">
              <b>Why</b>
              <p>
                {strategy.pit_window?.reason ?? strategy.main_opportunity ?? strategy.main_risk}
              </p>
              {strategy.model_disagreement ? (
                <span>The two strategy models disagree, so confidence is lower.</span>
              ) : null}
            </div>
          ) : null}

          <details className="strategy-more">
            <summary>Show the simulation numbers</summary>
            <section className="strategy-paired" aria-label="Paired PIT versus EXTEND evaluation">
              <div className="strategy-subtitle">
                <h4>Pitting now vs staying out</h4>
                <span>
                  {outcomes[0]?.trajectory_count
                    ? `${outcomes[0].trajectory_count} simulated races`
                    : strategy.paired_comparison
                      ? "Simulated"
                      : "Not run"}
                </span>
              </div>
              {strategy.paired_comparison ? (
                <>
                  <div className="strategy-pair-status">
                    <span>Do the two options give clearly different results?</span>
                    <b>
                      {strategy.uncertainty_overlap
                        ? "No, too close to call"
                        : strategy.uncertainty_overlap === false
                          ? "Yes, clearly different"
                          : "Not known"}
                    </b>
                  </div>
                  <div className="strategy-horizons">
                    {outcomes.map((outcome) => (
                      <Horizon outcome={outcome} key={outcome.horizon_laps} />
                    ))}
                  </div>
                </>
              ) : (
                <p className="strategy-unavailable">
                  The simulation did not run for this driver and lap.
                </p>
              )}
              <p className="strategy-limit">
                Time is pitting minus staying out, so a negative number means pitting is faster. It
                only looks a few laps ahead, not at the whole race.
              </p>
            </section>

            <section className="strategy-alerts" aria-label="Strategy alerts">
              <div className="strategy-subtitle">
                <h4>Alerts</h4>
                <span>{strategy.alerts.length}</span>
              </div>
              {strategy.alerts.length ? (
                <ul>
                  {strategy.alerts.map((alert, index) => (
                    <li key={`${alert.kind}-${alert.rival_id ?? "field"}-${index}`}>
                      <b>{sentenceCase(alert.kind)}</b>
                      <span>{alert.detail}</span>
                    </li>
                  ))}
                </ul>
              ) : (
                <p className="strategy-unavailable">No alerts.</p>
              )}
            </section>
          </details>
        </>
      ) : null}
    </section>
  );
}
