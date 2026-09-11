"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ChevronDown, RefreshCw, Sun } from "lucide-react";
import { getLiveDriver, getLivePitWall, getLiveRace, getLiveStatus } from "@/lib/api/live";
import { getRaceControl, getWeather } from "@/lib/api/weekend";
import type { CurrentPitWall, LiveRace, LiveStatus, PitWallAlert, PitWallDriver, RaceControlResponse, WeatherResponse } from "@/lib/api/types";
import { driverCode, fmtNumber, fmtTime } from "@/lib/format";
import { CompoundBadge } from "./compound-badge";
import { DriverEngineeringAnalysis } from "./driver-engineering-analysis";
import { ReplayStrategy } from "./replay-strategy";
import { EmptyRow, Status } from "./status";

export interface LivePitWallLoaders {
  status: () => Promise<LiveStatus>;
  race: () => Promise<LiveRace>;
  pitwall: () => Promise<CurrentPitWall>;
  weather: () => Promise<WeatherResponse>;
  control: () => Promise<RaceControlResponse>;
  driver: (id: string) => Promise<CurrentPitWall>;
}
const defaultLoaders: LivePitWallLoaders = { status: getLiveStatus, race: getLiveRace, pitwall: getLivePitWall, weather: getWeather, control: getRaceControl, driver: getLiveDriver };
export interface LivePitWallProps { initialStatus: LiveStatus; initialRace?: LiveRace | null; initialPitwall?: CurrentPitWall | null; initialWeather?: WeatherResponse | null; initialControl?: RaceControlResponse | null; driverLoader?: (id: string) => Promise<CurrentPitWall>; loaders?: Partial<LivePitWallLoaders>; pollIntervalMs?: number }
type MobileView = "overview" | "timing" | "strategy" | "feed";

function gapText(driver: PitWallDriver) {
  if (driver.gap_kind === "LAP_DEFICIT") return `+${driver.laps_behind ?? "—"}L`;
  return driver.gap_to_leader_seconds == null ? "—" : driver.gap_to_leader_seconds === 0 ? "Leader" : `+${fmtNumber(driver.gap_to_leader_seconds)}s`;
}

function Feed({ alerts }: { alerts: PitWallAlert[] }) {
  return <div className="engineering-feed">{alerts.length ? alerts.slice(0, 6).map((alert, index) => <p key={`${alert.kind}-${alert.driver_id}-${index}`}><i/><b>{alert.kind}</b><span>{alert.detail}</span></p>) : <EmptyRow>No active engineering alerts.</EmptyRow>}</div>;
}

export function LivePitWall({ initialStatus, initialRace = null, initialPitwall = null, initialWeather = null, initialControl = null, driverLoader, loaders, pollIntervalMs = 10_000 }: LivePitWallProps) {
  const services = useMemo<LivePitWallLoaders>(() => ({ ...defaultLoaders, ...loaders, driver: driverLoader ?? loaders?.driver ?? defaultLoaders.driver }), [driverLoader, loaders]);
  const [status, setStatus] = useState(initialStatus);
  const [race, setRace] = useState(initialRace);
  const [pitwall, setPitwall] = useState(initialPitwall);
  const [weather, setWeather] = useState(initialWeather);
  const [control, setControl] = useState(initialControl);
  const [selected, setSelected] = useState(initialPitwall?.snapshot?.drivers[0]?.driver.id ?? null);
  const [detail, setDetail] = useState<CurrentPitWall | null>(initialPitwall?.driver ? initialPitwall : null);
  const [mobileView, setMobileView] = useState<MobileView>("overview");
  const [loading, setLoading] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const [refreshError, setRefreshError] = useState<string | null>(null);
  const [detailError, setDetailError] = useState<string | null>(null);
  const refreshRequest = useRef(0);
  const detailRequest = useRef(0);
  const selectedRef = useRef(selected);

  const loadDriver = useCallback(async (id: string, showLoading = true) => {
    const request = ++detailRequest.current;
    selectedRef.current = id;
    if (showLoading) setLoading(true);
    setDetailError(null);
    try {
      const value = await services.driver(id);
      if (request === detailRequest.current && selectedRef.current === id) setDetail(value);
    } catch (caught) {
      if (request === detailRequest.current && selectedRef.current === id) setDetailError(caught instanceof Error ? caught.message : "Driver analysis unavailable");
    } finally {
      if (request === detailRequest.current && selectedRef.current === id) setLoading(false);
    }
  }, [services]);

  const refresh = useCallback(async () => {
    if (document.hidden) return;
    const request = ++refreshRequest.current;
    setRefreshing(true);
    try {
      const nextStatus = await services.status();
      if (request !== refreshRequest.current) return;
      setStatus(nextStatus);
      const available = nextStatus.live || nextStatus.availability === "DELAYED_AVAILABLE";
      if (available) {
        const driverId = selectedRef.current;
        const selectedRequest = driverId ? ++detailRequest.current : null;
        const all = await Promise.allSettled([services.race(), services.pitwall(), services.weather(), services.control(), driverId ? services.driver(driverId) : Promise.resolve(null)]);
        if (request !== refreshRequest.current) return;
        if (all[0].status === "fulfilled") setRace(all[0].value);
        if (all[1].status === "fulfilled") setPitwall(all[1].value);
        if (all[2].status === "fulfilled") setWeather(all[2].value);
        if (all[3].status === "fulfilled") setControl(all[3].value);
        if (driverId && selectedRequest === detailRequest.current && selectedRef.current === driverId) {
          if (all[4].status === "fulfilled" && all[4].value) { setDetail(all[4].value); setDetailError(null); }
          else setDetailError("Current driver engineering and strategy could not be refreshed.");
          setLoading(false);
        }
        if (all[0].status === "rejected" || all[1].status === "rejected") setRefreshError("Some current race data could not be refreshed.");
        else setRefreshError(null);
      }
    } catch (caught) {
      if (request === refreshRequest.current) setRefreshError(caught instanceof Error ? caught.message : "Refresh failed");
    } finally { if (request === refreshRequest.current) setRefreshing(false); }
  }, [services]);

  useEffect(() => {
    let stopped = false;
    let timer: ReturnType<typeof setTimeout>;
    const active = status.live || status.availability === "DELAYED_AVAILABLE";
    const delay = active ? pollIntervalMs : Math.max(pollIntervalMs, 30_000);
    const tick = async () => { await refresh(); if (!stopped) timer = setTimeout(tick, delay); };
    timer = setTimeout(tick, delay);
    return () => { stopped = true; clearTimeout(timer); };
  }, [pollIntervalMs, refresh, status.availability, status.live]);

  useEffect(() => {
    // Hydrates the server-selected row from the live detail endpoint.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    if (selected && !initialPitwall?.driver) void loadDriver(selected, false);
    return () => { detailRequest.current += 1; refreshRequest.current += 1; };
    // Initial selected-driver hydration only; polling owns subsequent refreshes.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const selectDriver = useCallback((id: string) => {
    setSelected(id); selectedRef.current = id; setDetail(null); setMobileView("overview");
    void loadDriver(id);
  }, [loadDriver]);

  const rows = pitwall?.snapshot?.drivers ?? [];
  const raceById = useMemo(() => new Map((race?.drivers ?? []).map((driver) => [driver.driver.id, driver])), [race?.drivers]);
  const rowDriver = rows.find((driver) => driver.driver.id === selected) ?? null;
  const detailDriver = detail?.driver?.driver.id === selected ? detail.driver : null;
  const snapshotLap = pitwall?.snapshot?.lap ?? race?.lap ?? 0;
  const detailLap = detailDriver?.engineering_analysis?.lap;
  const detailOutdated = Boolean(detailDriver && ((detailLap != null && snapshotLap && detailLap !== snapshotLap) || detailError));
  const chosen = detailOutdated ? rowDriver : detailDriver ?? rowDriver;
  const state = !detailOutdated && detail?.current_state ? detail.current_state : (selected ? raceById.get(selected) : null) ?? null;
  const engineering = detailDriver?.engineering_analysis ?? null;
  const fieldAlerts = useMemo(() => (pitwall?.snapshot?.alerts ?? []).filter((alert) => alert.driver_id !== selected), [pitwall?.snapshot?.alerts, selected]);
  const operational = status.live || status.availability === "DELAYED_AVAILABLE";

  if (!operational) return <div className="page"><header className="pitwall-offline-head"><div><span className="round-label">Live pit wall</span><h1>Session offline</h1></div><Status value={status.session_status}/><button className="icon-button" onClick={refresh} disabled={refreshing}><RefreshCw size={15}/>{refreshing ? "Refreshing" : "Refresh"}</button></header>{refreshError ? <div className="inline-alert" role="alert">Refresh issue · {refreshError}</div> : null}<section className="offline-state"><span className="experimental">Strategy · experimental</span><h2>{status.next_session?.session.name ?? "No live timing feed"}</h2><p>{status.reason ?? "Live strategy analysis activates when the provider reports an active session."}</p>{status.next_session ? <div className="offline-next"><span>Next session</span><strong>{status.next_session.event.name}</strong><time>{fmtTime(status.next_session.session.start)}</time></div> : null}<div className="offline-actions"><Link className="button-link button-primary" href="/weekend">View weekend</Link><span>Provider state · {status.availability.replaceAll("_", " ")}</span></div><div className="freshness-line"><Status value={status.freshness.state}/><span>Source · {status.provider}</span><span>Updated · {fmtTime(status.freshness.retrieved_at)}</span></div></section></div>;

  const trackState = race?.track_status ?? pitwall?.snapshot?.race_status ?? "UNKNOWN";
  const strategyFreshness = detailOutdated ? pitwall?.data_freshness ?? status.freshness : detail?.data_freshness ?? pitwall?.data_freshness ?? status.freshness;
  return <div className={`page pitwall-page mobile-${mobileView}`}>
    <header className="pitwall-context">
      <div className="pit-event"><strong>{status.event?.name ?? "Live race"}</strong><span>{status.event?.circuit.name ?? "Circuit pending"} · {status.session?.name ?? "Session"}</span></div>
      <div className="context-metric"><strong>Lap {pitwall?.snapshot?.lap ?? race?.lap ?? "—"}</strong><span>Race distance</span></div>
      <div className="context-metric context-green"><strong>Track {trackState}</strong><span>{status.session_status}</span></div>
      <div className={`context-metric context-freshness freshness-${(pitwall?.data_freshness.state ?? status.freshness.state).toLowerCase()}`}><strong>{pitwall?.data_freshness.state ?? status.freshness.state}{pitwall?.data_freshness.data_age_seconds != null ? ` · ${Math.round(pitwall.data_freshness.data_age_seconds)}s` : ""}</strong><span>{status.availability.replaceAll("_", " ")}</span></div>
      <div className="context-metric context-strategy"><strong>Strategy <em>Experimental</em></strong><span>{pitwall?.analysis_status ?? "Unavailable"} analysis</span></div>
      <button className="refresh-button" onClick={refresh} disabled={refreshing} aria-label="Refresh live data"><RefreshCw size={16}/><span>{refreshing ? "Refreshing" : "Refresh"}</span></button>
    </header>
    {refreshError ? <div className="inline-alert" role="alert">Refresh issue · {refreshError}</div> : null}
    {detailError ? <div className="inline-alert" role="alert">Selected driver detail · {detailError}</div> : null}
    {pitwall?.missing_requirements.length ? <div className="inline-alert">Partial analysis · {pitwall.missing_requirements.join(" · ")}</div> : null}
    <nav className="pitwall-mobile-tabs" aria-label="Pit wall views">{(["overview", "timing", "strategy", "feed"] as const).map((view) => <button key={view} aria-pressed={mobileView === view} onClick={() => setMobileView(view)}>{view}</button>)}</nav>
    <div className="pitwall-workspace">
      <section className="field-panel" aria-label="Running order"><div className="field-heading"><span>Pos</span><span>#</span><span>Driver</span><span>Gap</span><span>Tyre</span><span>Age</span><span>Stops</span><span>State</span></div>{rows.length ? <div className="field-list" role="listbox" aria-label="Drivers">{rows.map((driver) => { const live = raceById.get(driver.driver.id); return <button role="option" aria-selected={driver.driver.id === selected} onClick={() => selectDriver(driver.driver.id)} className={`driver-row ${driver.driver.id === selected ? "selected" : ""}`} key={driver.driver.id}><span className="position">{driver.current_position ?? "—"}</span><span className="car-number">{driver.driver.number ?? "—"}</span><b>{driverCode(driver.driver)}</b><span className="gap">{gapText(driver)}</span><CompoundBadge compound={driver.compound}/><span className="tyre-age">{driver.tyre_age ?? "—"}</span><span className="pit-count">{live?.pit_stops_completed ?? "—"}</span><span className={`row-state row-state-${driver.decision_state.toLowerCase()}`}>{driver.recommendation ?? driver.status}</span></button>; })}</div> : <EmptyRow>Live field analysis is not available.</EmptyRow>}<button className="mobile-full-timing" onClick={() => setMobileView("timing")}>View full timing →</button></section>
      <section className="driver-panel" aria-live="polite">{loading && !chosen ? <div className="loading-shell">Loading driver analysis…</div> : chosen ? <>
        <div className="driver-head"><div className="driver-position">{chosen.current_position ?? state?.position ?? "—"}</div><div><strong>{driverCode(chosen.driver)}</strong><span>{chosen.driver.full_name}</span></div><button className="driver-select-label" onClick={() => setMobileView("timing")}>Select driver <ChevronDown aria-hidden="true"/></button><b className="driver-p">P{chosen.current_position ?? state?.position ?? "—"}</b></div>
        <dl className="driver-snapshot"><div><dt>Gap to next</dt><dd>{gapText(chosen)}</dd></div><div><dt>Recent pace</dt><dd>{fmtNumber(chosen.recent_pace_seconds_per_lap, "s")}</dd></div><div><dt>Tyre</dt><dd><CompoundBadge compound={chosen.compound} full/> <span>{chosen.tyre_age ?? "—"} laps</span></dd></div><div><dt>Stops</dt><dd>{state?.pit_stops_completed ?? "—"}</dd></div><div><dt>State</dt><dd className="healthy-state">{chosen.status}</dd></div></dl>
        <section className="live-engineering-block" aria-label="Selected driver engineering">
          <div className="live-hierarchy-title"><h2>Driver engineering</h2><span>RaceState lap {snapshotLap} · {detailOutdated ? "previous detail retained" : "current detail"}</span></div>
          {loading && !engineering ? <div className="analysis-loading" role="status">Loading current engineering analysis…</div> : null}
          {engineering ? <div className="analysis-derived live-analysis-derived"><DriverEngineeringAnalysis analysis={engineering} drivers={race?.drivers ?? []} outdated={detailOutdated}/></div> : !loading ? <EmptyRow>Engineering analysis is unavailable for this driver state.</EmptyRow> : null}
        </section>
        <ReplayStrategy strategy={chosen} loading={false} error={chosen ? null : detailError} lap={snapshotLap} context="live" freshness={strategyFreshness} outdated={detailOutdated}/>
        <section className="alerts-panel"><div className="section-header"><h2>Field engineering feed</h2><span>{fieldAlerts.length} active</span></div><Feed alerts={fieldAlerts}/></section>
      </> : <EmptyRow>Select a driver to inspect.</EmptyRow>}</section>
    </div>
    <aside className="pitwall-support"><section><div className="support-title"><Sun/><span>Weather</span></div>{weather?.weather ? <dl className="support-weather"><div><dt>Air</dt><dd>{weather.weather.air_temperature_c ?? "—"}°C</dd></div><div><dt>Track</dt><dd>{weather.weather.track_temperature_c ?? "—"}°C</dd></div><div><dt>Humidity</dt><dd>{weather.weather.humidity_percent ?? "—"}%</dd></div><div><dt>Wind</dt><dd>{weather.weather.wind_speed ?? "—"}</dd></div></dl> : <EmptyRow>Weather unavailable.</EmptyRow>}</section><section className="support-control"><div className="support-title"><span>Race control</span></div>{control?.messages.length ? <ul>{control.messages.slice(0, 5).map((message, index) => <li key={`${message.timestamp}-${index}`}><time>{fmtTime(message.timestamp)}</time><b>{message.flag ?? message.category ?? "INFO"}</b><span>{message.message}</span></li>)}</ul> : <EmptyRow>No race control messages.</EmptyRow>}</section><section className="track-status"><span>Track status</span><strong>{trackState}</strong><small>{status.session_status}</small></section></aside>
  </div>;
}
