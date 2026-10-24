import { describe, expect, it } from "vitest";
import type { DriverAnalysis, DriverRaceState, PitWallDriver } from "@/lib/api/types";
import { plainRecommendation } from "@/lib/plain";
import { summaryLines } from "./driver-summary";

const car = (
  code: string,
  position: number,
  extra: Omit<Partial<DriverRaceState>, "constructor"> = {},
): DriverRaceState => ({
  driver: {
    id: code,
    number: position,
    code,
    first_name: code,
    last_name: `${code}son`,
    full_name: `${code} ${code}son`,
  },
  constructor: { id: "team", name: "Team" },
  position,
  gap_to_ahead: position === 1 ? null : 1.25,
  compound: "HARD",
  tyre_age: 34,
  pit_stops_completed: 1,
  status: "active",
  quality: { confidence: "observed", fields: {}, warnings: [] },
  ...extra,
});

const evidence = { confidence: "MEDIUM" as const, method: "test", components: {}, warnings: [] };

function analysis(driver: DriverRaceState, empty = false): DriverAnalysis {
  return {
    year: 2021,
    round: 22,
    lap: 48,
    cutoff_seconds: 1,
    driver,
    recent_pace: { ...evidence, seconds: empty ? null : 87.553, lap_numbers: [], sample_count: 3 },
    tyres: { ...evidence, driver_id: driver.driver.id, sample_count: 0 },
    pit_loss: { ...evidence, total_seconds: empty ? null : 21.8, sample_count: 17 },
    rejoin: {
      ...evidence,
      driver_id: driver.driver.id,
      position_range: [2, 2],
      ahead_id: "VER",
      nearby_drivers: [],
      traffic: "CLEAR_AIR",
    },
    traffic: { ...evidence, driver_id: driver.driver.id, status: empty ? "UNKNOWN" : "CLEAR_AIR" },
    pit_window: {
      ...evidence,
      current_lap: 48,
      rejoin: { ...evidence, driver_id: driver.driver.id, nearby_drivers: [], traffic: "UNKNOWN" },
      traffic_risk: "UNKNOWN",
    },
  };
}

const strategy = (recommendation: string | null) =>
  ({
    recommendation,
    decision_state: recommendation ? "COARSE_ONLY" : "INSUFFICIENT_DATA",
  }) as PitWallDriver;

describe("summaryLines", () => {
  const leader = car("HAM", 1);
  const field = [leader, car("VER", 2, { gap_to_ahead: 11.2 })];

  it("describes a running car in plain sentences", () => {
    expect(summaryLines(leader, field, analysis(leader), strategy("EXTEND"))).toEqual([
      "HAMson leads by 11.2s.",
      "On hard tyres that are 34 laps old.",
      "Recent laps are around 1:27.553.",
      "A pit stop now costs about 21.8s and would rejoin in P2, behind VERson.",
      "Running in clean air.",
      "Strategy model: stay out (rough estimate).",
    ]);
  });

  it("names the car ahead and says why estimates are missing", () => {
    const second = field[1];
    expect(summaryLines(second, field, analysis(second, true), strategy(null))).toEqual([
      "VERson is P2, 11.2s behind HAMson.",
      "On hard tyres that are 34 laps old.",
      "Not enough clean laps yet to judge pace.",
      "Too few pit stops so far to say what a stop would cost.",
      "Strategy model: not enough data to make a call yet.",
    ]);
  });

  it("explains a retired car instead of listing empty estimates", () => {
    const retired = car("RAI", 18, { status: "retired", laps_completed: 25 });
    expect(summaryLines(retired, field, analysis(retired, true), strategy(null))).toEqual([
      "RAIson retired from the race after 25 laps, so there is nothing left to analyse.",
    ]);
  });

  it("reports lapped cars by laps down", () => {
    const lapped = car("LAT", 16, { laps_behind: 1 });
    expect(summaryLines(lapped, field, null, null)).toEqual([
      "LATson is P16, 1 lap down.",
      "On hard tyres that are 34 laps old.",
    ]);
  });
});

describe("plainRecommendation", () => {
  it.each([
    ["PIT_NOW", "Pit now"],
    ["PIT_NOW_SOFT", "Pit now for softs"],
    ["EXTEND", "Stay out"],
    ["EXTEND_1", "Stay out 1 more lap"],
    ["EXTEND_5", "Stay out 5 more laps"],
    ["HOLD_NO_CLEAR_ADVANTAGE", "No clear gain either way"],
    ["INSUFFICIENT_DATA", "Not enough data"],
    [null, "No call"],
  ])("%s reads as %s", (code, text) => {
    expect(plainRecommendation(code)).toBe(text);
  });
});
