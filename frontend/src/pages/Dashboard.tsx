import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, type Check } from "../lib/api";
import { StatusBadge } from "../components/StatusBadge";
import { RunNowButton } from "../components/RunNowButton";

export function Dashboard() {
  const [checks, setChecks] = useState<Check[]>([]);
  const [loading, setLoading] = useState(true);

  function reload() {
    api.listChecks().then(setChecks).finally(() => setLoading(false));
  }

  useEffect(reload, []);

  const lastRuns = checks.map((c) => c.runs[0]?.status);
  const stats = {
    total: checks.length,
    passing: lastRuns.filter((s) => s === "PASSED").length,
    attention: lastRuns.filter((s) => s === "FAILED" || s === "ERROR").length,
  };

  if (loading) return null;

  return (
    <div className="min-h-screen bg-background px-6 py-10 sm:px-10">
      <div className="mx-auto max-w-5xl">
        <header className="mb-6">
          <h1 className="text-2xl font-semibold tracking-tight text-foreground">Data Integrity Checks</h1>
          <p className="mt-1 text-sm text-zinc-500 dark:text-zinc-400">
            Scheduled parity / integrity checks against any connected Snowflake account.
          </p>
        </header>

        <div className="mb-6 grid grid-cols-3 gap-4">
          <StatCard label="Total checks" value={stats.total} />
          <StatCard label="Passing" value={stats.passing} tone="success" />
          <StatCard label="Needs attention" value={stats.attention} tone={stats.attention > 0 ? "danger" : undefined} />
        </div>

        <div className="overflow-hidden rounded-xl border border-border bg-surface shadow-sm">
          <table className="min-w-full divide-y divide-border">
            <thead className="bg-zinc-50 dark:bg-white/[0.03]">
              <tr>
                <Th>Check</Th>
                <Th>Type</Th>
                <Th>Schedule</Th>
                <Th>Last run</Th>
                <Th>Status</Th>
                <Th />
              </tr>
            </thead>
            <tbody className="divide-y divide-border">
              {checks.map((check) => {
                const lastRun = check.runs[0];
                return (
                  <tr key={check.id} className="transition-colors hover:bg-zinc-50 dark:hover:bg-white/[0.02]">
                    <td className="px-4 py-3">
                      <Link to={`/checks/${check.id}`} className="font-medium text-foreground hover:text-accent">
                        {check.name}
                      </Link>
                      <div className="text-xs text-zinc-500 dark:text-zinc-400">
                        {check.connector.name}
                        {check.secondary_connector ? ` → ${check.secondary_connector.name}` : ""}
                      </div>
                    </td>
                    <td className="px-4 py-3">
                      <span className="rounded-md bg-zinc-100 px-1.5 py-0.5 text-xs font-medium text-zinc-600 dark:bg-zinc-800 dark:text-zinc-300">
                        {check.type}
                      </span>
                    </td>
                    <td className="px-4 py-3 font-mono text-xs text-zinc-500 dark:text-zinc-400">{check.schedule}</td>
                    <td className="px-4 py-3 text-sm text-zinc-500 dark:text-zinc-400">
                      {lastRun ? new Date(lastRun.started_at).toLocaleString() : "never"}
                    </td>
                    <td className="px-4 py-3">
                      <StatusBadge status={lastRun?.status ?? "NONE"} />
                    </td>
                    <td className="px-4 py-3 text-right">
                      <RunNowButton checkId={check.id} onDone={reload} />
                    </td>
                  </tr>
                );
              })}
              {checks.length === 0 && (
                <tr>
                  <td colSpan={6} className="px-4 py-10 text-center text-sm text-zinc-500">
                    No checks yet. Run <code>python -m app.seed</code> in the backend to create example checks.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}

function StatCard({ label, value, tone }: { label: string; value: number; tone?: "success" | "danger" }) {
  const valueColor =
    tone === "success" ? "text-emerald-600 dark:text-emerald-400" : tone === "danger" ? "text-red-600 dark:text-red-400" : "text-foreground";
  return (
    <div className="rounded-xl border border-border bg-surface p-4 shadow-sm">
      <div className="text-xs font-medium uppercase tracking-wide text-zinc-500 dark:text-zinc-400">{label}</div>
      <div className={`mt-1 text-2xl font-semibold ${valueColor}`}>{value}</div>
    </div>
  );
}

function Th({ children }: { children?: React.ReactNode }) {
  return <th className="px-4 py-2 text-left text-xs font-semibold uppercase tracking-wide text-zinc-500 dark:text-zinc-400">{children}</th>;
}
