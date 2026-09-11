import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { ReplayWorkspace, type ReplayLoaders } from "./replay-workspace";
import type { ReplayAnalysisLoaders } from "./replay-engineering";
import type { DriverAnalysis, DriverRaceState, Event, PairAnalysis, ReplayRaceState } from "@/lib/api/types";

const event = (year: number, round: number, name: string): Event => ({
  year, round, name, race_date: `${year}-03-01`, warnings: [], sessions: [],
  circuit: { id: `c${round}`, name: `${name} Circuit`, locality: "Test", country: "Test" },
});

const driver = (id: string, position: number): DriverRaceState => ({
  driver: { id, number: position, code: id.toUpperCase(), first_name: id, last_name: "Driver", full_name: `${id} Driver` },
  constructor: { id: `team-${id}`, name: `${id} Team` }, position, grid_position: position, laps_completed: 3,
  gap_to_leader: position - 1, gap_to_ahead: position === 1 ? null : 1, compound: "MEDIUM", tyre_age: 3,
  stint_number: 1, pit_stops_completed: 0, last_lap_time: 91, recent_clean_pace: 91.2, status: "active",
  quality: { confidence: "observed", fields: {}, warnings: [] },
});

const state = (lap: number, drivers = [driver("ver", 1), driver("nor", 2)]): ReplayRaceState => ({
  event: event(2024, 1, "Bahrain Grand Prix"),
  session: { type: "RACE", name: "Race", date: "2024-03-01", source: "test", cancelled: false },
  current_lap: lap, total_scheduled_laps: 57, session_time_seconds: lap * 90, elapsed_race_seconds: lap * 90,
  drivers, track: { track_status: "GREEN" }, quality: { confidence: "observed", fields: {}, warnings: [] }, source: "test",
});

function loaders(overrides: Partial<ReplayLoaders> = {}): ReplayLoaders {
  return {
    seasons: vi.fn().mockResolvedValue([{ year: 2023, source: "test" }, { year: 2024, source: "test" }]),
    events: vi.fn((year: number) => Promise.resolve([event(year, 1, `${year} Grand Prix`)])),
    laps: vi.fn((year: number, round: number) => Promise.resolve({ year, round, laps: [1, 2, 3], participants: 2, definition: "leader lap" })),
    state: vi.fn((year: number, round: number, lap: number) => Promise.resolve({ ...state(lap), event: event(year, round, `${year} Grand Prix`) })),
    ...overrides,
  };
}

const evidence = (confidence: "HIGH" | "MEDIUM" | "LOW" | "INSUFFICIENT" = "MEDIUM") => ({ confidence, method: "phase-3-test", components: {}, warnings: [] });

function analysis(driverId: string, lap: number, unavailable = false): DriverAnalysis {
  const chosen = driver(driverId, driverId === "ver" ? 1 : 2);
  return {
    year: 2024, round: 1, lap, cutoff_seconds: lap * 90, driver: chosen,
    recent_pace: { ...evidence(unavailable ? "INSUFFICIENT" : "MEDIUM"), seconds: unavailable ? null : 90 + lap / 10, lap_numbers: unavailable ? [] : [lap - 2, lap - 1, lap], sample_count: unavailable ? 0 : 3 },
    tyres: { ...evidence("LOW"), driver_id: driverId, compound: "MEDIUM", stint_number: 1, tyre_age: lap, reference_coverage: unavailable ? null : .8, sample_count: unavailable ? 0 : 5 },
    pit_loss: { ...evidence(unavailable ? "INSUFFICIENT" : "MEDIUM"), total_seconds: unavailable ? null : 21.45, sample_count: unavailable ? 0 : 4 },
    rejoin: { ...evidence(unavailable ? "INSUFFICIENT" : "MEDIUM"), driver_id: driverId, projected_position: unavailable ? null : 4, position_range: unavailable ? null : [3, 5], gap_ahead: unavailable ? null : 1.2, gap_behind: unavailable ? null : 2.1, ahead_id: unavailable ? null : "ver", behind_id: unavailable ? null : "nor", nearby_drivers: unavailable ? [] : ["ver", "nor"], traffic: unavailable ? "UNKNOWN" : "MODERATE_TRAFFIC" },
    traffic: { ...evidence(unavailable ? "INSUFFICIENT" : "MEDIUM"), driver_id: driverId, status: unavailable ? "UNKNOWN" : "LIGHT_TRAFFIC", ahead_id: unavailable ? null : "ver", behind_id: unavailable ? null : "nor", gap_ahead: unavailable ? null : 1.3, gap_behind: unavailable ? null : 2.4, clear_air: unavailable ? null : false, slower_car_blockage: unavailable ? null : true, rejoin_density: unavailable ? null : 2 },
    pit_window: { ...evidence("LOW"), current_lap: lap, rejoin: { ...evidence("LOW"), driver_id: driverId, nearby_drivers: [], traffic: "UNKNOWN" }, clear_air_opportunity: null, traffic_risk: "UNKNOWN" },
  };
}

function pair(kind: "undercut" | "overcut", driverId: string, targetId: string): PairAnalysis {
  return { ...evidence("LOW"), kind, driver_id: driverId, target_id: targetId, current_gap: 1.2, required_gain: 2.4, estimated_fresh_tyre_gain: .8, traffic_penalty: .3, estimated_margin: -.7, opportunity: "MARGINAL", conditions_required: ["One clean lap required"] };
}

function analysisLoaders(overrides: Partial<ReplayAnalysisLoaders> = {}): ReplayAnalysisLoaders {
  return {
    driver: vi.fn((_year, _round, lap, driverId) => Promise.resolve(analysis(driverId, lap))),
    undercut: vi.fn((_year, _round, _lap, driverId, targetId) => Promise.resolve(pair("undercut", driverId, targetId))),
    overcut: vi.fn((_year, _round, _lap, driverId, targetId) => Promise.resolve(pair("overcut", driverId, targetId))),
    ...overrides,
  };
}

describe("ReplayWorkspace", () => {
  it("loads the catalogue and renders the full dynamic grid", async () => {
    render(<ReplayWorkspace loaders={loaders()} analysisLoaders={analysisLoaders()} />);
    expect(screen.getByRole("status", { name: "Loading historical replay" })).toBeInTheDocument();
    expect(await screen.findByRole("heading", { name: "Race timing" })).toBeInTheDocument();
    expect(within(screen.getByRole("listbox", { name: "Historical drivers" })).getAllByRole("option")).toHaveLength(2);
    expect(screen.getByText("Lap 3 / 3 · 2 participants")).toBeInTheDocument();
  });

  it("supports previous, direct, scrubbed lap navigation and driver selection", async () => {
    const api = loaders();
    render(<ReplayWorkspace loaders={api} analysisLoaders={analysisLoaders()} />);
    await screen.findByText("Lap 3 / 3 · 2 participants");
    fireEvent.click(screen.getByRole("button", { name: "Previous lap" }));
    expect(await screen.findByText("Lap 2 / 3 · 2 participants")).toBeInTheDocument();
    const direct = screen.getByRole("spinbutton", { name: "Direct lap selection" });
    fireEvent.change(direct, { target: { value: "1" } });
    fireEvent.keyDown(direct, { key: "Enter" });
    expect(await screen.findByText("Lap 1 / 3 · 2 participants")).toBeInTheDocument();
    const scrubber = screen.getByRole("slider", { name: "Leader lap scrubber" });
    fireEvent.change(scrubber, { target: { value: "3" } });
    fireEvent.pointerUp(scrubber);
    expect(await screen.findByText("Lap 3 / 3 · 2 participants", {}, { timeout: 1200 })).toBeInTheDocument();
    fireEvent.click(within(screen.getByRole("listbox", { name: "Historical drivers" })).getByRole("option", { name: /NOR/ }));
    expect(within(screen.getByRole("listbox", { name: "Historical drivers" })).getByRole("option", { name: /NOR/ })).toHaveAttribute("aria-selected", "true");
  });

  it("changes season and event using backend-provided options", async () => {
    const api = loaders();
    render(<ReplayWorkspace loaders={api} analysisLoaders={analysisLoaders()} />);
    await screen.findByText("Lap 3 / 3 · 2 participants");
    fireEvent.change(screen.getByRole("combobox", { name: "Season" }), { target: { value: "2023" } });
    await waitFor(() => expect(api.events).toHaveBeenCalledWith(2023));
    await waitFor(() => expect(screen.getByRole("combobox", { name: "Event" })).toHaveValue("1"));
    expect(screen.getAllByText("2023 Grand Prix").length).toBeGreaterThan(0);
  });

  it("retains the current grid while an adjacent lap is loading", async () => {
    let release: ((value: ReplayRaceState) => void) | undefined;
    const pending = new Promise<ReplayRaceState>((resolve) => { release = resolve; });
    const api = loaders({ state: vi.fn((_year: number, _round: number, lap: number) => lap === 2 ? pending : Promise.resolve(state(lap))) });
    render(<ReplayWorkspace loaders={api} analysisLoaders={analysisLoaders()} />);
    await screen.findByText("Lap 3 / 3 · 2 participants");
    fireEvent.click(screen.getByRole("button", { name: "Previous lap" }));
    expect(await screen.findByText("Current lap retained until the causal snapshot is ready.")).toBeInTheDocument();
    expect(within(screen.getByRole("listbox", { name: "Historical drivers" })).getAllByRole("option")).toHaveLength(2);
    release?.(state(2));
    expect(await screen.findByText("Lap 2 / 3 · 2 participants")).toBeInTheDocument();
  });

  it("renders backend errors and unavailable driver data honestly", async () => {
    const first = render(<ReplayWorkspace loaders={loaders({ seasons: vi.fn().mockRejectedValue(new Error("archive offline")) })} analysisLoaders={analysisLoaders()} />);
    expect((await screen.findAllByRole("alert")).some((alert) => alert.textContent?.includes("archive offline"))).toBe(true);
    first.unmount();
    render(<ReplayWorkspace loaders={loaders({ state: vi.fn().mockResolvedValue(state(3, [])) })} analysisLoaders={analysisLoaders()} />);
    expect(await screen.findByText("No drivers were returned for this replay lap.")).toBeInTheDocument();
    expect(screen.getByText("No driver state is available.")).toBeInTheDocument();
  });

  it("renders Phase 3 engineering analysis for the selected driver", async () => {
    render(<ReplayWorkspace loaders={loaders()} analysisLoaders={analysisLoaders()} />);
    expect(await screen.findByRole("heading", { name: "Driver engineering" })).toBeInTheDocument();
    expect(await screen.findByText("1:30.300")).toBeInTheDocument();
    expect(screen.getByText("P3–P5")).toBeInTheDocument();
    expect(screen.getByText("LIGHT TRAFFIC")).toBeInTheDocument();
    expect(screen.getAllByText("LOW CONFIDENCE").length).toBeGreaterThan(0);
  });

  it("changes engineering analysis with driver and leader lap", async () => {
    const engineering = analysisLoaders();
    render(<ReplayWorkspace loaders={loaders()} analysisLoaders={engineering} />);
    await screen.findByText("1:30.300");
    fireEvent.click(within(screen.getByRole("listbox", { name: "Historical drivers" })).getByRole("option", { name: /NOR/ }));
    await waitFor(() => expect(engineering.driver).toHaveBeenCalledWith(2024, 1, 3, "nor"));
    expect((await screen.findAllByText("nor Driver")).length).toBeGreaterThan(0);
    fireEvent.click(screen.getByRole("button", { name: "Previous lap" }));
    await waitFor(() => expect(engineering.driver).toHaveBeenCalledWith(2024, 1, 2, "nor"));
    expect(await screen.findByText("1:30.200")).toBeInTheDocument();
  });

  it("renders insufficient and unavailable analysis fields without invented values", async () => {
    const engineering = analysisLoaders({ driver: vi.fn((_year, _round, lap, driverId) => Promise.resolve(analysis(driverId, lap, true))) });
    render(<ReplayWorkspace loaders={loaders()} analysisLoaders={engineering} />);
    expect(await screen.findByText("Insufficient clean laps are available at this cutoff.")).toBeInTheDocument();
    expect(screen.getAllByText("INSUFFICIENT DATA").length).toBeGreaterThan(0);
    expect(screen.getByText("Diagnostic evidence only · no tyre-life or degradation forecast.")).toBeInTheDocument();
    expect(screen.getAllByText("Unavailable").length).toBeGreaterThan(1);
  });

  it("keeps RaceState usable when engineering analysis fails", async () => {
    const engineering = analysisLoaders({ driver: vi.fn().mockRejectedValue(new Error("analysis service offline")), undercut: vi.fn().mockRejectedValue(new Error("pair offline")) });
    render(<ReplayWorkspace loaders={loaders()} analysisLoaders={engineering} />);
    expect(await screen.findByText("analysis service offline")).toBeInTheDocument();
    expect(within(screen.getByRole("listbox", { name: "Historical drivers" })).getAllByRole("option")).toHaveLength(2);
    expect(screen.getByText("Lap 3 / 3 · 2 participants")).toBeInTheDocument();
  });

  it("loads low-confidence undercut and overcut context for a changed comparison car", async () => {
    const third = driver("ham", 3);
    const api = loaders({ state: vi.fn((_year, _round, lap) => Promise.resolve(state(lap, [driver("ver", 1), driver("nor", 2), third]))) });
    const engineering = analysisLoaders();
    render(<ReplayWorkspace loaders={api} analysisLoaders={engineering} />);
    const comparison = await screen.findByRole("combobox", { name: "Comparison car" });
    fireEvent.change(comparison, { target: { value: "ham" } });
    await waitFor(() => expect(engineering.undercut).toHaveBeenCalledWith(2024, 1, 3, "ver", "ham"));
    expect(screen.getAllByText("Supporting context only")).toHaveLength(2);
    expect(screen.getByText("Sparse historical coverage and material margin error. Context, not a recommendation.")).toBeInTheDocument();
  });
});
