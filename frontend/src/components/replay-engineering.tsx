"use client";

import { AlertTriangle, LoaderCircle } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import {
  getReplayDriverAnalysis,
  getReplayOvercut,
  getReplayStrategy,
  getReplayUndercut,
} from "@/lib/api/replay";
import type { DriverAnalysis, DriverRaceState, PairAnalysis, PitWallDriver } from "@/lib/api/types";
import { driverCode } from "@/lib/format";
import { isOut, plainStatus, sentenceCase } from "@/lib/plain";
import { CompoundBadge } from "./compound-badge";
import {
  ConfidenceBadge,
  DriverEngineeringAnalysis,
  EvidenceLabel,
} from "./driver-engineering-analysis";
import { DriverSummary } from "./driver-summary";
import { ReplayStrategy } from "./replay-strategy";

export interface ReplayAnalysisLoaders {
  driver: (year: number, round: number, lap: number, driverId: string) => Promise<DriverAnalysis>;
  strategy: (year: number, round: number, lap: number, driverId: string) => Promise<PitWallDriver>;
  undercut: (
    year: number,
    round: number,
    lap: number,
    driverId: string,
    targetId: string,
  ) => Promise<PairAnalysis>;
  overcut: (
    year: number,
    round: number,
    lap: number,
    driverId: string,
    targetId: string,
  ) => Promise<PairAnalysis>;
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

const MISSING = "—";

function formatLapTime(value?: number | null) {
  if (value == null) return MISSING;
  const minutes = Math.floor(value / 60);
  return `${minutes}:${(value - minutes * 60).toFixed(3).padStart(6, "0")}`;
}

function formatSeconds(value?: number | null, signed = false) {
  if (value == null) return MISSING;
  const prefix = signed && value > 0 ? "+" : "";
  return `${prefix}${value.toFixed(1)}s`;
}

function driverLabel(id: string | null | undefined, byId: Map<string, DriverRaceState>) {
  if (!id) return MISSING;
  const driver = byId.get(id);
  return driver ? `${driverCode(driver.driver)} · ${driver.driver.last_name}` : id.toUpperCase();
}

function TacticalRow({ title, result }: { title: string; result: PairAnalysis | null }) {
  return (
    <div className="analysis-tactical-row">
      <div>
        <b>{title}</b>
      </div>
      {result ? (
        <>
          <ConfidenceBadge value={result.confidence} />
          <dl>
            <div>
              <dt>Verdict</dt>
              <dd>{sentenceCase(result.opportunity)}</dd>
            </div>
            <div>
              <dt>Margin if it works</dt>
              <dd>{formatSeconds(result.estimated_margin, true)}</dd>
            </div>
          </dl>
          {result.conditions_required.length ? (
            <small>{result.conditions_required.slice(0, 2).join(" · ")}</small>
          ) : null}
        </>
      ) : (
        <span className="analysis-unavailable">No estimate for this pair of cars</span>
      )}
    </div>
  );
}

export function ReplayEngineering({
  year,
  round,
  lap,
  selected,
  drivers,
  loaders = defaultReplayAnalysisLoaders,
}: Props) {
  const defaultTarget =
    drivers.find((driver) => driver.driver.id !== selected.driver.id)?.driver.id ?? "";
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
  // Retired, disqualified and non-starting cars have no race left to analyse.
  const out = isOut(selected.status);
  const byId = useMemo(
    () => new Map(drivers.map((driver) => [driver.driver.id, driver])),
    [drivers],
  );

  useEffect(() => {
    let active = true;
    const load = async () => {
      let embedded: DriverAnalysis | null = null;
      try {
        const value = await loaders.strategy(year, round, lap, selected.driver.id);
        embedded = value.engineering_analysis ?? null;
        if (active) setStrategy(value);
      } catch (caught) {
        if (active)
          setStrategyError(
            caught instanceof Error ? caught.message : "Historical strategy unavailable",
          );
      } finally {
        if (active) setStrategyLoading(false);
      }
      if (embedded) {
        if (active) {
          setAnalysis(embedded);
          setLoading(false);
        }
        return;
      }
      try {
        const value = await loaders.driver(year, round, lap, selected.driver.id);
        if (active) setAnalysis(value);
      } catch (caught) {
        if (active)
          setError(caught instanceof Error ? caught.message : "Engineering analysis unavailable");
      } finally {
        if (active) setLoading(false);
      }
    };
    void load();
    return () => {
      active = false;
    };
  }, [lap, loaders, round, selected.driver.id, year]);

  const loadPairs = async (nextTarget: string) => {
    const request = ++pairRequest.current;
    setTargetId(nextTarget);
    setPairError(null);
    setUndercut(null);
    setOvercut(null);
    if (!nextTarget) {
      setPairLoading(false);
      return;
    }
    setPairLoading(true);
    const [under, over] = await Promise.allSettled([
      loaders.undercut(year, round, lap, selected.driver.id, nextTarget),
      loaders.overcut(year, round, lap, selected.driver.id, nextTarget),
    ]);
    if (request !== pairRequest.current) return;
    if (under.status === "fulfilled") setUndercut(under.value);
    if (over.status === "fulfilled") setOvercut(over.value);
    if (under.status === "rejected" || over.status === "rejected")
      setPairError("Part of this comparison could not load.");
    setPairLoading(false);
  };

  useEffect(() => {
    // The keyed workspace performs one bounded pair request for its initial comparison car.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    void loadPairs(defaultTarget);
    return () => {
      pairRequest.current += 1;
    };
    // The component is keyed by lap and selected driver; only its initial target belongs here.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <aside className="replay-driver-panel replay-engineering" aria-live="polite">
      <div className="replay-section-title">
        <h2>Driver engineering</h2>
        <span>As of lap {lap}</span>
      </div>
      <div className="replay-driver-head">
        <strong>{selected.position ?? "—"}</strong>
        <i />
        <span>
          <b>{selected.driver.full_name}</b>
          <small>{selected.constructor?.name ?? "Constructor unavailable"}</small>
        </span>
      </div>

      <DriverSummary
        selected={selected}
        drivers={drivers}
        analysis={analysis}
        strategy={strategy}
      />

      <section className="analysis-observed" aria-label="Observed driver state">
        <div className="analysis-heading">
          <h3>Right now</h3>
          <EvidenceLabel observed />
        </div>
        <dl>
          <div>
            <dt>Position</dt>
            <dd>{selected.position ?? "—"}</dd>
          </div>
          <div>
            <dt>Last lap</dt>
            <dd>{formatLapTime(selected.last_lap_time)}</dd>
          </div>
          <div>
            <dt>Compound</dt>
            <dd>
              <CompoundBadge compound={selected.compound} full />
            </dd>
          </div>
          <div>
            <dt>Tyre age</dt>
            <dd>{selected.tyre_age == null ? MISSING : `${selected.tyre_age} laps`}</dd>
          </div>
          <div>
            <dt>Stint</dt>
            <dd>{selected.stint_number ?? MISSING}</dd>
          </div>
          <div>
            <dt>Stops</dt>
            <dd>{selected.pit_stops_completed}</dd>
          </div>
        </dl>
        <div className="analysis-state-line">
          <span className={`replay-status replay-status-${selected.status}`}>
            {plainStatus(selected.status)}
          </span>
          <span>Uses only what was known by lap {lap}</span>
        </div>
      </section>

      {loading ? (
        <div className="analysis-loading" role="status">
          <LoaderCircle aria-hidden="true" />
          <span>
            <b>Loading the analysis</b>
            <small>The timing table stays usable meanwhile.</small>
          </span>
        </div>
      ) : null}
      {error ? (
        <div className="analysis-error" role="alert">
          <AlertTriangle aria-hidden="true" />
          <span>
            <b>The analysis could not load</b>
            <small>{error}</small>
          </span>
        </div>
      ) : null}

      {out ? null : analysis ? (
        <div className="analysis-derived">
          <DriverEngineeringAnalysis analysis={analysis} drivers={drivers} />
          <details className="analysis-tactical" aria-label="Undercut and overcut context">
            <summary className="analysis-heading">
              <h3>Undercut / overcut</h3>
              <span className="analysis-low-label">Low confidence</span>
            </summary>
            <p className="analysis-note">
              Could pitting before (undercut) or after (overcut) another car gain the place? A rough
              guide only: few past races to learn from, and the margins have wide error.
            </p>
            <label>
              <span>Comparison car</span>
              <select
                aria-label="Comparison car"
                value={targetId}
                onChange={(event) => void loadPairs(event.target.value)}
              >
                {drivers
                  .filter((driver) => driver.driver.id !== selected.driver.id)
                  .map((driver) => (
                    <option value={driver.driver.id} key={driver.driver.id}>
                      {driverLabel(driver.driver.id, byId)}
                    </option>
                  ))}
              </select>
            </label>
            {pairLoading ? (
              <div className="analysis-pair-loading" role="status">
                Loading the comparison…
              </div>
            ) : (
              <>
                <TacticalRow title="Undercut" result={undercut} />
                <TacticalRow title="Overcut" result={overcut} />
              </>
            )}
            {pairError ? <p className="analysis-note analysis-warning">{pairError}</p> : null}
          </details>
        </div>
      ) : null}
      {out ? null : (
        <ReplayStrategy
          strategy={strategy}
          loading={strategyLoading}
          error={strategyError}
          lap={lap}
        />
      )}
    </aside>
  );
}
