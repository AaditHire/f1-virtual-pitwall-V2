import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { ReplayWorkspace, type ReplayLoaders } from "./replay-workspace";
import type { DriverRaceState, Event, ReplayRaceState } from "@/lib/api/types";

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

describe("ReplayWorkspace", () => {
  it("loads the catalogue and renders the full dynamic grid", async () => {
    render(<ReplayWorkspace loaders={loaders()} />);
    expect(screen.getByRole("status", { name: "Loading historical replay" })).toBeInTheDocument();
    expect(await screen.findByRole("heading", { name: "Race timing" })).toBeInTheDocument();
    expect(within(screen.getByRole("listbox", { name: "Historical drivers" })).getAllByRole("option")).toHaveLength(2);
    expect(screen.getByText("Lap 3 / 3 · 2 participants")).toBeInTheDocument();
  });

  it("supports previous, direct, scrubbed lap navigation and driver selection", async () => {
    const api = loaders();
    render(<ReplayWorkspace loaders={api} />);
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
    render(<ReplayWorkspace loaders={api} />);
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
    render(<ReplayWorkspace loaders={api} />);
    await screen.findByText("Lap 3 / 3 · 2 participants");
    fireEvent.click(screen.getByRole("button", { name: "Previous lap" }));
    expect(await screen.findByText("Current lap retained until the causal snapshot is ready.")).toBeInTheDocument();
    expect(within(screen.getByRole("listbox", { name: "Historical drivers" })).getAllByRole("option")).toHaveLength(2);
    release?.(state(2));
    expect(await screen.findByText("Lap 2 / 3 · 2 participants")).toBeInTheDocument();
  });

  it("renders backend errors and unavailable driver data honestly", async () => {
    const first = render(<ReplayWorkspace loaders={loaders({ seasons: vi.fn().mockRejectedValue(new Error("archive offline")) })} />);
    expect((await screen.findAllByRole("alert")).some((alert) => alert.textContent?.includes("archive offline"))).toBe(true);
    first.unmount();
    render(<ReplayWorkspace loaders={loaders({ state: vi.fn().mockResolvedValue(state(3, [])) })} />);
    expect(await screen.findByText("No drivers were returned for this replay lap.")).toBeInTheDocument();
    expect(screen.getByText("No driver state is available.")).toBeInTheDocument();
  });
});
