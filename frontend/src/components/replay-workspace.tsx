"use client";

import { ChevronLeft, ChevronRight, Database, History, LoaderCircle } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { getReplayEvents, getReplayLaps, getReplaySeasons, getReplayState } from "@/lib/api/replay";
import type { DriverRaceState, Event, ReplayAvailableLaps, ReplayRaceState, Season } from "@/lib/api/types";
import { driverCode, fmtNumber } from "@/lib/format";
import { CompoundBadge } from "./compound-badge";
import { ReplayEngineering, type ReplayAnalysisLoaders } from "./replay-engineering";
import { EmptyRow, SurfaceError } from "./status";

export interface ReplayLoaders {
  seasons: () => Promise<Season[]>;
  events: (year: number) => Promise<Event[]>;
  laps: (year: number, round: number) => Promise<ReplayAvailableLaps>;
  state: (year: number, round: number, lap: number) => Promise<ReplayRaceState>;
}

const defaultLoaders: ReplayLoaders = { seasons: getReplaySeasons, events: getReplayEvents, laps: getReplayLaps, state: getReplayState };

function completedEvents(events: Event[]) {
  const now = Date.now();
  return events.filter((event) => new Date(`${event.race_date}T23:59:59Z`).getTime() < now).sort((a, b) => a.round - b.round);
}

function formatLapTime(value?: number | null) {
  if (value == null) return "—";
  const minutes = Math.floor(value / 60);
  const seconds = value - minutes * 60;
  return `${minutes}:${seconds.toFixed(3).padStart(6, "0")}`;
}

function gapText(driver: DriverRaceState) {
  if (driver.laps_behind) return `+${driver.laps_behind}L`;
  if (driver.position === 1 || driver.gap_to_leader === 0) return "Leader";
  return driver.gap_to_leader == null ? "—" : `+${fmtNumber(driver.gap_to_leader, "s")}`;
}

function intervalText(driver: DriverRaceState) {
  if (driver.laps_behind) return `${driver.laps_behind} lap${driver.laps_behind === 1 ? "" : "s"}`;
  return driver.gap_to_ahead == null ? "—" : `+${fmtNumber(driver.gap_to_ahead, "s")}`;
}

function nearestLap(laps: number[], requested: number) {
  return laps.reduce((closest, lap) => Math.abs(lap - requested) < Math.abs(closest - requested) ? lap : closest, laps[0]);
}

export function ReplayWorkspace({ loaders = defaultLoaders, analysisLoaders }: { loaders?: ReplayLoaders; analysisLoaders?: ReplayAnalysisLoaders }) {
  const [seasons, setSeasons] = useState<Season[]>([]);
  const [events, setEvents] = useState<Event[]>([]);
  const [selectedYear, setSelectedYear] = useState<number | null>(null);
  const [selectedRound, setSelectedRound] = useState<number | null>(null);
  const [available, setAvailable] = useState<ReplayAvailableLaps | null>(null);
  const [raceState, setRaceState] = useState<ReplayRaceState | null>(null);
  const [selectedDriverId, setSelectedDriverId] = useState<string | null>(null);
  const [draftLap, setDraftLap] = useState(1);
  const [pendingLap, setPendingLap] = useState<number | null>(null);
  const [catalogLoading, setCatalogLoading] = useState(true);
  const [raceLoading, setRaceLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const requestId = useRef(0);

  const loadRace = useCallback(async (event: Event) => {
    const id = ++requestId.current;
    setSelectedRound(event.round);
    setRaceLoading(true);
    setPendingLap(null);
    setAvailable(null);
    setRaceState(null);
    setSelectedDriverId(null);
    setError(null);
    try {
      const laps = await loaders.laps(event.year, event.round);
      if (!laps.laps.length) throw new Error("No leader laps are available for this race session.");
      const lap = laps.laps.at(-1)!;
      const state = await loaders.state(event.year, event.round, lap);
      if (requestId.current !== id) return;
      setAvailable(laps);
      setRaceState(state);
      setDraftLap(lap);
      setSelectedDriverId(state.drivers[0]?.driver.id ?? null);
    } catch (caught) {
      if (requestId.current === id) setError(caught instanceof Error ? caught.message : "Historical race unavailable");
    } finally {
      if (requestId.current === id) setRaceLoading(false);
    }
  }, [loaders]);

  const loadYear = useCallback(async (year: number, preferredRound?: number) => {
    const id = ++requestId.current;
    setSelectedYear(year);
    setRaceLoading(true);
    setEvents([]);
    setAvailable(null);
    setRaceState(null);
    setSelectedDriverId(null);
    setError(null);
    try {
      const calendar = completedEvents(await loaders.events(year));
      if (requestId.current !== id) return;
      setEvents(calendar);
      const event = calendar.find((item) => item.round === preferredRound) ?? calendar.at(-1);
      if (!event) throw new Error(`No completed race sessions are available for ${year}.`);
      await loadRace(event);
    } catch (caught) {
      if (requestId.current === id) {
        setRaceLoading(false);
        setError(caught instanceof Error ? caught.message : "Season calendar unavailable");
      }
    }
  }, [loadRace, loaders]);

  const initialize = useCallback(async () => {
    requestId.current += 1;
    setCatalogLoading(true);
    setError(null);
    try {
      const supported = (await loaders.seasons()).filter((season) => season.year >= 2021).sort((a, b) => a.year - b.year);
      setSeasons(supported);
      if (!supported.length) throw new Error("No historical replay seasons are available.");
      let chosen: { year: number; events: Event[] } | null = null;
      for (const season of supported.toReversed()) {
        const calendar = completedEvents(await loaders.events(season.year));
        if (calendar.length) { chosen = { year: season.year, events: calendar }; break; }
      }
      if (!chosen) throw new Error("No completed race sessions are available for replay.");
      setSelectedYear(chosen.year);
      setEvents(chosen.events);
      await loadRace(chosen.events.at(-1)!);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Replay catalogue unavailable");
    } finally { setCatalogLoading(false); }
  }, [loadRace, loaders]);

  useEffect(() => {
    // Initializing this client-only archive requires a one-time state transition on mount.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    void initialize();
    return () => {
      requestId.current += 1;
    };
  }, [initialize]);

  const loadLap = useCallback(async (lap: number) => {
    if (selectedYear == null || selectedRound == null || !available || !available.laps.includes(lap)) return;
    const id = ++requestId.current;
    setDraftLap(lap);
    setPendingLap(lap);
    setError(null);
    try {
      const state = await loaders.state(selectedYear, selectedRound, lap);
      if (requestId.current !== id) return;
      setRaceState(state);
      setSelectedDriverId((current) => state.drivers.some((driver) => driver.driver.id === current) ? current : state.drivers[0]?.driver.id ?? null);
    } catch (caught) {
      if (requestId.current === id) setError(caught instanceof Error ? caught.message : "Lap state unavailable");
    } finally {
      if (requestId.current === id) setPendingLap(null);
    }
  }, [available, loaders, selectedRound, selectedYear]);

  useEffect(() => {
    if (!available || raceLoading || pendingLap != null || raceState?.current_lap === draftLap || !available.laps.includes(draftLap)) return;
    const timer = window.setTimeout(() => void loadLap(draftLap), 350);
    return () => window.clearTimeout(timer);
  }, [available, draftLap, loadLap, pendingLap, raceLoading, raceState?.current_lap]);

  const selectedEvent = events.find((event) => event.round === selectedRound) ?? raceState?.event ?? null;
  const selectedDriver = useMemo(() => raceState?.drivers.find((driver) => driver.driver.id === selectedDriverId) ?? null, [raceState?.drivers, selectedDriverId]);
  const currentLapIndex = available?.laps.indexOf(raceState?.current_lap ?? draftLap) ?? -1;
  const previousLap = currentLapIndex > 0 ? available?.laps[currentLapIndex - 1] : undefined;
  const nextLap = available && currentLapIndex >= 0 && currentLapIndex < available.laps.length - 1 ? available.laps[currentLapIndex + 1] : undefined;

  const commitDirectLap = () => {
    if (!available?.laps.length) return;
    void loadLap(nearestLap(available.laps, draftLap));
  };

  return <div className="page replay-page">
    <header className="replay-header">
      <div className="replay-title"><History aria-hidden="true"/><div><span>Race archive</span><h1>Historical replay</h1></div></div>
      <div className="replay-event"><strong>{selectedEvent?.name ?? "Select a historical race"}</strong><span>{selectedYear ?? "—"} · {selectedEvent?.circuit.name ?? "Circuit unavailable"} · {raceState?.session.name ?? "Race"}</span></div>
      <div className="replay-lap-summary"><span>Leader lap</span><strong>{raceState?.current_lap ?? "—"} <em>/ {available?.laps.at(-1) ?? "—"}</em></strong></div>
      <div className="replay-mode"><Database aria-hidden="true"/><span><strong>Historical data</strong><small>No future data</small></span></div>
    </header>

    <section className="replay-controls" aria-label="Replay controls">
      <label><span>Season</span><select aria-label="Season" value={selectedYear ?? ""} onChange={(event) => void loadYear(Number(event.target.value))} disabled={catalogLoading}>{seasons.toReversed().map((season) => <option key={season.year} value={season.year}>{season.year}</option>)}</select></label>
      <label className="event-selector"><span>Event</span><select aria-label="Event" value={selectedRound ?? ""} onChange={(event) => { const selected = events.find((item) => item.round === Number(event.target.value)); if (selected) void loadRace(selected); }} disabled={raceLoading || !events.length}>{events.map((event) => <option key={event.round} value={event.round}>{event.name}</option>)}</select></label>
      <button className="replay-step" aria-label="Previous lap" disabled={previousLap == null || pendingLap != null || raceLoading} onClick={() => previousLap != null && void loadLap(previousLap)}><ChevronLeft/> <span>Previous lap</span></button>
      <label className="lap-input"><span>Lap</span><input aria-label="Direct lap selection" type="number" min={available?.laps[0] ?? 1} max={available?.laps.at(-1) ?? 1} value={draftLap} onChange={(event) => setDraftLap(Number(event.target.value))} onBlur={commitDirectLap} onKeyDown={(event) => { if (event.key === "Enter") commitDirectLap(); }}/><b>/ {available?.laps.at(-1) ?? "—"}</b></label>
      <button className="replay-step" aria-label="Next lap" disabled={nextLap == null || pendingLap != null || raceLoading} onClick={() => nextLap != null && void loadLap(nextLap)}><span>Next lap</span> <ChevronRight/></button>
      <label className="lap-scrubber"><input aria-label="Leader lap scrubber" type="range" min={available?.laps[0] ?? 1} max={available?.laps.at(-1) ?? 1} value={draftLap} disabled={!available || raceLoading} onChange={(event) => setDraftLap(Number(event.target.value))} onPointerUp={(event) => void loadLap(Number(event.currentTarget.value))} onKeyUp={(event) => void loadLap(Number(event.currentTarget.value))} onBlur={(event) => void loadLap(Number(event.currentTarget.value))}/><span><b>Lap {available?.laps[0] ?? "—"}</b><b>Lap {available?.laps.at(-1) ?? "—"}</b></span></label>
    </section>

    {error ? <div className="inline-alert replay-alert" role="alert">Replay issue · {error}{!raceState ? <button onClick={() => void initialize()}>Try again</button> : null}</div> : null}
    {catalogLoading && !raceState ? <div className="replay-initial" role="status" aria-label="Loading historical replay"><LoaderCircle aria-hidden="true"/><strong>Loading race archive…</strong><span>Finding the latest supported completed race.</span></div> : null}
    {!catalogLoading && !raceLoading && !raceState && error ? <SurfaceError title="Historical replay unavailable" detail={error}/> : null}
    {raceLoading && !raceState ? <div className="replay-initial" role="status" aria-label="Loading historical race"><LoaderCircle aria-hidden="true"/><strong>Loading historical race…</strong><span>FastF1 archive preparation may take a moment.</span></div> : null}

    {raceState ? <div className="replay-workspace" aria-busy={pendingLap != null}>
      {pendingLap != null ? <div className="replay-loading" role="status"><LoaderCircle aria-hidden="true"/><span><strong>Loading leader lap {pendingLap}</strong><small>Current lap retained until the causal snapshot is ready.</small></span></div> : null}
      <section className="replay-field" aria-label="Historical race timing">
        <div className="replay-section-title"><h2>Race timing</h2><span>Lap {raceState.current_lap} / {available?.laps.at(-1) ?? "—"} · {raceState.drivers.length} participants</span></div>
        <div className="replay-table-wrap"><div className="replay-timing-head"><span>Pos</span><span>Driver</span><span>Gap / interval</span><span>Tyre</span><span>Age</span><span>Stint</span><span>Stops</span><span>Recent pace</span><span>Status</span></div>
          <div className="replay-timing-list" role="listbox" aria-label="Historical drivers">{raceState.drivers.length ? raceState.drivers.map((driver) => <button role="option" aria-selected={driver.driver.id === selectedDriverId} className="replay-driver-row" key={driver.driver.id} onClick={() => setSelectedDriverId(driver.driver.id)}><span className="position">{driver.position ?? "—"}</span><span className="replay-driver"><b>{driverCode(driver.driver)}</b><small>{driver.driver.full_name}</small></span><span className="replay-gap"><b>{gapText(driver)}</b><small>{intervalText(driver)}</small></span><CompoundBadge compound={driver.compound}/><span>{driver.tyre_age ?? "—"}</span><span>{driver.stint_number ?? "—"}</span><span>{driver.pit_stops_completed}</span><span className="numeric">{formatLapTime(driver.recent_clean_pace ?? driver.last_lap_time)}</span><span className={`replay-status replay-status-${driver.status}`}>{driver.status.replaceAll("_", " ")}</span></button>) : <EmptyRow>No drivers were returned for this replay lap.</EmptyRow>}</div>
        </div>
      </section>
      {selectedDriver ? <ReplayEngineering key={`${raceState.current_lap}:${selectedDriver.driver.id}`} year={raceState.event.year} round={raceState.event.round} lap={raceState.current_lap} selected={selectedDriver} drivers={raceState.drivers} loaders={analysisLoaders}/> : <aside className="replay-driver-panel"><div className="replay-section-title"><h2>Driver engineering</h2><span>No driver selected</span></div><EmptyRow>{raceState.drivers.length ? "The selected driver is unavailable at this lap." : "No driver state is available."}</EmptyRow></aside>}
    </div> : null}
  </div>;
}
