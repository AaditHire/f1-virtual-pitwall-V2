import { apiGet } from "./client";
import type { CurrentPitWall, LiveRace, LiveStatus } from "./types";
export const getLiveStatus = () => apiGet<LiveStatus>("/api/v1/live/status");
export const getLiveRace = () => apiGet<LiveRace>("/api/v1/live/race");
export const getLivePitWall = () => apiGet<CurrentPitWall>("/api/v1/live/pitwall");
export const getLiveDriver = (id: string) =>
  apiGet<CurrentPitWall>(`/api/v1/live/pitwall/drivers/${encodeURIComponent(id)}`);
