import type { ConstructorStanding, DriverStanding } from "@/lib/api/types";
import { driverCode } from "@/lib/format";

export function DriverStandings({ rows, compact = false }: { rows: DriverStanding[]; compact?: boolean }) {
  return <div className="table-scroll"><table><thead><tr><th>Pos</th><th>Driver</th><th>Team</th><th className="numeric">Pts</th></tr></thead><tbody>{rows.slice(0, compact ? 5 : undefined).map(row => <tr key={row.driver.id}><td>{row.position ?? "—"}</td><td><b>{driverCode(row.driver)}</b><span className="cell-detail">{row.driver.full_name}</span></td><td>{row.constructors.map(c => c.name).join(", ") || "—"}</td><td className="numeric"><b>{row.points}</b></td></tr>)}</tbody></table></div>;
}
export function ConstructorStandings({ rows, compact = false }: { rows: ConstructorStanding[]; compact?: boolean }) {
  return <div className="table-scroll"><table><thead><tr><th>Pos</th><th>Constructor</th><th className="numeric">Wins</th><th className="numeric">Pts</th></tr></thead><tbody>{rows.slice(0, compact ? 5 : undefined).map(row => <tr key={row.constructor.id}><td>{row.position ?? "—"}</td><td><b>{row.constructor.name}</b></td><td className="numeric">{row.wins ?? "—"}</td><td className="numeric"><b>{row.points}</b></td></tr>)}</tbody></table></div>;
}
