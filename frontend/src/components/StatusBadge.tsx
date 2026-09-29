const STYLES: Record<string, { pill: string; dot: string }> = {
  PASSED: { pill: "border-emerald-500/40 bg-emerald-500/10 text-emerald-400", dot: "bg-emerald-500" },
  FAILED: { pill: "border-red-500/40 bg-red-500/10 text-red-400", dot: "bg-red-500" },
  ERROR: { pill: "border-amber-500/40 bg-amber-500/10 text-amber-400", dot: "bg-amber-500" },
  RUNNING: { pill: "border-blue-500/40 bg-blue-500/10 text-blue-400", dot: "bg-blue-500 animate-pulse" },
  TODO: { pill: "border-zinc-700 bg-zinc-800 text-zinc-300", dot: "bg-zinc-400" },
  IN_PROGRESS: { pill: "border-blue-500/40 bg-blue-500/10 text-blue-400", dot: "bg-blue-500" },
  DONE: { pill: "border-emerald-500/40 bg-emerald-500/10 text-emerald-400", dot: "bg-emerald-500" },
  NONE: { pill: "border-zinc-700 bg-zinc-800 text-zinc-400", dot: "bg-zinc-400" },
};

export function StatusBadge({ status }: { status: string }) {
  const style = STYLES[status] ?? STYLES.NONE;
  return (
    <span className={`inline-flex items-center gap-1.5 border px-2 py-0.5 font-mono text-[11px] uppercase tracking-[0.1em] ${style.pill}`}>
      <span className={`h-1.5 w-1.5 ${style.dot}`} />
      {status.replace("_", " ")}
    </span>
  );
}
