import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, type Project } from "../lib/api";
import { HealthPill } from "../components/HealthPill";
import { relativeTime } from "../lib/time";

export function Projects() {
  const [projects, setProjects] = useState<Project[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.listProjects().then(setProjects).catch((e) => setError(e.message));
  }, []);

  // Sort unhealthy first. The whole point of a landing page is that the thing
  // needing attention is the thing you see, without scanning or filtering.
  const ordered = projects
    ? [...projects].sort((a, b) => {
        const rank = (p: Project) =>
          p.health.erroring > 0 ? 0 : p.health.failing > 0 ? 1 : p.health.total_checks === 0 ? 3 : 2;
        return rank(a) - rank(b) || a.name.localeCompare(b.name);
      })
    : null;

  const needsAttention = ordered?.filter((p) => p.health.failing > 0 || p.health.erroring > 0) ?? [];

  return (
    <main className="mx-auto max-w-5xl px-6 py-10 sm:px-10">
      <header className="mb-8">
        <h1 className="text-2xl font-semibold tracking-[0.08em] text-foreground">Projects</h1>
        <p className="mt-1 text-sm text-zinc-500">
          Each project is a data product — the databases that together serve one domain.
          {needsAttention.length > 0 && (
            <>
              {" "}
              <span className="font-medium text-red-600 dark:text-red-400">
                {needsAttention.length} need{needsAttention.length === 1 ? "s" : ""} attention.
              </span>
            </>
          )}
        </p>
      </header>

      {error && (
        <div className="rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700 dark:border-red-900 dark:bg-red-950 dark:text-red-400">
          {error}
        </div>
      )}

      {ordered === null && !error && <p className="text-sm text-zinc-500">Loading…</p>}

      {ordered?.length === 0 && (
        <div className="rounded-xl border border-dashed border-border bg-surface px-6 py-12 text-center">
          <h2 className="text-base font-medium text-foreground">No projects yet</h2>
          <p className="mx-auto mt-1 max-w-md text-sm text-zinc-500">
            A project is a data product — Customer 360, Inventory. Inside it you add the databases it
            spans, and checks live inside those.
          </p>
          <Link
            to="/projects/new"
            className="mt-5 inline-block rounded-md bg-accent px-4 py-2 text-sm font-medium text-accent-foreground shadow-sm transition-colors hover:bg-accent-hover"
          >
            Create your first project
          </Link>
        </div>
      )}

      {ordered && ordered.length > 0 && (
        <div className="grid gap-4 sm:grid-cols-2">
          {ordered.map((project) => (
            <Link
              key={project.id}
              to={`/projects/${project.slug}`}
              className="group rounded-xl border border-border bg-surface p-5 transition-all hover:border-accent hover:shadow-sm"
            >
              <div className="flex items-start justify-between gap-3">
                <h2 className="font-medium text-foreground transition-colors group-hover:text-accent">
                  {project.name}
                </h2>
                <HealthPill health={project.health} />
              </div>

              {project.description && (
                <p className="mt-2 line-clamp-2 text-sm text-zinc-500">{project.description}</p>
              )}

              <dl className="mt-4 flex flex-wrap items-center gap-x-5 gap-y-1 text-xs text-zinc-500">
                <div className="flex gap-1">
                  <dt>Databases</dt>
                  <dd className="font-medium text-foreground">{project.databases.length}</dd>
                </div>
                <div className="flex gap-1">
                  <dt>Checks</dt>
                  <dd className="font-medium text-foreground">{project.health.total_checks}</dd>
                </div>
                {project.health.open_incidents > 0 && (
                  <div className="flex gap-1">
                    <dt>Open incidents</dt>
                    <dd className="font-medium text-foreground">{project.health.open_incidents}</dd>
                  </div>
                )}
                <div className="flex gap-1">
                  <dt>Last run</dt>
                  <dd className="font-medium text-foreground">{relativeTime(project.health.last_run_at)}</dd>
                </div>
              </dl>
            </Link>
          ))}
        </div>
      )}
    </main>
  );
}
