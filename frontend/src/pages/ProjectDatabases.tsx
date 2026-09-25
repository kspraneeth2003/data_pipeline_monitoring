import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api, type Project } from "../lib/api";
import { Breadcrumbs } from "../components/Breadcrumbs";
import { HealthPill } from "../components/HealthPill";
import { relativeTime } from "../lib/time";

/**
 * The databases a project spans.
 *
 * This used to be the project page itself, which made the database the way in
 * to everything. It is not: a check is reasoned about by the hop it watches,
 * not by which database its row is stored under, and a parity check spans two
 * databases anyway. So the project opens on its checks and this page is what
 * it always should have been - where a database is added, described, pointed
 * at a different connector, or removed.
 */
export function ProjectDatabases() {
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

  // Unhealthy first, same rule as the projects list one level up.
  const databases = [...project.databases].sort((a, b) => {
    const rank = (d: typeof a) =>
      d.health.erroring > 0 ? 0 : d.health.failing > 0 ? 1 : d.health.total_checks === 0 ? 3 : 2;
    return rank(a) - rank(b) || a.name.localeCompare(b.name);
  });

  return (
    <main className="mx-auto max-w-5xl px-6 py-10 sm:px-10">
      <Breadcrumbs
        items={[
          { label: "Projects", to: "/" },
          { label: project.name, to: `/projects/${slug}` },
          { label: "Databases" },
        ]}
      />

      <header className="mb-6 flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold tracking-[0.08em] text-foreground">Databases</h1>
          <p className="mt-1 max-w-2xl text-sm text-zinc-500">
            The databases {project.name} spans — the sources it reads and the ones it writes. To
            find a check, use the stage tabs on the{" "}
            <Link to={`/projects/${slug}`} className="text-accent hover:underline">
              project page
            </Link>
            .
          </p>
        </div>
        <Link
          to={`/projects/${slug}/databases/new`}
          className="shrink-0 rounded-md bg-accent px-3 py-1.5 text-sm font-medium text-accent-foreground shadow-sm transition-colors hover:bg-accent-hover"
        >
          Add a database
        </Link>
      </header>

      {databases.length === 0 ? (
        <div className="rounded-xl border border-dashed border-border bg-surface px-6 py-12 text-center">
          <h2 className="text-base font-medium text-foreground">No databases yet</h2>
          <p className="mx-auto mt-1 max-w-md text-sm text-zinc-500">
            Add the databases this project spans. Checks are stored under a database, so there has
            to be one before a check can exist.
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
                <h2 className="font-mono text-sm font-medium text-foreground transition-colors group-hover:text-accent">
                  {database.name}
                </h2>
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
                <div className="flex gap-1">
                  <dt>via</dt>
                  <dd className="font-medium text-foreground">{database.connector.name}</dd>
                </div>
              </dl>
            </Link>
          ))}
        </div>
      )}
    </main>
  );
}
