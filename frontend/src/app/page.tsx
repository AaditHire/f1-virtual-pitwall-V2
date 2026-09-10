import Link from "next/link";
import { Countdown } from "@/components/countdown";
import { EditorialNews } from "@/components/editorial-news";
import { EventFeature } from "@/components/event-feature";
import { SessionTimeline } from "@/components/session-timeline";
import { ConstructorStandings, DriverStandings } from "@/components/standings-table";
import { EmptyRow, Status, SurfaceError } from "@/components/status";
import { getHome } from "@/lib/api/home";

export const dynamic = "force-dynamic";

export default async function HomePage() {
  const result = await getHome().then(data => ({ data })).catch((error: Error) => ({ error }));
  if (!("data" in result)) return <div className="page"><SurfaceError title="Pit wall unavailable" detail={result.error.message} /></div>;
  const home = result.data;
  const weekend = home.current_weekend;
  const event = weekend?.event ?? home.current_or_next_event;
  const sessions = weekend?.session_schedule?.length ? weekend.session_schedule : home.weekend_schedule;
  return <div className="page home-page">
    <EventFeature event={event} nextSession={home.next_session} status={weekend?.status ?? home.weekend_status} />
    <section className="section timeline-section"><div className="section-header"><h2>Weekend sessions</h2><Link className="text-link" href="/weekend">All times IST · Full weekend →</Link></div>{sessions.length ? <SessionTimeline sessions={sessions} /> : <EmptyRow>Session schedule is not available yet.</EmptyRow>}</section>
    <div className="home-intelligence">
      <section className="home-news"><div className="section-header"><h2>Latest news</h2><Link className="text-link" href="/news">All news →</Link></div>{home.latest_news.length ? <EditorialNews articles={home.latest_news} /> : <EmptyRow>No news available.</EmptyRow>}</section>
      <div className="championship-band">
        <section><div className="section-header"><h2>Drivers&apos; championship</h2><Link className="text-link" href="/standings">Top 10 →</Link></div>{home.driver_standings_top.length ? <DriverStandings rows={home.driver_standings_top} compact /> : <EmptyRow>No standings returned.</EmptyRow>}</section>
        <section><div className="section-header"><h2>Constructors&apos; championship</h2><Link className="text-link" href="/standings">Top 10 →</Link></div>{home.constructor_standings_top.length ? <ConstructorStandings rows={home.constructor_standings_top} compact /> : <EmptyRow>No standings returned.</EmptyRow>}</section>
      </div>
    </div>
    <section className="live-entry"><div className="live-entry-brand"><i/><span><strong>Live pit wall</strong><small>Real insights. Deeper racing.</small></span></div><div><Status value={home.live_status?.session_status ?? "UNKNOWN"}/><strong>{home.live_status?.live ? "Live telemetry available" : "No session live"}</strong></div><div className="live-next"><span>Next up</span><b>{home.next_session?.session.name ?? "Schedule pending"}</b><Countdown target={home.next_session?.session.start} compact /></div><Link className="button-link button-primary" href="/pitwall">Open pit wall <span>→</span></Link></section>
  </div>;
}

