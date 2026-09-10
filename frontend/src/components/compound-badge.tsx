const compoundNames: Record<string, string> = { S: "Soft", M: "Medium", H: "Hard", I: "Intermediate", W: "Wet" };

export function CompoundBadge({ compound, full = false }: { compound?: string | null; full?: boolean }) {
  const key = compound?.slice(0, 1).toUpperCase() || "U";
  const name = compoundNames[key] ?? compound ?? "Unknown";
  return <span className={`compound compound-${name.toLowerCase()}`} title={name}><b>{key}</b>{full ? <span>{name}</span> : null}</span>;
}
