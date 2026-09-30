import type { AnomalySeverity, ProfileAnomaly } from "../lib/api";

/** Severity is always written out, never carried by color alone. */
const STYLES: Record<AnomalySeverity, { pill: string; dot: string }> = {
  HIGH: { pill: "border-red-500/40 bg-red-500/10 text-red-400", dot: "bg-red-500" },
  MEDIUM: { pill: "border-amber-500/40 bg-amber-500/10 text-amber-400", dot: "bg-amber-500" },
  LOW: { pill: "border-border bg-surface-raised text-zinc-400", dot: "bg-zinc-400" },
};

const KIND_LABELS: Record<ProfileAnomaly["kind"], string> = {
  ANOMALY: "vs history",
  FINDING: "finding",
  SCHEMA: "schema",
};

export function SeverityBadge({ severity }: { severity: AnomalySeverity }) {
  const style = STYLES[severity] ?? STYLES.LOW;
  return (
    <span className={`inline-flex items-center gap-1.5 border px-2 py-0.5 font-mono text-[11px] uppercase tracking-[0.1em] ${style.pill}`}>
      <span className={`h-1.5 w-1.5 ${style.dot}`} />
      {severity}
    </span>
  );
}

export function KindLabel({ kind }: { kind: ProfileAnomaly["kind"] }) {
  return (
    <span className="font-mono text-[11px] uppercase tracking-[0.1em] text-zinc-500">{KIND_LABELS[kind] ?? kind}</span>
  );
}
