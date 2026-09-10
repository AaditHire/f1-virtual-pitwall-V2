import type { Event, NextSession, SessionStatus } from "@/lib/api/types";
import { fmtTime } from "@/lib/format";
import { Countdown } from "./countdown";
import { Status } from "./status";

function eventDates(event?: Event | null) {
  if (!event) return "Schedule pending";
  const dates = event.sessions.map((session) => new Date(session.start ?? session.date)).filter((date) => !Number.isNaN(date.getTime()));
  const first = dates[0] ?? new Date(event.race_date);
  const last = dates.at(-1) ?? new Date(event.race_date);
  const day = new Intl.DateTimeFormat("en-IN", { day: "2-digit", timeZone: "Asia/Kolkata" });
  const tail = new Intl.DateTimeFormat("en-IN", { day: "2-digit", month: "short", year: "numeric", timeZone: "Asia/Kolkata" });
  return `${day.format(first)}–${tail.format(last)}`.toUpperCase();
}

function TrackGlyph() {
  return <svg className="track-glyph" viewBox="0 0 240 108" aria-hidden="true"><path d="M18 68c6-24 31-29 50-14l26 20c12 9 29 6 36-7l22-39c8-13 23-17 36-9l31 19c11 7 8 24-5 26l-28 5c-12 2-13 18-2 23l25 11H66c-12 0-20-11-16-22l4-12c4-11-8-21-18-15z" /></svg>;
}

export function EventFeature({ event, nextSession, status, mode = "home", weekendFormat }: { event?: Event | null; nextSession?: NextSession | null; status: SessionStatus | string; mode?: "home" | "weekend"; weekendFormat?: string }) {
  const location = [event?.circuit.locality, event?.circuit.country].filter(Boolean).join(", ") || "Location pending";
  const eventName = event?.name ?? "Current race weekend";
  const grandPrixIndex = eventName.toLowerCase().lastIndexOf("grand prix");
  const nameLead = grandPrixIndex > 0 ? eventName.slice(0, grandPrixIndex).trim() : eventName;
  const nameAccent = grandPrixIndex > 0 ? eventName.slice(grandPrixIndex) : null;
  return <section className={`event-feature event-feature-${mode}`}>
    <div className="event-copy">
      <span className="round-label">Round {event?.round ?? "—"}</span>
      <h1><span>{nameLead}</span>{nameAccent ? <em> {nameAccent}</em> : null}</h1>
      <p className="event-circuit">{event?.circuit.name ?? "Circuit to be confirmed"}</p>
      <div className="event-detail"><span>{location}</span><span>{eventDates(event)}</span>{weekendFormat ? <span>{weekendFormat} weekend</span> : null}</div>
      <TrackGlyph />
    </div>
    <div className="event-image" aria-hidden="true"><span className="event-round-watermark">{event?.round ?? "—"}</span></div>
    <div className="event-session">
      {mode === "weekend" ? <div className="event-status"><span>Event status</span><Status value={status} /></div> : null}
      <div><span>Next session</span><strong>{nextSession?.session.name ?? "Schedule pending"}</strong><time>{fmtTime(nextSession?.session.start)}</time></div>
      {mode === "home" ? <div className="hero-countdown"><span>Time until session</span><Countdown target={nextSession?.session.start} compact /></div> : null}
    </div>
  </section>;
}
