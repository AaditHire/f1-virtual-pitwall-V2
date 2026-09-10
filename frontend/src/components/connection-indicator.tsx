"use client";

import { useEffect, useState } from "react";

export function ConnectionIndicator() {
  const [online, setOnline] = useState<boolean | null>(null);
  useEffect(() => {
    let alive = true;
    const check = async () => {
      try { const response = await fetch("/backend/health", { cache: "no-store" }); if (alive) setOnline(response.ok); }
      catch { if (alive) setOnline(false); }
    };
    void check();
    const timer = window.setInterval(check, 30_000);
    return () => { alive = false; window.clearInterval(timer); };
  }, []);
  return <div className="connection" aria-live="polite"><i className={online ? "ok" : online === false ? "bad" : "idle"} />{online ? "Backend online" : online === false ? "Backend offline" : "Checking backend"}</div>;
}
