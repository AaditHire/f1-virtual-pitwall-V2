const SERVER_API = (process.env.API_BASE_URL ?? "http://127.0.0.1:8000").replace(/\/$/, "");

export class ApiError extends Error {
  constructor(public status: number, message: string) { super(message); }
}

export async function apiGet<T>(path: string, options: RequestInit = {}): Promise<T> {
  const url = typeof window === "undefined" ? `${SERVER_API}${path}` : `/backend${path}`;
  const response = await fetch(url, { ...options, cache: "no-store", headers: { Accept: "application/json", ...options.headers } });
  if (!response.ok) {
    let detail = `Request failed (${response.status})`;
    try { detail = (await response.json()).detail ?? detail; } catch {}
    throw new ApiError(response.status, detail);
  }
  return response.json() as Promise<T>;
}
