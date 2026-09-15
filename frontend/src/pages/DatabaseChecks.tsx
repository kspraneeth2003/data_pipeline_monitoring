import { useCallback, useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api, type Check, type Database } from "../lib/api";
import { Breadcrumbs } from "../components/Breadcrumbs";
import { HealthPill } from "../components/HealthPill";
import { StatusBadge } from "../components/StatusBadge";
import { RunNowButton } from "../components/RunNowButton";
import { formatDateTime } from "../lib/time";

export function DatabaseChecks() {
  const { slug = "", dbSlug = "" } = useParams();
  const [database, setDatabase] = useState<Database | null>(null);
  const [checks, setChecks] = useState<Check[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  const reload = useCallback(() => {
    Promise.all([api.getDatabase(slug, dbSlug), api.listDatabaseChecks(slug, dbSlug)])
      .then(([d, c]) => {
        setDatabase(d);
        setChecks(c);
      })
      .catch((e) => setError(e.message));
  }, [slug, dbSlug]);

  useEffect(reload, [reload]);

  if (error) {
    return (
      <main className="mx-auto max-w-5xl px-6 py-10 sm:px-10">
        <Breadcrumbs items={[{ label: "Projects", to: "/" }, { label: "Not found" }]} />
        <div className="rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700 dark:border-red-900 dark:bg-red-950 dark:text-red-400">
          {error}
        </div>
      </main>
    );
  }

  if (!database || !checks) {
    return (
      <main className="mx-auto max-w-5xl px-6 py-10 sm:px-10">
        <p className="text-sm text-zinc-500">Loading…</p>
      </main>
    );
  }

  const projectName = checks[0]?.database.project.name ?? "Project";

  return (
    <main className="mx-auto max-w-5xl px-6 py-10 sm:px-10">
      <Breadcrumbs
        items={[
          { label: "Projects", to: "/" },
          { label: projectName, to: `/projects/${slug}` },
          { label: database.name },
        ]}
      />

      <header className="mb-6 flex flex-wrap items-start justify-between gap-3">
        <div>
          <div className="flex flex-wrap items-center gap-3">
            <h1 className="font-mono text-2xl font-semibold tracking-tight text-foreground">{database.name}</h1>
            <HealthPill health={database.health} />
          </div>
          {database.description && <p className="mt-1 max-w-2xl text-sm text-zinc-500">{database.description}</p>}
          <p className="mt-1 text-xs text-zinc-500">via {database.connector.name}</p>
        </div>
        <div className="flex shrink-0 gap-2">
          <Link
            to={`/projects/${slug}/databases/${dbSlug}/settings`}
            className="rounded-md border border-border px-3 py-1.5 text-sm font-medium text-zinc-700 transition-colors hover:bg-zinc-100 dark:text-zinc-200 dark:hover:bg-zinc-800"
          >
            Settings
          </Link>
          <Link
            to={`/projects/${slug}/databases/${dbSlug}/checks/new`}
            className="rounded-md bg-accent px-3 py-1.5 text-sm font-medium text-accent-foreground shadow-sm transition-colors hover:bg-accent-hover"
          >
            New check
          </Link>
        </div>
      </header>

      {checks.length === 0 ? (
        <div className="rounded-xl border border-dashed border-border bg-surface px-6 py-12 text-center">
          <h2 className="text-base font-medium text-foreground">No checks on this database</h2>
          <p className="mx-auto mt-1 max-w-md text-sm text-zinc-500">
            Checks run on a cron schedule against {database.name} and open a ticket when they fail.
          </p>
          <Link
            to={`/projects/${slug}/databases/${dbSlug}/checks/new`}
            className="mt-5 inline-block rounded-md bg-accent px-4 py-2 text-sm font-medium text-accent-foreground shadow-sm transition-colors hover:bg-accent-hover"
          >
            Add a check
          </Link>
        </div>
      ) : (
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
                      <Link
                        to={`/projects/${slug}/databases/${dbSlug}/checks/${check.id}`}
                        className="font-medium text-foreground hover:text-accent"
                      >
                        {check.name}
                      </Link>
                      {lastRun?.message && (
                        <div className="mt-0.5 line-clamp-1 text-xs text-zinc-500">{lastRun.message}</div>
                      )}
                    </td>
                    <td className="px-4 py-3">
                      <span className="rounded-md bg-zinc-100 px-1.5 py-0.5 text-xs font-medium text-zinc-600 dark:bg-zinc-800 dark:text-zinc-300">
                        {check.type}
                      </span>
                    </td>
                    <td className="px-4 py-3 font-mono text-xs text-zinc-500 dark:text-zinc-400">{check.schedule}</td>
                    <td className="px-4 py-3 text-sm text-zinc-500 dark:text-zinc-400">
                      {formatDateTime(lastRun?.started_at)}
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
            </tbody>
          </table>
        </div>
      )}
    </main>
  );
}

function Th({ children }: { children?: React.ReactNode }) {
  return (
    <th className="px-4 py-2 text-left text-xs font-semibold uppercase tracking-wide text-zinc-500 dark:text-zinc-400">
      {children}
    </th>
  );
}
