"use client";

import { useSyncExternalStore } from "react";
import { fmtDateRange, fmtTime, zoneName } from "@/lib/format";

const noSubscribe = () => () => {};
const browserZone = () => Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";

// The server cannot know the viewer's zone, so server HTML and hydration use UTC and the
// browser re-renders in its own zone straight after.
export const useTimeZone = () => useSyncExternalStore(noSubscribe, browserZone, () => "UTC");

export function LocalTime({ value, dateTime }: { value?: string | null; dateTime?: string }) {
  const zone = useTimeZone();
  return <time dateTime={dateTime ?? value ?? undefined}>{fmtTime(value, zone)}</time>;
}

export function LocalDateRange({ first, last }: { first: Date; last: Date }) {
  return <span>{fmtDateRange(first, last, useTimeZone())}</span>;
}

export function ZoneLabel({ full = false }: { full?: boolean }) {
  const zone = useTimeZone();
  return <>All times {full ? zone : zoneName(zone)}</>;
}
