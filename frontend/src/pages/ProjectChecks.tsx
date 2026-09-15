import { useCallback, useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api, type Check, type Project } from "../lib/api";
import { Breadcrumbs } from "../components/Breadcrumbs";
import { StatusBadge } from "../components/StatusBadge";
import { RunNowButton } from "../components/RunNowButton";
import { formatDateTime } from "../lib/time";

export function ProjectChecks() {
  const { slug = "" } = useParams();
  const [project, setProject] = useState<Project | null>(null);
  const [checks, setChecks] = useState<Check[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  const reload = useCallback(() => {
    Promise.all([api.getProject(slug), api.listProjectChecks(slug)])
      .then(([p, c]) => {
        setProject(p);
        setChecks(c);
      })
      .catch((e) => setError(e.message));
  }, [slug]);

  useEffect(reload, [reload]);

  if (error) {
    return (
      <main className="mx-auto max-w-5xl px-6 py-10 sm:px-10">
        <div className="rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700 dark:border-red-900 dark:bg-red-950 dark:text-red-400">
          {error}
        </div>
      </main>
    );
  }

  if (!project || !checks) {
    return (
      <main className="mx-auto max-w-5xl px-6 py-10 sm:px-10">
        <p className="text-sm text-zinc-500">Loading…</p>
      </main>
    );
  }

  return (
    <main className="mx-auto max-w-5xl px-6 py-10 sm:px-10">
      <Breadcrumbs
        items={[{ label: "Projects", to: "/" }, { label: project.name, to: `/projects/${slug}` }, { label: "Checks" }]}
      />

      <header className="mb-6 flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight text-foreground">Checks</h1>
          <p className="mt-1 text-sm text-zinc-500">
            {checks.length} check{checks.length === 1 ? "" : "s"} in {project.name}, run on a schedule.
          </p>
        </div>
        <Link
          to={`/projects/${slug}/checks/new`}
          className="rounded-md bg-accent px-3 py-1.5 text-sm font-medium text-accent-foreground shadow-sm transition-colors hover:bg-accent-hover"
        >
          New check
        </Link>
      </header>

      {checks.length === 0 ? (
        <div className="rounded-xl border border-dashed border-border bg-surface px-6 py-12 text-center">
          <h2 className="text-base font-medium text-foreground">No checks in this project</h2>
          <p className="mx-auto mt-1 max-w-md text-sm text-zinc-500">
            Checks run on a cron schedule and open a ticket when they fail.
          </p>
          <Link
            to={`/projects/${slug}/checks/new`}
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
                        to={`/projects/${slug}/checks/${check.id}`}
                        className="font-medium text-foreground hover:text-accent"
                      >
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
