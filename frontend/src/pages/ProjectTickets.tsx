import { useCallback, useEffect, useState } from "react";
import { useParams } from "react-router-dom";
import { api, type Project, type TicketWithContext } from "../lib/api";
import { Breadcrumbs } from "../components/Breadcrumbs";
import { TicketBoard } from "../components/TicketBoard";

export function ProjectTickets() {
  const { slug = "" } = useParams();
  const [project, setProject] = useState<Project | null>(null);
  const [tickets, setTickets] = useState<TicketWithContext[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  const reload = useCallback(() => {
    Promise.all([api.getProject(slug), api.listProjectTickets(slug)])
      .then(([p, t]) => {
        setProject(p);
        setTickets(t);
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

  if (!project || !tickets) {
    return (
      <main className="mx-auto max-w-5xl px-6 py-10 sm:px-10">
        <p className="text-sm text-zinc-500">Loading…</p>
      </main>
    );
  }

  return (
    <main className="mx-auto max-w-5xl px-6 py-10 sm:px-10">
      <Breadcrumbs
        items={[{ label: "Projects", to: "/" }, { label: project.name, to: `/projects/${slug}` }, { label: "Tickets" }]}
      />

      <header className="mb-6">
        <h1 className="text-2xl font-semibold tracking-tight text-foreground">Tickets</h1>
        <p className="mt-1 text-sm text-zinc-500">
          Simulated Jira board, scoped to {project.name}. Tickets are filed automatically, pre-filled from RCA,
          when a check in this project fails.
        </p>
      </header>

      {tickets.length === 0 ? (
        <div className="rounded-xl border border-dashed border-border bg-surface px-6 py-12 text-center">
          <h2 className="text-base font-medium text-foreground">No tickets</h2>
          <p className="mx-auto mt-1 max-w-md text-sm text-zinc-500">
            Nothing has failed in {project.name}. Tickets appear here automatically when a check fails.
          </p>
        </div>
      ) : (
        <TicketBoard tickets={tickets} onChange={reload} projectSlug={slug} />
      )}
    </main>
  );
}
