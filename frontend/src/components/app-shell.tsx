"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { Menu, X } from "lucide-react";
import { useState } from "react";
import { ConnectionIndicator } from "./connection-indicator";

const links = [
  ["Home", "/"], ["Weekend", "/weekend"], ["Live Pit Wall", "/pitwall"],
  ["Standings", "/standings"], ["News", "/news"],
] as const;

export function AppShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const [open, setOpen] = useState(false);
  return <div className="app-shell">
    <header className="topbar">
      <Link className="brand" href="/" onClick={() => setOpen(false)} aria-label="Virtual Pit Wall home"><span>VPW</span><b>Virtual Pit Wall</b></Link>
      <button className="menu-button" onClick={() => setOpen(!open)} aria-expanded={open} aria-label="Toggle navigation">{open ? <X /> : <Menu />}</button>
      <nav className={open ? "main-nav is-open" : "main-nav"} aria-label="Primary navigation">
        {links.map(([label, href]) => <Link key={href} href={href} className={pathname === href ? "active" : ""} onClick={() => setOpen(false)}>{label}</Link>)}
        <span className="nav-later" aria-disabled="true">Replay · later</span>
      </nav>
      <ConnectionIndicator />
    </header>
    <main>{children}</main>
    <footer className="footer"><b>Virtual Pit Wall</b><span>Current F1 data · short-horizon analysis</span><span>All times Asia/Kolkata</span></footer>
  </div>;
}
