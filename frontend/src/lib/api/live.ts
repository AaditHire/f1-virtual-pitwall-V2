import { apiGet } from "./client";
import type { CurrentPitWall, LiveRace, LiveStatus } from "./types";
export const getLiveStatus = () => apiGet<LiveStatus>("/api/v1/live/status?timezone=Asia/Kolkata");
export const getLiveRace = () => apiGet<LiveRace>("/api/v1/live/race?timezone=Asia/Kolkata");
export const getLivePitWall = () => apiGet<CurrentPitWall>("/api/v1/live/pitwall?timezone=Asia/Kolkata");
export const getLiveDriver = (id: string) => apiGet<CurrentPitWall>(`/api/v1/live/pitwall/drivers/${encodeURIComponent(id)}?timezone=Asia/Kolkata`);
