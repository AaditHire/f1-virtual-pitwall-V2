"use client";

import { useEffect, useState } from "react";

function parts(target: string) {
  const seconds = Math.max(0, Math.floor((new Date(target).getTime() - Date.now()) / 1000));
  return { days: Math.floor(seconds / 86400), hours: Math.floor(seconds % 86400 / 3600), minutes: Math.floor(seconds % 3600 / 60), seconds: seconds % 60 };
}
export function Countdown({ target, compact = false }: { target?: string | null; compact?: boolean }) {
  const [value, setValue] = useState(() => target ? parts(target) : null);
  useEffect(() => {
    if (!target) return;
    const tick = () => setValue(parts(target)); tick();
    const timer = window.setInterval(tick, 1000); return () => window.clearInterval(timer);
  }, [target]);
  if (!value) return <span>TBC</span>;
  const output = `${value.days ? `${String(value.days).padStart(2, "0")}D ` : ""}${String(value.hours).padStart(2, "0")}H ${String(value.minutes).padStart(2, "0")}M${compact ? "" : ` ${String(value.seconds).padStart(2, "0")}S`}`;
  return <time dateTime={target ?? undefined}>{output}</time>;
}
