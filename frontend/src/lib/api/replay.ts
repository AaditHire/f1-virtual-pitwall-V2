import { apiGet } from "./client";
import type { DriverAnalysis, Event, PairAnalysis, PitWallDriver, ReplayAvailableLaps, ReplayRaceState, Season } from "./types";

const immutableReplayCache = new Map<string, Promise<unknown>>();
const MAX_REPLAY_CACHE_ENTRIES = 256;

function immutableGet<T>(path: string): Promise<T> {
  const cached = immutableReplayCache.get(path) as Promise<T> | undefined;
  if (cached) return cached;
  const request = apiGet<T>(path).catch((error) => {
    immutableReplayCache.delete(path);
    throw error;
  });
  if (immutableReplayCache.size >= MAX_REPLAY_CACHE_ENTRIES) {
    const oldest = immutableReplayCache.keys().next().value;
    if (oldest) immutableReplayCache.delete(oldest);
  }
  immutableReplayCache.set(path, request);
  return request;
}

export const getReplaySeasons = () => apiGet<Season[]>("/api/v1/seasons");
export const getReplayEvents = (year: number) => apiGet<Event[]>(`/api/v1/seasons/${year}/calendar?timezone=Asia/Kolkata`);
export const getReplayLaps = (year: number, round: number) => immutableGet<ReplayAvailableLaps>(`/api/v1/replay/${year}/${round}/laps`);
export const getReplayState = (year: number, round: number, lap: number) => immutableGet<ReplayRaceState>(`/api/v1/replay/${year}/${round}/${lap}`);
export const getReplayDriverAnalysis = (year: number, round: number, lap: number, driverId: string) => immutableGet<DriverAnalysis>(`/api/v1/analysis/${year}/${round}/${lap}/drivers/${encodeURIComponent(driverId)}`);
export const getReplayUndercut = (year: number, round: number, lap: number, driverId: string, targetId: string) => {
  const query = new URLSearchParams({ attacker: driverId, target: targetId });
  return immutableGet<PairAnalysis>(`/api/v1/analysis/${year}/${round}/${lap}/undercut?${query}`);
};
export const getReplayOvercut = (year: number, round: number, lap: number, driverId: string, targetId: string) => {
  const query = new URLSearchParams({ driver: driverId, target: targetId });
  return immutableGet<PairAnalysis>(`/api/v1/analysis/${year}/${round}/${lap}/overcut?${query}`);
};
export const getReplayStrategy = (year: number, round: number, lap: number, driverId: string) => immutableGet<PitWallDriver>(`/api/v1/pitwall/${year}/${round}/${lap}/drivers/${encodeURIComponent(driverId)}?trajectory_count=100`);
