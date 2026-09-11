const STYLES: Record<string, { pill: string; dot: string }> = {
  PASSED: { pill: "bg-emerald-50 text-emerald-700 dark:bg-emerald-500/10 dark:text-emerald-400", dot: "bg-emerald-500" },
  FAILED: { pill: "bg-red-50 text-red-700 dark:bg-red-500/10 dark:text-red-400", dot: "bg-red-500" },
  ERROR: { pill: "bg-amber-50 text-amber-700 dark:bg-amber-500/10 dark:text-amber-400", dot: "bg-amber-500" },
  RUNNING: { pill: "bg-blue-50 text-blue-700 dark:bg-blue-500/10 dark:text-blue-400", dot: "bg-blue-500 animate-pulse" },
  TODO: { pill: "bg-zinc-100 text-zinc-700 dark:bg-zinc-800 dark:text-zinc-300", dot: "bg-zinc-400" },
  IN_PROGRESS: { pill: "bg-blue-50 text-blue-700 dark:bg-blue-500/10 dark:text-blue-400", dot: "bg-blue-500" },
  DONE: { pill: "bg-emerald-50 text-emerald-700 dark:bg-emerald-500/10 dark:text-emerald-400", dot: "bg-emerald-500" },
  NONE: { pill: "bg-zinc-100 text-zinc-500 dark:bg-zinc-800 dark:text-zinc-400", dot: "bg-zinc-400" },
};

export function StatusBadge({ status }: { status: string }) {
  const style = STYLES[status] ?? STYLES.NONE;
  return (
    <span className={`inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-xs font-medium ${style.pill}`}>
      <span className={`h-1.5 w-1.5 rounded-full ${style.dot}`} />
      {status.replace("_", " ")}
    </span>
  );
}
