import type { ProjectHealth } from "../lib/api";

const STATUS_STYLES: Record<string, string> = {
  PASSED: "bg-emerald-50 text-emerald-700 dark:bg-emerald-950 dark:text-emerald-400",
  FAILED: "bg-red-50 text-red-700 dark:bg-red-950 dark:text-red-400",
  ERROR: "bg-amber-50 text-amber-700 dark:bg-amber-950 dark:text-amber-400",
  NONE: "bg-zinc-100 text-zinc-600 dark:bg-zinc-900 dark:text-zinc-400",
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
      className={`inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-xs font-medium ${STATUS_STYLES[key] ?? STATUS_STYLES.NONE}`}
    >
      <span className={`h-1.5 w-1.5 rounded-full ${DOT_STYLES[key] ?? DOT_STYLES.NONE}`} />
      {label}
    </span>
  );
}
