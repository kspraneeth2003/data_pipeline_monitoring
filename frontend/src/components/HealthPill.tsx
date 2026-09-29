import type { ProjectHealth } from "../lib/api";

const STATUS_STYLES: Record<string, string> = {
  PASSED: "border-emerald-500/40 bg-emerald-950 text-emerald-400",
  FAILED: "border-red-500/40 bg-red-950 text-red-400",
  ERROR: "border-amber-500/40 bg-amber-950 text-amber-400",
  NONE: "border-zinc-700 bg-zinc-900 text-zinc-400",
};

const DOT_STYLES: Record<string, string> = {
  PASSED: "bg-emerald-500",
  FAILED: "bg-red-500",
  ERROR: "bg-amber-500",
  NONE: "bg-zinc-400",
};

/**
 * Says what is wrong, not just that something is. "3 failing" is actionable in
 * a way that a bare red dot is not, so the label carries the count that drove
 * the status.
 */
export function HealthPill({ health }: { health: ProjectHealth }) {
  const label =
    health.total_checks === 0
      ? "No checks"
      : health.erroring > 0
        ? `${health.erroring} erroring`
        : health.failing > 0
          ? `${health.failing} failing`
          : health.passing > 0
            ? "All passing"
            : "Not yet run";

  const key = health.total_checks === 0 ? "NONE" : health.status;

  return (
    <span
      className={`inline-flex items-center gap-1.5 border px-2 py-0.5 font-mono text-[11px] uppercase tracking-[0.1em] ${STATUS_STYLES[key] ?? STATUS_STYLES.NONE}`}
    >
      <span className={`h-1.5 w-1.5 ${DOT_STYLES[key] ?? DOT_STYLES.NONE}`} />
      {label}
    </span>
  );
}
