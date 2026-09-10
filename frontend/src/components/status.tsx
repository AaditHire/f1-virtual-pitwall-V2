import type { FreshnessState, SessionStatus } from "@/lib/api/types";

export function Status({ value }: { value: SessionStatus | FreshnessState | string }) {
  const key = value.toLowerCase().replaceAll("_", "-");
  return <span className={`status status-${key}`}><i />{value.replaceAll("_", " ")}</span>;
}

export function SurfaceError({ title, detail }: { title: string; detail?: string }) {
  return <section className="surface-error" role="alert"><h1>{title}</h1><p>{detail || "The backend did not return this surface. Other routes may still be available."}</p></section>;
}

export function EmptyRow({ children }: { children: React.ReactNode }) {
  return <div className="empty-row">{children}</div>;
}
