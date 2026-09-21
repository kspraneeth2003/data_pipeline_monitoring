import { useCallback, useEffect, useState } from "react";
import { useParams } from "react-router-dom";

import { Breadcrumbs } from "../components/Breadcrumbs";
import { api, type CheckRevision } from "../lib/api";
import { parseServerDate } from "../lib/time";

/**
 * The review queue for proposed check changes.
 *
 * Shows the current config beside the proposed one, because the decision a
 * reviewer is making is about the difference - the agent's reasoning is an
 * argument for the diff, not a substitute for seeing it.
 */

const KIND_STYLES: Record<string, string> = {
  CREATE: "bg-emerald-100 text-emerald-800 dark:bg-emerald-950 dark:text-emerald-300",
  UPDATE: "bg-amber-100 text-amber-800 dark:bg-amber-950 dark:text-amber-300",
  RETIRE: "bg-zinc-200 text-zinc-700 dark:bg-zinc-800 dark:text-zinc-300",
};

function ConfigBlock({ label, config }: { label: string; config: unknown }) {
  return (
    <div className="min-w-0 flex-1">
      <p className="mb-1 text-xs font-medium text-zinc-500">{label}</p>
      <pre className="overflow-x-auto rounded border border-border bg-background px-3 py-2 text-xs text-foreground">
        {config ? JSON.stringify(config, null, 2) : "—"}
      </pre>
    </div>
  );
}

export function ProjectRevisions() {
  const { slug = "" } = useParams();
  const [revisions, setRevisions] = useState<CheckRevision[]>([]);
  const [busy, setBusy] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);

  const load = useCallback(() => {
    api.listRevisions(slug).then(setRevisions).catch(() => setRevisions([]));
  }, [slug]);

  useEffect(load, [load]);

  const scan = async () => {
    setBusy("scan");
    setNote(null);
    try {
      const result = await api.runMaintenance(slug);
      setNote(
        !result.changed
          ? "No change to the pipeline definition since the last scan."
          : `${result.revisions} revision(s) proposed, ${result.auto_applied} applied automatically` +
            (result.undocumented_ddl
              ? `; ${result.undocumented_ddl} warehouse change(s) with no commit behind them`
              : "") +
            (result.agent_error ? `. The agent failed: ${result.agent_error}` : "."),
      );
      load();
    } catch (error) {
      setNote(error instanceof Error ? error.message : "Scan failed");
    } finally {
      setBusy(null);
    }
  };

  const review = async (id: string, accept: boolean) => {
    setBusy(id);
    try {
      await (accept ? api.applyRevision(id) : api.rejectRevision(id));
      load();
    } catch (error) {
      setNote(error instanceof Error ? error.message : "Could not record that");
    } finally {
      setBusy(null);
    }
  };

  const pending = revisions.filter((r) => r.status === "PENDING");
  const reviewed = revisions.filter((r) => r.status !== "PENDING");

  return (
    <div className="mx-auto max-w-5xl px-6 py-8">
      <Breadcrumbs
        items={[{ label: slug, to: `/projects/${slug}` }, { label: "Proposed changes" }]}
      />

      <div className="mt-4 flex items-center justify-between">
        <div>
          <h1 className="text-xl font-semibold text-foreground">Proposed changes</h1>
          <p className="mt-1 text-sm text-zinc-500">
            Raised when the pipeline's DDL moves and the checks no longer match it.
          </p>
        </div>
        <button
          type="button"
          onClick={scan}
          disabled={busy === "scan"}
          className="rounded-md border border-border px-3 py-1.5 text-sm text-foreground transition-colors hover:border-accent disabled:opacity-50"
        >
          {busy === "scan" ? "Scanning…" : "Scan for changes"}
        </button>
      </div>

      {note && (
        <p className="mt-3 rounded-md border border-border bg-surface px-4 py-2 text-sm text-foreground">
          {note}
        </p>
      )}

      {pending.length === 0 ? (
        <p className="mt-6 rounded-lg border border-border bg-surface px-4 py-6 text-sm text-zinc-500">
          Nothing waiting for review.
        </p>
      ) : (
        <ul className="mt-6 space-y-4">
          {pending.map((revision) => (
            <li key={revision.id} className="rounded-lg border border-border bg-surface p-4">
              <div className="flex flex-wrap items-center gap-2">
                <span
                  className={`rounded px-1.5 py-0.5 text-xs font-medium ${
                    KIND_STYLES[revision.kind]
                  }`}
                >
                  {revision.kind}
                </span>
                <span className="text-sm font-medium text-foreground">
                  {revision.check_name ?? revision.proposed_name ?? "New check"}
                </span>
                <span className="text-xs text-zinc-500">{revision.database_name}</span>
                {revision.confidence !== null && (
                  <span
                    className="text-xs text-zinc-500"
                    title="The agent's own confidence. Below 0.5 it said it was guessing."
                  >
                    {Math.round(revision.confidence * 100)}% confident
                  </span>
                )}
              </div>

              <p className="mt-2 text-sm text-foreground">{revision.reason}</p>

              {revision.triggered_by_commit && (
                <p className="mt-1 font-mono text-xs text-zinc-500">
                  {revision.triggered_by_commit.slice(0, 8)}
                </p>
              )}

              {revision.kind !== "RETIRE" && (
                <div className="mt-3 flex flex-col gap-3 sm:flex-row">
                  <ConfigBlock label="Now" config={revision.current_config} />
                  <ConfigBlock label="Proposed" config={revision.proposed_config} />
                </div>
              )}

              <div className="mt-3 flex gap-2">
                <button
                  type="button"
                  onClick={() => review(revision.id, true)}
                  disabled={busy === revision.id}
                  className="rounded-md bg-accent px-3 py-1.5 text-sm text-accent-foreground transition-colors hover:bg-accent-hover disabled:opacity-50"
                >
                  Apply
                </button>
                <button
                  type="button"
                  onClick={() => review(revision.id, false)}
                  disabled={busy === revision.id}
                  className="rounded-md border border-border px-3 py-1.5 text-sm text-foreground transition-colors hover:border-accent disabled:opacity-50"
                >
                  Reject
                </button>
              </div>
            </li>
          ))}
        </ul>
      )}

      {reviewed.length > 0 && (
        <>
          <h2 className="mt-8 text-sm font-medium text-foreground">Already decided</h2>
          <ul className="mt-2 space-y-1">
            {reviewed.map((revision) => (
              <li key={revision.id} className="text-sm text-zinc-500">
                <span className="text-foreground">
                  {revision.check_name ?? revision.proposed_name ?? "New check"}
                </span>{" "}
                — {revision.kind.toLowerCase()}, {revision.status.toLowerCase().replace("_", " ")}
                {revision.reviewed_at && ` on ${parseServerDate(revision.reviewed_at).toLocaleDateString()}`}
              </li>
            ))}
          </ul>
        </>
      )}
    </div>
  );
}
