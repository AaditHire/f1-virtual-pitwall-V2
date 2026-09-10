import { apiGet } from "./client";
import type { Home } from "./types";
export const getHome = () => apiGet<Home>("/api/v1/home?timezone=Asia/Kolkata");
