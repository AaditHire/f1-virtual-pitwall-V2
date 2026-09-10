export const fmtTime = (value?: string | null) => value ? new Intl.DateTimeFormat("en-IN", { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit", timeZone: "Asia/Kolkata", hour12: false }).format(new Date(value)).toUpperCase() : "TIME TBC";
export const fmtNewsTime = (value?: string | null) => value ? new Intl.RelativeTimeFormat("en", { numeric: "auto" }).format(-Math.max(1, Math.round((Date.now() - new Date(value).getTime()) / 3_600_000)), "hour") : "TIME UNKNOWN";
export const fmtNumber = (value?: number | null, suffix = "") => value == null ? "—" : `${Number.isInteger(value) ? value : value.toFixed(1)}${suffix}`;
export const driverCode = (driver: { code?: string | null; last_name: string }) => driver.code || driver.last_name.slice(0, 3).toUpperCase();
