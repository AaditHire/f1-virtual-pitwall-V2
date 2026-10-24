// Plain-English wording for backend codes. The backend vocabulary stays exact in the API; these
// labels are only what a reader sees.
import type { AnalysisConfidence } from "@/lib/api/types";

/** "PIT_WINDOW_OPEN" -> "Pit window open", for codes without a dedicated label. */
export function sentenceCase(value: string) {
  const words = value.replaceAll("_", " ").toLowerCase();
  return words.charAt(0).toUpperCase() + words.slice(1);
}

const COMPOUNDS: Record<string, string> = {
  SOFT: "soft",
  MEDIUM: "medium",
  HARD: "hard",
  INTERMEDIATE: "intermediate",
  WET: "wet",
};

export function plainCompound(value?: string | null) {
  return value ? (COMPOUNDS[value.toUpperCase()] ?? value.toLowerCase()) : null;
}

export function plainConfidence(value: AnalysisConfidence) {
  return value === "INSUFFICIENT" ? "Not enough data" : `${sentenceCase(value)} confidence`;
}

export function plainRecommendation(value?: string | null) {
  if (!value || value === "UNAVAILABLE") return "No call";
  if (value === "INSUFFICIENT_DATA") return "Not enough data";
  if (value.startsWith("HOLD")) return "No clear gain either way";
  const tyre = COMPOUNDS[value.split("_").at(-1) ?? ""];
  if (value.startsWith("PIT_NOW")) return tyre ? `Pit now for ${tyre}s` : "Pit now";
  const laps = /^EXTEND_(\d+)$/.exec(value)?.[1];
  if (value.startsWith("EXTEND"))
    return laps ? `Stay out ${laps} more lap${laps === "1" ? "" : "s"}` : "Stay out";
  return sentenceCase(value);
}

const DECISION_STATES: Record<string, string> = {
  ACTIONABLE: "Confident",
  CAUTION: "Uncertain",
  COARSE_ONLY: "Rough estimate",
  INSUFFICIENT_DATA: "Not enough data",
};

export function plainDecisionState(value: string) {
  return DECISION_STATES[value] ?? sentenceCase(value);
}

const PIT_WINDOWS: Record<string, string> = {
  PIT_WINDOW_STRONG: "Good time to pit",
  PIT_WINDOW_OPEN: "Pitting is an option",
  PIT_WINDOW_CLOSED: "Not a good time to pit",
  PIT_WINDOW_UNCERTAIN: "Unclear",
};

export function plainPitWindow(value?: string | null) {
  return value ? (PIT_WINDOWS[value] ?? sentenceCase(value)) : "—";
}

const TRAFFIC: Record<string, string> = {
  CLEAR_AIR: "Clean air",
  LIGHT_TRAFFIC: "Light traffic",
  MODERATE_TRAFFIC: "Some traffic",
  HEAVY_TRAFFIC: "Heavy traffic",
  UNKNOWN: "Not known",
};

export function plainTraffic(value: string) {
  return TRAFFIC[value] ?? sentenceCase(value);
}

const STATUSES: Record<string, string> = {
  active: "Running",
  in_pit: "In the pits",
  stopped: "Stopped",
  retired: "Retired",
  dns: "Did not start",
  dsq: "Disqualified",
  unknown: "Unknown",
};

export function plainStatus(value: string) {
  return STATUSES[value] ?? sentenceCase(value);
}

/** Drivers out of the race: there is nothing left to analyse for them. */
export function isOut(status: string) {
  return status === "retired" || status === "dns" || status === "dsq";
}
