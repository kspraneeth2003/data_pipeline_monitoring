import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api, type Check, type Project } from "../lib/api";
import { Breadcrumbs } from "../components/Breadcrumbs";
import { HealthPill } from "../components/HealthPill";
import { StatusBadge } from "../components/StatusBadge";

function latestRun(check: Check) {
  return check.runs?.[0];
}

export function ProjectOverview() {
  const { slug = "" } = useParams();
  const [project, setProject] = useState<Project | null>(null);
  const [checks, setChecks] = useState<Check[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    Promise.all([api.getProject(slug), api.listProjectChecks(slug)])
      .then(([p, c]) => {
        setProject(p);
        setChecks(c);
      })
      .catch((e) => setError(e.message));
  }, [slug]);

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

  if (!project || !checks) {
    return (
      <main className="mx-auto max-w-5xl px-6 py-10 sm:px-10">
        <p className="text-sm text-zinc-500">Loading…</p>
      </main>
    );
  }

  // Anything not green, surfaced first and separately. Scrolling a table to
  // find the red row is work the page should be doing for you.
  const unhealthy = checks.filter((c) => {
    const run = latestRun(c);
    return run && (run.status === "FAILED" || run.status === "ERROR");
  });

  return (
    <main className="mx-auto max-w-5xl px-6 py-10 sm:px-10">
      <Breadcrumbs items={[{ label: "Projects", to: "/" }, { label: project.name }]} />

      <header className="mb-8 flex flex-wrap items-start justify-between gap-4">
        <div>
          <div className="flex items-center gap-3">
            <h1 className="text-2xl font-semibold tracking-tight text-foreground">{project.name}</h1>
            <HealthPill health={project.health} />
          </div>
          {project.description && <p className="mt-1 max-w-2xl text-sm text-zinc-500">{project.description}</p>}
        </div>
      </header>

      <div className="mb-8 grid gap-4 sm:grid-cols-4">
        {[
          { label: "Checks", value: project.health.total_checks, to: `/projects/${slug}/checks` },
          { label: "Passing", value: project.health.passing, tone: "text-emerald-600 dark:text-emerald-400" },
          {
            label: "Needs attention",
            value: project.health.failing + project.health.erroring,
            tone: "text-red-600 dark:text-red-400",
          },
          { label: "Open tickets", value: project.health.open_tickets, to: `/projects/${slug}/tickets` },
        ].map((stat) => {
          const body = (
            <>
              <dt className="text-xs font-medium uppercase tracking-wide text-zinc-500">{stat.label}</dt>
              <dd className={`mt-1 text-2xl font-semibold ${stat.tone ?? "text-foreground"}`}>{stat.value}</dd>
            </>
          );
          return stat.to ? (
            <Link
              key={stat.label}
              to={stat.to}
              className="rounded-xl border border-border bg-surface p-4 transition-colors hover:border-accent"
            >
              {body}
            </Link>
          ) : (
            <div key={stat.label} className="rounded-xl border border-border bg-surface p-4">
              {body}
            </div>
          );
        })}
      </div>

      {unhealthy.length > 0 && (
        <section className="mb-8">
          <h2 className="mb-3 text-sm font-semibold text-foreground">Needs attention</h2>
          <div className="space-y-2">
            {unhealthy.map((check) => {
              const run = latestRun(check);
              return (
                <Link
                  key={check.id}
                  to={`/projects/${slug}/checks/${check.id}`}
                  className="flex items-start justify-between gap-4 rounded-lg border border-red-200 bg-red-50/50 px-4 py-3 transition-colors hover:border-red-300 dark:border-red-900 dark:bg-red-950/30"
                >
                  <div className="min-w-0">
                    <p className="font-medium text-foreground">{check.name}</p>
                    {run?.message && <p className="mt-0.5 line-clamp-2 text-sm text-zinc-600 dark:text-zinc-400">{run.message}</p>}
                  </div>
                  {run && <StatusBadge status={run.status} />}
                </Link>
              );
            })}
          </div>
        </section>
      )}

      <section>
        <div className="mb-3 flex items-center justify-between">
          <h2 className="text-sm font-semibold text-foreground">All checks</h2>
          <Link to={`/projects/${slug}/checks`} className="text-sm text-accent hover:underline">
            View all →
          </Link>
        </div>
        {checks.length === 0 ? (
          <div className="rounded-xl border border-dashed border-border bg-surface px-6 py-10 text-center">
            <h3 className="text-base font-medium text-foreground">No checks yet</h3>
            <p className="mx-auto mt-1 max-w-md text-sm text-zinc-500">
              Add a check to start monitoring this pipeline on a schedule.
            </p>
            <Link
              to={`/projects/${slug}/checks/new`}
              className="mt-5 inline-block rounded-md bg-accent px-4 py-2 text-sm font-medium text-accent-foreground shadow-sm transition-colors hover:bg-accent-hover"
            >
              Add a check
            </Link>
          </div>
        ) : (
          <ul className="divide-y divide-border overflow-hidden rounded-xl border border-border bg-surface">
            {checks.slice(0, 5).map((check) => {
              const run = latestRun(check);
              return (
                <li key={check.id}>
                  <Link
                    to={`/projects/${slug}/checks/${check.id}`}
                    className="flex items-center justify-between gap-4 px-4 py-3 transition-colors hover:bg-accent-soft/40"
                  >
                    <div className="min-w-0">
                      <p className="truncate font-medium text-foreground">{check.name}</p>
                      <p className="mt-0.5 font-mono text-xs text-zinc-500">{check.type}</p>
                    </div>
                    <StatusBadge status={run?.status ?? "NONE"} />
                  </Link>
                </li>
              );
            })}
          </ul>
        )}
      </section>
    </main>
  );
}
