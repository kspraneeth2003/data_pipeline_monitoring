import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api, type Project } from "../lib/api";
import { Breadcrumbs } from "../components/Breadcrumbs";
import { HealthPill } from "../components/HealthPill";
import { relativeTime } from "../lib/time";

export function ProjectOverview() {
  const { slug = "" } = useParams();
  const [project, setProject] = useState<Project | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.getProject(slug).then(setProject).catch((e) => setError(e.message));
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

  if (!project) {
    return (
      <main className="mx-auto max-w-5xl px-6 py-10 sm:px-10">
        <p className="text-sm text-zinc-500">Loading…</p>
      </main>
    );
  }

  // Unhealthy databases first, same rule as the projects list one level up.
  const databases = [...project.databases].sort((a, b) => {
    const rank = (d: typeof a) =>
      d.health.erroring > 0 ? 0 : d.health.failing > 0 ? 1 : d.health.total_checks === 0 ? 3 : 2;
    return rank(a) - rank(b) || a.name.localeCompare(b.name);
  });

  return (
    <main className="mx-auto max-w-5xl px-6 py-10 sm:px-10">
      <Breadcrumbs items={[{ label: "Projects", to: "/" }, { label: project.name }]} />

      <header className="mb-8">
        <div className="flex flex-wrap items-center gap-3">
          <h1 className="text-2xl font-semibold tracking-[0.08em] text-foreground">{project.name}</h1>
          <HealthPill health={project.health} />
        </div>
        {project.description && <p className="mt-1 max-w-2xl text-sm text-zinc-500">{project.description}</p>}

        {/* The two agents' output. Incidents is where the monitor reports -
            and the record of what went wrong, whether or not it reached
            Jira. Proposed changes is where the maintenance agent reports. */}
        <nav className="mt-3 flex flex-wrap gap-4 text-sm">
          <Link to={`/projects/${slug}/incidents`} className="text-accent hover:underline">
            Incidents
          </Link>
          <Link to={`/projects/${slug}/changes`} className="text-accent hover:underline">
            Proposed changes
          </Link>
        </nav>
      </header>

      <div className="mb-8 grid gap-4 sm:grid-cols-4">
        {[
          { label: "Databases", value: project.databases.length },
          { label: "Checks", value: project.health.total_checks },
          {
            label: "Needs attention",
            value: project.health.failing + project.health.erroring,
            tone:
              project.health.failing + project.health.erroring > 0 ? "text-red-400" : "text-foreground",
          },
          { label: "Open incidents", value: project.health.open_incidents, to: `/projects/${slug}/incidents` },
        ].map((stat) => {
          const body = (
            <>
              <dt className="text-xs font-medium uppercase tracking-wide text-zinc-500">{stat.label}</dt>
              <dd className={`mt-1 font-mono text-2xl font-semibold ${stat.tone ?? "text-foreground"}`}>{stat.value}</dd>
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

      <section>
        <div className="mb-3 flex items-center justify-between">
          <h2 className="text-sm font-semibold text-foreground">Databases</h2>
          <Link to={`/projects/${slug}/databases/new`} className="text-sm text-accent hover:underline">
            + Add a database
          </Link>
        </div>

        {databases.length === 0 ? (
          <div className="rounded-xl border border-dashed border-border bg-surface px-6 py-12 text-center">
            <h3 className="text-base font-medium text-foreground">No databases yet</h3>
            <p className="mx-auto mt-1 max-w-md text-sm text-zinc-500">
              Add the databases this project spans — the source databases it reads and the ones it writes.
              Checks live inside a database.
            </p>
            <Link
              to={`/projects/${slug}/databases/new`}
              className="mt-5 inline-block rounded-md bg-accent px-4 py-2 text-sm font-medium text-accent-foreground shadow-sm transition-colors hover:bg-accent-hover"
            >
              Add a database
            </Link>
          </div>
        ) : (
          <div className="grid gap-4 sm:grid-cols-2">
            {databases.map((database) => (
              <Link
                key={database.id}
                to={`/projects/${slug}/databases/${database.slug}`}
                className="group rounded-xl border border-border bg-surface p-5 transition-all hover:border-accent hover:shadow-sm"
              >
                <div className="flex items-start justify-between gap-3">
                  <h3 className="font-mono text-sm font-medium text-foreground transition-colors group-hover:text-accent">
                    {database.name}
                  </h3>
                  <HealthPill health={database.health} />
                </div>
                {database.description && (
                  <p className="mt-2 line-clamp-2 text-sm text-zinc-500">{database.description}</p>
                )}
                <dl className="mt-4 flex flex-wrap items-center gap-x-5 gap-y-1 text-xs text-zinc-500">
                  <div className="flex gap-1">
                    <dt>Checks</dt>
                    <dd className="font-medium text-foreground">{database.health.total_checks}</dd>
                  </div>
                  <div className="flex gap-1">
                    <dt>Last run</dt>
                    <dd className="font-medium text-foreground">{relativeTime(database.health.last_run_at)}</dd>
                  </div>
                </dl>
              </Link>
            ))}
          </div>
        )}
      </section>
    </main>
  );
}
