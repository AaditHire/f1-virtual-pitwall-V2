import type { Session, SessionState } from "@/lib/api/types";
import { fmtTime } from "@/lib/format";
import { Status } from "./status";

const short: Record<string, string> = { "Practice 1": "FP1", "Practice 2": "FP2", "Practice 3": "FP3", "Sprint Qualifying": "SQ", Sprint: "SPRINT", Qualifying: "QUALI", Race: "RACE" };

export function SessionTimeline({ sessions }: { sessions: SessionState[] | Session[] }) {
  const normalized: SessionState[] = sessions.map((item) => "session" in item ? item : ({ session: item, status: "UNKNOWN" }));
  return <div className="timeline" role="list" aria-label="Weekend sessions">
    {normalized.map(({ session, status }) => <div className={`timeline-stop timeline-${status.toLowerCase()}`} role="listitem" key={`${session.name}-${session.date}`}>
      <span className="timeline-marker" aria-hidden="true" />
      <strong>{short[session.name] ?? session.name.toUpperCase()}</strong>
      <time dateTime={session.start ?? session.date}>{fmtTime(session.start)}</time>
      <Status value={status} />
    </div>)}
  </div>;
}
