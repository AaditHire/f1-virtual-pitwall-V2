"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useState } from "react";
import { ChevronDown, RefreshCw, Sun } from "lucide-react";
import { getLiveDriver, getLivePitWall, getLiveRace, getLiveStatus } from "@/lib/api/live";
import { getRaceControl, getWeather } from "@/lib/api/weekend";
import type { CurrentPitWall, LiveRace, LiveStatus, PitWallAlert, PitWallDriver, RaceControlResponse, WeatherResponse } from "@/lib/api/types";
import { driverCode, fmtNumber, fmtTime } from "@/lib/format";
import { CompoundBadge } from "./compound-badge";
import { EmptyRow, Status } from "./status";

export interface LivePitWallProps { initialStatus: LiveStatus; initialRace?: LiveRace | null; initialPitwall?: CurrentPitWall | null; initialWeather?: WeatherResponse | null; initialControl?: RaceControlResponse | null; driverLoader?: (id: string) => Promise<CurrentPitWall> }
type MobileView = "overview" | "timing" | "strategy" | "feed";

function decisionQuality(driver: PitWallDriver) {
  const state = driver.decision_state.toUpperCase();
  if (state.includes("INSUFFICIENT")) return "INSUFFICIENT_DATA";
  if (state.includes("COARSE")) return "COARSE_ONLY";
  if (driver.model_disagreement || state.includes("CAUTION") || state.includes("UNCERTAIN") || state === "MEDIUM") return "CAUTION";
  if (state.includes("ACTIONABLE") || state === "HIGH" || state === "STRONG") return "ACTIONABLE";
  return "CAUTION";
}

function gapText(driver: PitWallDriver) {
  if (driver.gap_kind === "LAP_DEFICIT") return `+${driver.laps_behind ?? "—"}L`;
  return driver.gap_to_leader_seconds == null ? "—" : driver.gap_to_leader_seconds === 0 ? "Leader" : `+${fmtNumber(driver.gap_to_leader_seconds)}s`;
}

function Feed({ alerts }: { alerts: PitWallAlert[] }) {
  return <div className="engineering-feed">{alerts.length ? alerts.slice(0, 6).map((alert, index) => <p key={`${alert.kind}-${alert.driver_id}-${index}`}><i/><b>{alert.kind}</b><span>{alert.detail}</span></p>) : <EmptyRow>No active engineering alerts.</EmptyRow>}</div>;
}

export function LivePitWall({ initialStatus, initialRace = null, initialPitwall = null, initialWeather = null, initialControl = null, driverLoader = getLiveDriver }: LivePitWallProps) {
  const [status, setStatus] = useState(initialStatus);
  const [race, setRace] = useState(initialRace);
  const [pitwall, setPitwall] = useState(initialPitwall);
  const [weather, setWeather] = useState(initialWeather);
  const [control, setControl] = useState(initialControl);
  const [selected, setSelected] = useState(initialPitwall?.snapshot?.drivers[0]?.driver.id ?? null);
  const [detail, setDetail] = useState<CurrentPitWall | null>(null);
  const [mobileView, setMobileView] = useState<MobileView>("overview");
  const [loading, setLoading] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    if (document.hidden) return;
    setRefreshing(true);
    try {
      const nextStatus = await getLiveStatus();
      setStatus(nextStatus);
      if (nextStatus.live) {
        const all = await Promise.allSettled([getLiveRace(), getLivePitWall(), getWeather(), getRaceControl()]);
        if (all[0].status === "fulfilled") setRace(all[0].value);
        if (all[1].status === "fulfilled") setPitwall(all[1].value);
        if (all[2].status === "fulfilled") setWeather(all[2].value);
        if (all[3].status === "fulfilled") setControl(all[3].value);
      }
      setError(null);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Refresh failed");
    } finally { setRefreshing(false); }
  }, []);

  useEffect(() => {
    let stopped = false;
    let timer: ReturnType<typeof setTimeout>;
    const tick = async () => { await refresh(); if (!stopped) timer = setTimeout(tick, status.live ? 10_000 : 30_000); };
    timer = setTimeout(tick, status.live ? 10_000 : 15_000);
    return () => { stopped = true; clearTimeout(timer); };
  }, [refresh, status.live]);

  const selectDriver = useCallback((id: string) => {
    setSelected(id); setDetail(null); setLoading(true); setMobileView("overview");
    driverLoader(id).then(setDetail).catch((caught) => setError(caught instanceof Error ? caught.message : "Driver analysis unavailable")).finally(() => setLoading(false));
  }, [driverLoader]);

  const rows = pitwall?.snapshot?.drivers ?? [];
  const raceById = useMemo(() => new Map((race?.drivers ?? []).map((driver) => [driver.driver.id, driver])), [race?.drivers]);
  const chosen = detail?.driver ?? rows.find((driver) => driver.driver.id === selected) ?? null;
  const state = detail?.current_state ?? (selected ? raceById.get(selected) : null) ?? null;
  const selectedAlerts = useMemo(() => [...(chosen?.alerts ?? []), ...(pitwall?.snapshot?.alerts ?? [])], [chosen?.alerts, pitwall?.snapshot?.alerts]);

  if (!status.live) return <div className="page"><header className="pitwall-offline-head"><div><span className="round-label">Live pit wall</span><h1>Session offline</h1></div><Status value={status.session_status}/><button className="icon-button" onClick={refresh} disabled={refreshing}><RefreshCw size={15}/>{refreshing ? "Refreshing" : "Refresh"}</button></header><section className="offline-state"><span className="experimental">Strategy · experimental</span><h2>{status.next_session?.session.name ?? "No live timing feed"}</h2><p>{status.reason ?? "Live strategy analysis activates when the provider reports an active session."}</p>{status.next_session ? <div className="offline-next"><span>Next session</span><strong>{status.next_session.event.name}</strong><time>{fmtTime(status.next_session.session.start)}</time></div> : null}<div className="offline-actions"><Link className="button-link button-primary" href="/weekend">View weekend</Link><span>Recent race data · {status.availability.replaceAll("_", " ")}</span></div><div className="freshness-line"><Status value={status.freshness.state}/><span>Source · {status.provider}</span><span>Updated · {fmtTime(status.freshness.retrieved_at)}</span></div></section></div>;

  const latestOutcome = chosen?.paired_comparison?.outcomes.at(-1);
  const quality = chosen ? decisionQuality(chosen) : "INSUFFICIENT_DATA";
  const trackState = race?.track_status ?? pitwall?.snapshot?.race_status ?? "UNKNOWN";
  return <div className={`page pitwall-page mobile-${mobileView}`}>
    <header className="pitwall-context">
      <div className="pit-event"><strong>{status.event?.name ?? "Live race"}</strong><span>{status.event?.circuit.name ?? "Circuit pending"} · {status.session?.name ?? "Session"}</span></div>
      <div className="context-metric"><strong>Lap {pitwall?.snapshot?.lap ?? race?.lap ?? "—"}</strong><span>Race distance</span></div>
      <div className="context-metric context-green"><strong>Track {trackState}</strong><span>{status.session_status}</span></div>
      <div className="context-metric"><strong>{pitwall?.data_freshness.state ?? status.freshness.state}{pitwall?.data_freshness.data_age_seconds != null ? ` · ${Math.round(pitwall.data_freshness.data_age_seconds)}s` : ""}</strong><span>Data freshness</span></div>
      <div className="context-metric context-strategy"><strong>Strategy <em>Experimental</em></strong><span>{pitwall?.analysis_status ?? "Unavailable"} analysis</span></div>
      <button className="refresh-button" onClick={refresh} disabled={refreshing} aria-label="Refresh live data"><RefreshCw size={16}/><span>{refreshing ? "Refreshing" : "Refresh"}</span></button>
    </header>
    {error ? <div className="inline-alert" role="alert">Refresh issue · {error}</div> : null}
    {pitwall?.missing_requirements.length ? <div className="inline-alert">Partial analysis · {pitwall.missing_requirements.join(" · ")}</div> : null}
    <nav className="pitwall-mobile-tabs" aria-label="Pit wall views">{(["overview", "timing", "strategy", "feed"] as const).map((view) => <button key={view} aria-pressed={mobileView === view} onClick={() => setMobileView(view)}>{view}</button>)}</nav>
    <div className="pitwall-workspace">
      <section className="field-panel" aria-label="Running order"><div className="field-heading"><span>Pos</span><span>#</span><span>Driver</span><span>Gap</span><span>Tyre</span><span>Age</span><span>Stops</span><span>State</span></div>{rows.length ? <div className="field-list" role="listbox" aria-label="Drivers">{rows.map((driver) => { const live = raceById.get(driver.driver.id); return <button role="option" aria-selected={driver.driver.id === selected} onClick={() => selectDriver(driver.driver.id)} className={`driver-row ${driver.driver.id === selected ? "selected" : ""}`} key={driver.driver.id}><span className="position">{driver.current_position ?? "—"}</span><span className="car-number">{driver.driver.number ?? "—"}</span><b>{driverCode(driver.driver)}</b><span className="gap">{gapText(driver)}</span><CompoundBadge compound={driver.compound}/><span className="tyre-age">{driver.tyre_age ?? "—"}</span><span className="pit-count">{live?.pit_stops_completed ?? "—"}</span><span className={`row-state row-state-${driver.decision_state.toLowerCase()}`}>{driver.recommendation ?? driver.status}</span></button>; })}</div> : <EmptyRow>Live field analysis is not available.</EmptyRow>}<button className="mobile-full-timing" onClick={() => setMobileView("timing")}>View full timing →</button></section>
      <section className="driver-panel" aria-live="polite">{loading && !chosen ? <div className="loading-shell">Loading driver analysis…</div> : chosen ? <>
        <div className="driver-head"><div className="driver-position">{chosen.current_position ?? state?.position ?? "—"}</div><div><strong>{driverCode(chosen.driver)}</strong><span>{chosen.driver.full_name}</span></div><button className="driver-select-label" onClick={() => setMobileView("timing")}>Select driver <ChevronDown aria-hidden="true"/></button><b className="driver-p">P{chosen.current_position ?? state?.position ?? "—"}</b></div>
        <dl className="driver-snapshot"><div><dt>Gap to next</dt><dd>{gapText(chosen)}</dd></div><div><dt>Recent pace</dt><dd>{fmtNumber(chosen.recent_pace_seconds_per_lap, "s")}</dd></div><div><dt>Tyre</dt><dd><CompoundBadge compound={chosen.compound} full/> <span>{chosen.tyre_age ?? "—"} laps</span></dd></div><div><dt>Stops</dt><dd>{state?.pit_stops_completed ?? "—"}</dd></div><div><dt>State</dt><dd className="healthy-state">{chosen.status}</dd></div></dl>
        <div className="driver-detail-grid"><dl className="current-state"><div className="section-header"><h2>Current state</h2></div><div><dt>Position</dt><dd>{chosen.current_position ?? "—"}</dd></div><div><dt>Pit cycle</dt><dd>P{chosen.pit_cycle_position ?? "—"}</dd></div><div><dt>Traffic</dt><dd>{chosen.traffic}</dd></div><div><dt>Stint</dt><dd>{state?.stint_number ?? "—"}</dd></div><div><dt>Laps completed</dt><dd>{state?.laps_completed ?? "—"}</dd></div><div><dt>Model agreement</dt><dd>{chosen.model_disagreement ? "Mixed" : "Aligned"}</dd></div></dl>
          <div className="strategy-column"><div className="recommendation"><span>Recommendation <em>(experimental)</em></span><strong>{chosen.recommendation ?? "Insufficient data"}</strong><p>{chosen.main_opportunity ?? chosen.main_risk ?? "The model has not returned a strategic explanation."}</p><div className={`decision-quality quality-${quality.toLowerCase()}`}><span>Decision quality</span><b>{quality.replaceAll("_", " ")}</b><p>{quality === "ACTIONABLE" ? "Evidence clears the current decision threshold; continue to monitor live conditions." : "Conditional guidance only. Monitor tyre performance, traffic and track evolution."}</p></div></div>
          <div className="paired-outlook"><div className="section-header"><h2>{latestOutcome?.horizon_laps ?? 5}-lap outlook</h2><span>80% interval</span></div><div className="comparison"><div className="pit-scenario"><strong>Pit now</strong><span><b>Position range</b>{latestOutcome?.pit_net_position_range_80?.join(" – ") ?? "—"}</span><span><b>Relative time</b>{fmtNumber(latestOutcome?.median_time_delta_seconds, "s")}</span><span><b>Window</b>{chosen.paired_comparison?.pit_window_state.replaceAll("_", " ") ?? "—"}</span></div><div className="extend-scenario"><strong>Extend</strong><span><b>Position range</b>{latestOutcome?.extend_net_position_range_80?.join(" – ") ?? "—"}</span><span><b>Relative time</b>Baseline</span><span><b>Alternative</b>{chosen.alternative ?? "Continue stint"}</span></div></div></div>
        </div></div>
        <section className="alerts-panel"><div className="section-header"><h2>Alerts & engineering feed</h2><span>{selectedAlerts.length} active</span></div><Feed alerts={selectedAlerts}/></section>
      </> : <EmptyRow>Select a driver to inspect.</EmptyRow>}</section>
    </div>
    <aside className="pitwall-support"><section><div className="support-title"><Sun/><span>Weather</span></div>{weather?.weather ? <dl className="support-weather"><div><dt>Air</dt><dd>{weather.weather.air_temperature_c ?? "—"}°C</dd></div><div><dt>Track</dt><dd>{weather.weather.track_temperature_c ?? "—"}°C</dd></div><div><dt>Humidity</dt><dd>{weather.weather.humidity_percent ?? "—"}%</dd></div><div><dt>Wind</dt><dd>{weather.weather.wind_speed ?? "—"}</dd></div></dl> : <EmptyRow>Weather unavailable.</EmptyRow>}</section><section className="support-control"><div className="support-title"><span>Race control</span></div>{control?.messages.length ? <ul>{control.messages.slice(0, 5).map((message, index) => <li key={`${message.timestamp}-${index}`}><time>{fmtTime(message.timestamp)}</time><b>{message.flag ?? message.category ?? "INFO"}</b><span>{message.message}</span></li>)}</ul> : <EmptyRow>No race control messages.</EmptyRow>}</section><section className="track-status"><span>Track status</span><strong>{trackState}</strong><small>{status.session_status}</small></section></aside>
  </div>;
}
