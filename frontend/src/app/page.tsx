import Link from "next/link";
import { Countdown } from "@/components/countdown";
import { SessionTimeline } from "@/components/session-timeline";
import { ConstructorStandings, DriverStandings } from "@/components/standings-table";
import { EmptyRow, Status, SurfaceError } from "@/components/status";
import { getHome } from "@/lib/api/home";
import { fmtNewsTime, fmtTime } from "@/lib/format";

export const dynamic = "force-dynamic";

export default async function HomePage() {
  const result = await getHome().then(data => ({ data })).catch((error: Error) => ({ error }));
  if (!("data" in result)) return <div className="page"><SurfaceError title="Pit wall unavailable" detail={result.error.message} /></div>;
  const home = result.data;
  const weekend = home.current_weekend;
  const event = weekend?.event ?? home.current_or_next_event;
  const sessions = weekend?.session_schedule?.length ? weekend.session_schedule : home.weekend_schedule;
  return <div className="page">
    <section className="event-band">
      <div className="event-identity"><span className="eyeline">Current weekend</span><h1 className="event-title">{event?.name ?? "Season control"}</h1><div className="event-subtitle">{event?.circuit.name ?? "Awaiting the next event"}</div><div className="event-meta"><span>{event?.circuit.country ?? "FIA Formula One"}</span><span>{event ? `Round ${event.round}` : home.active_season?.year}</span><Status value={weekend?.status ?? home.weekend_status} /></div></div>
      <div className="next-session"><span className="eyeline">Next session</span><strong>{home.next_session?.session.name ?? "Schedule pending"}</strong><span className="session-time">{fmtTime(home.next_session?.session.start)}</span></div>
      <div className="countdown-panel"><span className="eyeline">Lights out in</span><Countdown target={home.next_session?.session.start} /></div>
    </section>
    <section className="section"><div className="section-header"><h2>Weekend sequence</h2><Link className="text-link" href="/weekend">Full weekend →</Link></div>{sessions.length ? <SessionTimeline sessions={sessions} /> : <EmptyRow>Session schedule is not available yet.</EmptyRow>}</section>
    <div className="home-columns">
      <section><div className="section-header"><h2>Drivers</h2><Link className="text-link" href="/standings">All →</Link></div>{home.driver_standings_top.length ? <DriverStandings rows={home.driver_standings_top} compact /> : <EmptyRow>No standings returned.</EmptyRow>}</section>
      <section><div className="section-header"><h2>Constructors</h2><Link className="text-link" href="/standings">All →</Link></div>{home.constructor_standings_top.length ? <ConstructorStandings rows={home.constructor_standings_top} compact /> : <EmptyRow>No standings returned.</EmptyRow>}</section>
      <section><div className="section-header"><h2>Intelligence wire</h2><Link className="text-link" href="/news">All news →</Link></div>{home.latest_news.length ? <ul className="news-list">{home.latest_news.slice(0,5).map(a => <li key={a.url}><a href={a.url} target="_blank" rel="noreferrer"><span className="news-time">{fmtNewsTime(a.published_at)}</span><span className="news-title">{a.headline}<small className="news-source">{a.source}</small></span></a></li>)}</ul> : <EmptyRow>No news available.</EmptyRow>}<div className="live-summary"><Status value={home.live_status?.session_status ?? "UNKNOWN"} /><strong>{home.live_status?.live ? "Live telemetry available" : "No session live"}</strong><p>{home.live_status?.reason ?? "The pit wall will activate when live timing is available."}</p><Link className="button-link" href="/pitwall">Open pit wall</Link></div></section>
    </div>
  </div>;
}

