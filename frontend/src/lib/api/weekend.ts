import { apiGet } from "./client";
import type { RaceControlResponse, WeatherResponse, WeekendState } from "./types";
export const getWeekend = () => apiGet<WeekendState>("/api/v1/weekend/current?timezone=Asia/Kolkata");
export const getWeather = () => apiGet<WeatherResponse>("/api/v1/live/weather?timezone=Asia/Kolkata");
export const getRaceControl = () => apiGet<RaceControlResponse>("/api/v1/live/race-control?timezone=Asia/Kolkata");
