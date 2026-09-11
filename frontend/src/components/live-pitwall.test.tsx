import { act, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { LivePitWall, type LivePitWallLoaders } from "./live-pitwall";
import type { CurrentPitWall, DriverAnalysis, DriverRaceState, Event, Freshness, LiveRace, LiveStatus, PitWallDriver, RaceControlResponse, WeatherResponse } from "@/lib/api/types";

const fresh = (state: Freshness["state"] = "FRESH", age = 4): Freshness => ({ source: "test", retrieved_at: "2026-09-10T10:00:00Z", provider_timestamp: "2026-09-10T09:59:56Z", data_age_seconds: age, state });
const event: Event = { year: 2026, round: 13, name: "Italian Grand Prix", race_date: "2026-09-10", warnings: [], sessions: [], circuit: { id: "monza", name: "Monza", locality: "Monza", country: "Italy" } };
const liveStatus = (availability: LiveStatus["availability"] = "LIVE_AVAILABLE", freshness = fresh()): LiveStatus => ({ live: availability === "LIVE_AVAILABLE", availability, session_status: availability === "DELAYED_AVAILABLE" ? "DELAYED" : availability === "LIVE_AVAILABLE" ? "LIVE" : "COMPLETED", provider: "test", freshness, event, session: { type: "RACE", name: "Race", date: "2026-09-10", source: "test", cancelled: false }, reason: availability === "UNAVAILABLE" ? "Provider offline" : undefined });

const raceDriver = (id: string, position: number, lap = 12): DriverRaceState => ({
  driver: { id, number: position, code: id.toUpperCase(), first_name: id, last_name: "Driver", full_name: `${id} Driver` }, constructor: { id: `team-${id}`, name: `${id} Team` }, position, grid_position: position,
  laps_completed: lap, gap_to_leader: position - 1, gap_to_ahead: position === 1 ? null : 1.2, compound: "MEDIUM", tyre_age: 5, stint_number: 1, pit_stops_completed: 0,
  last_lap_time: 91.1, recent_clean_pace: 91.2, status: "active", quality: { confidence: "observed", fields: {}, warnings: [] },
});
const evidence = (confidence: "HIGH" | "MEDIUM" | "LOW" | "INSUFFICIENT" = "MEDIUM") => ({ confidence, method: "phase-3-test", components: {}, warnings: [] });
const analysis = (id: string, lap: number): DriverAnalysis => ({
  year: 2026, round: 13, lap, cutoff_seconds: lap * 90, driver: raceDriver(id, id === "ver" ? 1 : 2, lap),
  recent_pace: { ...evidence(), seconds: 90.42, lap_numbers: [lap - 2, lap - 1, lap], sample_count: 3 },
  tyres: { ...evidence("LOW"), driver_id: id, compound: "MEDIUM", stint_number: 1, tyre_age: 5, reference_coverage: .8, sample_count: 5 },
  pit_loss: { ...evidence(), total_seconds: 21.45, sample_count: 4 },
  rejoin: { ...evidence(), driver_id: id, projected_position: 4, position_range: [3, 5], gap_ahead: 1.2, gap_behind: 2.1, ahead_id: "ver", behind_id: "nor", nearby_drivers: ["ver", "nor"], traffic: "MODERATE_TRAFFIC" },
  traffic: { ...evidence(), driver_id: id, status: "LIGHT_TRAFFIC", ahead_id: "ver", behind_id: "nor", gap_ahead: 1.3, gap_behind: 2.4, clear_air: false, slower_car_blockage: true, rejoin_density: 2 },
  pit_window: { ...evidence("LOW"), current_lap: lap, rejoin: { ...evidence("LOW"), driver_id: id, nearby_drivers: [], traffic: "UNKNOWN" }, clear_air_opportunity: null, traffic_risk: "UNKNOWN" },
});

const pitDriver = (id: string, position: number, lap: number, overrides: Partial<PitWallDriver> = {}): PitWallDriver => ({
  driver: raceDriver(id, position, lap).driver, status: "active", current_position: position, gap_kind: "TIME", gap_to_leader_seconds: position - 1, compound: "MEDIUM", tyre_age: 5,
  recent_pace_seconds_per_lap: 91.2, traffic: "LIGHT_TRAFFIC", pit_cycle_position: position, recommendation: "HOLD_NO_CLEAR_ADVANTAGE", alternative: "PIT_NOW_SOFT", decision_state: "CAUTION",
  operating_envelope: "SHORT_HORIZON_ONLY", horizons_laps: [1, 3, 5], policy_recommendation: "EXTEND_1", model_disagreement: true, best_pit_compound: "SOFT", evaluated_action_count: 3,
  decision_margin_seconds: .3, uncertainty_overlap: true, paired_comparison: { pit_action: "PIT_NOW_SOFT", extend_action: "EXTEND_5", decision_horizon_laps: 5, pit_window_state: "PIT_WINDOW_OPEN", outcomes: ([1, 3, 5] as const).map((horizon_laps) => ({ horizon_laps, trajectory_count: 100, median_time_delta_seconds: -.2 * horizon_laps, interval_80: [-1.2, .8], pit_net_position_range_80: [2, 5], extend_net_position_range_80: [1, 4] })) },
  pit_window: { state: "PIT_WINDOW_OPEN", best_compound: "SOFT", paired_advantage_seconds: .4, pit_cycle_position_advantage: 1, traffic: "LIGHT_TRAFFIC", rejoin: "MODERATE_TRAFFIC", uncertainty: "CAUTION", reason: "Paired evidence favors PIT but does not clear the strong-window gate." },
  main_opportunity: "A short-horizon tyre offset is available.", relevant_rivals: [], alerts: [{ kind: "PIT_WINDOW_OPEN", driver_id: id, detail: "Paired counterfactual state is PIT_WINDOW_OPEN." }], data_quality: {}, actions: [], engineering_analysis: analysis(id, lap), ...overrides,
});

const pitwall = (lap = 12, overrides: Partial<CurrentPitWall> = {}): CurrentPitWall => ({ analysis_status: "AVAILABLE", strategy_status: "EXPERIMENTAL", provider: "test", data_freshness: fresh(), missing_requirements: [], snapshot: { lap, race_status: "GREEN", trajectory_count: 100, drivers: [pitDriver("ver", 1, lap), pitDriver("nor", 2, lap)], alerts: [{ kind: "POSITION_AT_RISK", driver_id: "nor", detail: "Position at risk." }] }, ...overrides });
const detail = (id: string, lap = 12, overrides: Partial<PitWallDriver> = {}): CurrentPitWall => ({ analysis_status: "AVAILABLE", strategy_status: "EXPERIMENTAL", provider: "test", data_freshness: fresh(), missing_requirements: [], driver: pitDriver(id, id === "ver" ? 1 : 2, lap, overrides), current_state: raceDriver(id, id === "ver" ? 1 : 2, lap) });
const liveRace = (lap = 12, freshness = fresh()): LiveRace => ({ live: true, availability: freshness.state === "DELAYED" ? "DELAYED_AVAILABLE" : "LIVE_AVAILABLE", event, session: liveStatus().session, lap, status: freshness.state === "DELAYED" ? "DELAYED" : "LIVE", track_status: "GREEN", drivers: [raceDriver("ver", 1, lap), raceDriver("nor", 2, lap)], provider: "test", freshness, current_weekend_status: "LIVE", latest_results: [], news: [], missing_requirements: [] });
const weather = (freshness = fresh()): WeatherResponse => ({ available: true, weather: { air_temperature_c: 28, track_temperature_c: 42, humidity_percent: 38, wind_speed: 2.1 }, freshness });
const control = (freshness = fresh()): RaceControlResponse => ({ available: true, track_status: "GREEN", messages: [], freshness });

function loaders(overrides: Partial<LivePitWallLoaders> = {}): LivePitWallLoaders {
  return { status: vi.fn().mockResolvedValue(liveStatus()), race: vi.fn().mockResolvedValue(liveRace()), pitwall: vi.fn().mockResolvedValue(pitwall()), weather: vi.fn().mockResolvedValue(weather()), control: vi.fn().mockResolvedValue(control()), driver: vi.fn((id: string) => Promise.resolve(detail(id))), ...overrides };
}

afterEach(() => { vi.useRealTimers(); });

describe("LivePitWall", () => {
  it("renders honest historical-only and unavailable provider states", () => {
    const { unmount } = render(<LivePitWall initialStatus={{ ...liveStatus("HISTORICAL_ONLY"), reason: "No active session" }}/>);
    expect(screen.getByText("Session offline")).toBeInTheDocument();
    expect(screen.getByText("Provider state · HISTORICAL ONLY")).toBeInTheDocument();
    unmount();
    render(<LivePitWall initialStatus={liveStatus("UNAVAILABLE")}/>);
    expect(screen.getByText("Provider offline")).toBeInTheDocument();
    expect(screen.getByText("Provider state · UNAVAILABLE")).toBeInTheDocument();
  });

  it("keeps delayed current data operational and visibly inherits stale freshness", async () => {
    const stale = fresh("STALE", 75);
    render(<LivePitWall initialStatus={liveStatus("DELAYED_AVAILABLE", stale)} initialRace={liveRace(12, stale)} initialPitwall={pitwall(12, { data_freshness: stale })} loaders={loaders({ driver: vi.fn().mockResolvedValue({ ...detail("ver"), data_freshness: stale }) })}/>);
    expect(screen.getByText("STALE · 75s")).toBeInTheDocument();
    expect(screen.getByText("DELAYED AVAILABLE")).toBeInTheDocument();
    expect(await screen.findByText(/RaceState freshness · STALE · 75s old/)).toBeInTheDocument();
  });

  it("renders selected-driver engineering and the full experimental tactical strategy", async () => {
    render(<LivePitWall initialStatus={liveStatus()} initialRace={liveRace()} initialPitwall={pitwall()} loaders={loaders()}/>);
    expect(within(screen.getByRole("listbox", { name: "Drivers" })).getAllByRole("option")).toHaveLength(2);
    expect(await screen.findByRole("region", { name: "Pace analysis" })).toBeInTheDocument();
    expect(screen.getByRole("region", { name: "Pit and rejoin analysis" })).toHaveTextContent("P3–P5");
    expect(screen.getByRole("region", { name: "Traffic analysis" })).toHaveTextContent("LIGHT TRAFFIC");
    const strategy = screen.getByRole("region", { name: "Experimental live strategy" });
    expect(within(strategy).getByText(/experimental strategy/i)).toBeInTheDocument();
    expect(strategy).toHaveTextContent("HOLD NO CLEAR ADVANTAGE");
    expect(strategy).toHaveTextContent("CAUTION");
    expect(strategy).toHaveTextContent("PIT WINDOW OPEN");
    expect(within(strategy).getByText("+1")).toBeInTheDocument();
    expect(within(strategy).getByText("+3")).toBeInTheDocument();
    expect(within(strategy).getByText("+5")).toBeInTheDocument();
    expect(strategy).toHaveTextContent("Paired counterfactual state is PIT_WINDOW_OPEN.");
  });

  it.each([
    ["PIT_NOW_SOFT", "ACTIONABLE", "PIT_WINDOW_STRONG"],
    ["EXTEND_2", "CAUTION", "PIT_WINDOW_CLOSED"],
    [null, "INSUFFICIENT_DATA", null],
  ] as const)("renders backend strategy state %s without frontend substitution", async (recommendation, decision_state, window) => {
    const response = detail("ver", 12, { recommendation, decision_state, pit_window: window ? { ...pitDriver("ver", 1, 12).pit_window!, state: window } : null, paired_comparison: window ? pitDriver("ver", 1, 12).paired_comparison : null });
    render(<LivePitWall initialStatus={liveStatus()} initialRace={liveRace()} initialPitwall={pitwall()} loaders={loaders({ driver: vi.fn().mockResolvedValue(response) })}/>);
    const strategy = await screen.findByRole("region", { name: "Experimental live strategy" });
    expect(strategy).toHaveTextContent(recommendation?.replaceAll("_", " ") ?? "INSUFFICIENT DATA");
    expect(strategy).toHaveTextContent(decision_state.replaceAll("_", " "));
    if (window) expect(strategy).toHaveTextContent(window.replaceAll("_", " "));
  });

  it("switches drivers and ignores a late response for the previous selection", async () => {
    let resolveNor!: (value: CurrentPitWall) => void;
    const nor = new Promise<CurrentPitWall>((resolve) => { resolveNor = resolve; });
    const api = loaders({ driver: vi.fn((id: string) => id === "nor" ? nor : Promise.resolve(detail("ver", 12, { recommendation: "EXTEND_3" }))) });
    render(<LivePitWall initialStatus={liveStatus()} initialRace={liveRace()} initialPitwall={pitwall()} loaders={api}/>);
    await screen.findByRole("region", { name: "Pace analysis" });
    fireEvent.click(screen.getByRole("option", { name: /NOR/ }));
    fireEvent.click(screen.getByRole("option", { name: /VER/ }));
    expect(await screen.findByText("EXTEND 3")).toBeInTheDocument();
    await act(async () => { resolveNor(detail("nor", 12, { recommendation: "PIT_NOW_SOFT" })); });
    expect(screen.getByText("ver Driver")).toBeInTheDocument();
    expect(screen.getByText("EXTEND 3")).toBeInTheDocument();
  });

  it("polls once per interval and replaces RaceState and selected detail together", async () => {
    vi.useFakeTimers();
    const api = loaders({ race: vi.fn().mockResolvedValue(liveRace(13)), pitwall: vi.fn().mockResolvedValue(pitwall(13)), driver: vi.fn((id: string) => Promise.resolve(detail(id, 13, { recommendation: "EXTEND_1" }))) });
    render(<LivePitWall initialStatus={liveStatus()} initialRace={liveRace()} initialPitwall={pitwall()} loaders={api} pollIntervalMs={50}/>);
    await act(async () => { await vi.advanceTimersByTimeAsync(50); });
    expect(screen.getByText("Lap 13")).toBeInTheDocument();
    expect(screen.getByText("EXTEND 1")).toBeInTheDocument();
    expect(api.status).toHaveBeenCalledTimes(1);
    expect(api.pitwall).toHaveBeenCalledTimes(1);
    expect(api.driver).toHaveBeenCalledTimes(2);
  });

  it("prevents an older overlapping refresh from replacing a newer RaceState", async () => {
    vi.useFakeTimers();
    let resolveFirst!: (value: LiveStatus) => void;
    const first = new Promise<LiveStatus>((resolve) => { resolveFirst = resolve; });
    const api = loaders({ status: vi.fn().mockReturnValueOnce(first).mockResolvedValue(liveStatus()), race: vi.fn().mockResolvedValue(liveRace(14)), pitwall: vi.fn().mockResolvedValue(pitwall(14)), driver: vi.fn((id: string) => Promise.resolve(detail(id, 14))) });
    render(<LivePitWall initialStatus={liveStatus()} initialRace={liveRace()} initialPitwall={pitwall()} loaders={api} pollIntervalMs={20}/>);
    fireEvent.click(screen.getByRole("button", { name: "Refresh live data" }));
    await act(async () => { await vi.advanceTimersByTimeAsync(20); });
    expect(screen.getByText("Lap 14")).toBeInTheDocument();
    await act(async () => { resolveFirst(liveStatus()); });
    expect(screen.getByText("Lap 14")).toBeInTheDocument();
    expect(api.race).toHaveBeenCalledTimes(1);
  });

  it("preserves timing and current strategy when engineering detail fails", async () => {
    render(<LivePitWall initialStatus={liveStatus()} initialRace={liveRace()} initialPitwall={pitwall()} loaders={loaders({ driver: vi.fn().mockRejectedValue(new Error("analysis failed")) })}/>);
    expect(within(screen.getByRole("listbox", { name: "Drivers" })).getAllByRole("option")).toHaveLength(2);
    expect(await screen.findByText("Selected driver detail · analysis failed")).toBeInTheDocument();
    expect(screen.getByRole("region", { name: "Experimental live strategy" })).toHaveTextContent("HOLD NO CLEAR ADVANTAGE");
    expect(screen.getByText("Engineering analysis is unavailable for this driver state.")).toBeInTheDocument();
  });

  it("retains prior engineering with a stale warning when a later detail refresh fails", async () => {
    const api = loaders({ driver: vi.fn().mockResolvedValueOnce(detail("ver")).mockRejectedValueOnce(new Error("detail offline")) });
    render(<LivePitWall initialStatus={liveStatus()} initialRace={liveRace()} initialPitwall={pitwall()} loaders={api}/>);
    await screen.findByRole("region", { name: "Pace analysis" });
    fireEvent.click(screen.getByRole("button", { name: "Refresh live data" }));
    expect(await screen.findByText("Previous engineering snapshot retained while current driver detail is unavailable.")).toBeInTheDocument();
    expect(within(screen.getByRole("listbox", { name: "Drivers" })).getAllByRole("option")).toHaveLength(2);
    expect(screen.getByRole("region", { name: "Experimental live strategy" })).toHaveTextContent("RaceState freshness · STALE");
  });
});
