import { useCallback, useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";

import { Breadcrumbs } from "../components/Breadcrumbs";
import { api, type Incident, type IncidentDetail } from "../lib/api";
import { parseServerDate, relativeTime } from "../lib/time";

/**
 * Incidents, with the comment stream that is the monitor's actual output.
 *
 * Ordered active-first rather than newest-first: a cleared incident from an
 * hour ago is history, and an open one from last week is the thing somebody
 * still has to deal with.
 */

const STATE_STYLES: Record<string, string> = {
  OPEN: "bg-red-100 text-red-800 dark:bg-red-950 dark:text-red-300",
  WARNING: "bg-amber-100 text-amber-800 dark:bg-amber-950 dark:text-amber-300",
  CLEARED: "bg-emerald-100 text-emerald-800 dark:bg-emerald-950 dark:text-emerald-300",
  SUPPRESSED: "bg-zinc-200 text-zinc-700 dark:bg-zinc-800 dark:text-zinc-300",
};

const EVENT_LABELS: Record<string, string> = {
  OPENED: "Opened",
  COMMENT: "Comment",
  ESCALATED: "Escalated",
  REOPENED: "Reopened",
  CLEARED: "Cleared",
  SUPPRESSED: "Suppressed",
};

function EventStream({ incidentId }: { incidentId: string }) {
  const [detail, setDetail] = useState<IncidentDetail | null>(null);

  useEffect(() => {
    api.getIncident(incidentId).then(setDetail).catch(() => setDetail(null));
  }, [incidentId]);

  if (!detail) return <p className="px-4 py-3 text-sm text-zinc-500">Loading…</p>;
  if (detail.events.length === 0) {
    return (
      <p className="px-4 py-3 text-sm text-zinc-500">
        No comments yet — nothing has changed since this incident opened.
      </p>
    );
  }

  return (
    <ol className="space-y-3 px-4 py-3">
      {detail.events.map((event) => (
        <li key={event.id} className="border-l-2 border-border pl-3">
          <p className="text-xs text-zinc-500">
            <span className="font-medium text-foreground">
              {EVENT_LABELS[event.kind] ?? event.kind}
            </span>{" "}
            · by {event.author} · {relativeTime(event.created_at)}
          </p>
          <p className="mt-1 whitespace-pre-wrap text-sm text-foreground">{event.body}</p>
        </li>
      ))}
    </ol>
  );
}

export function ProjectIncidents() {
  const { slug = "" } = useParams();
  const [incidents, setIncidents] = useState<Incident[]>([]);
  const [expanded, setExpanded] = useState<string | null>(null);
  const [sweeping, setSweeping] = useState(false);
  const [note, setNote] = useState<string | null>(null);

  const load = useCallback(() => {
    api.listIncidents(slug).then(setIncidents).catch(() => setIncidents([]));
  }, [slug]);

  useEffect(load, [load]);

  const sweep = async () => {
    setSweeping(true);
    setNote(null);
    try {
      const result = await api.runSweep();
      setNote(
        result.agent_error
          ? `Rules ran; the agent failed: ${result.agent_error}`
          : `Reopened ${result.reopened}, escalated ${result.escalated}, agent acted on ${result.agent_actions}.`,
      );
      load();
    } catch (error) {
      setNote(error instanceof Error ? error.message : "Sweep failed");
    } finally {
      setSweeping(false);
    }
  };

  // Active first: a cleared incident is history, an open one is work.
  const sorted = [...incidents].sort((a, b) => {
    const rank = (i: Incident) => (i.state === "OPEN" ? 0 : i.state === "WARNING" ? 1 : 2);
    return rank(a) - rank(b) || parseServerDate(b.opened_at).getTime() - parseServerDate(a.opened_at).getTime();
  });

  return (
    <div className="mx-auto max-w-5xl px-6 py-8">
      <Breadcrumbs items={[{ label: slug, to: `/projects/${slug}` }, { label: "Incidents" }]} />

      <div className="mt-4 flex items-center justify-between">
        <div>
          <h1 className="text-xl font-semibold text-foreground">Incidents</h1>
          <p className="mt-1 text-sm text-zinc-500">
            One per problem, across every run it takes to fix.
          </p>
        </div>
        <button
          type="button"
          onClick={sweep}
          disabled={sweeping}
          className="rounded-md border border-border px-3 py-1.5 text-sm text-foreground transition-colors hover:border-accent disabled:opacity-50"
        >
          {sweeping ? "Sweeping…" : "Run monitor now"}
        </button>
      </div>

      {note && (
        <p className="mt-3 rounded-md border border-border bg-surface px-4 py-2 text-sm text-foreground">
          {note}
        </p>
      )}

      {sorted.length === 0 ? (
        <p className="mt-6 rounded-lg border border-border bg-surface px-4 py-6 text-sm text-zinc-500">
          No incidents. Every check on this project is passing.
        </p>
      ) : (
        <ul className="mt-6 space-y-3">
          {sorted.map((incident) => (
            <li key={incident.id} className="rounded-lg border border-border bg-surface">
              <button
                type="button"
                onClick={() => setExpanded(expanded === incident.id ? null : incident.id)}
                className="flex w-full items-start justify-between gap-4 px-4 py-3 text-left"
              >
                <div className="min-w-0">
                  <div className="flex flex-wrap items-center gap-2">
                    <span
                      className={`rounded px-1.5 py-0.5 text-xs font-medium ${
                        STATE_STYLES[incident.state] ?? STATE_STYLES.SUPPRESSED
                      }`}
                    >
                      {incident.state}
                    </span>
                    <span className="text-sm font-medium text-foreground">
                      {incident.check_name}
                    </span>
                    {incident.correlation_id && (
                      <span
                        className="rounded bg-accent-soft px-1.5 py-0.5 text-xs text-accent"
                        title="Grouped with other incidents believed to share one cause"
                      >
                        correlated
                      </span>
                    )}
                    {incident.triage_source === "agent" && (
                      <span className="rounded border border-border px-1.5 py-0.5 text-xs text-zinc-500">
                        agent
                      </span>
                    )}
                  </div>
                  <p className="mt-1 truncate text-xs text-zinc-500">
                    {incident.failure_count} failure{incident.failure_count === 1 ? "" : "s"} ·
                    opened {relativeTime(incident.opened_at)}
                    {incident.escalation_count > 0 &&
                      ` · escalated ${incident.escalation_count}×`}
                    {incident.event_count > 0 && ` · ${incident.event_count} comment(s)`}
                  </p>
                  {incident.summary && (
                    <p className="mt-1 line-clamp-2 text-sm text-zinc-500">{incident.summary}</p>
                  )}
                </div>
                <div className="shrink-0 text-right">
                  <p className="text-xs font-medium text-foreground">{incident.severity}</p>
                  {incident.ticket && (
                    <Link
                      to={`/projects/${slug}/tickets`}
                      onClick={(e) => e.stopPropagation()}
                      className="text-xs text-accent hover:underline"
                    >
                      {incident.ticket.key}
                    </Link>
                  )}
                </div>
              </button>
              {expanded === incident.id && (
                <div className="border-t border-border">
                  <EventStream incidentId={incident.id} />
                </div>
              )}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
