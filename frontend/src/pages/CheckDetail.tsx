import { useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api, type Check } from "../lib/api";
import { Breadcrumbs } from "../components/Breadcrumbs";
import { formatDateTime } from "../lib/time";
import { StatusBadge } from "../components/StatusBadge";
import { RunNowButton } from "../components/RunNowButton";
import { EnabledToggle } from "../components/EnabledToggle";
import { DeleteButton } from "../components/DeleteButton";

export function CheckDetail() {
  const { id, slug = "", dbSlug = "" } = useParams<{ id: string; slug: string; dbSlug: string }>();
  const navigate = useNavigate();
  const [check, setCheck] = useState<Check | null>(null);

  function reload() {
    if (id) api.getCheck(id).then(setCheck);
  }

  useEffect(reload, [id]);

  if (!check) return null;

  return (
    <div className="min-h-screen bg-background px-6 py-10 sm:px-10">
      <div className="mx-auto max-w-5xl">
        <Breadcrumbs
          items={[
            { label: "Projects", to: "/" },
            { label: check.database.project.name, to: `/projects/${slug}` },
            { label: check.database.name, to: `/projects/${slug}/databases/${dbSlug}` },
            { label: check.name },
          ]}
        />

        <header className="mb-6 flex flex-wrap items-start justify-between gap-3">
          <div>
            <h1 className="text-2xl font-semibold tracking-tight text-foreground">{check.name}</h1>
            {check.description && <p className="mt-1 text-sm text-zinc-500 dark:text-zinc-400">{check.description}</p>}
            <dl className="mt-3 flex flex-wrap gap-x-6 gap-y-1 text-xs text-zinc-500 dark:text-zinc-400">
              <div>
                <dt className="inline font-semibold text-zinc-600 dark:text-zinc-300">Type:</dt> <dd className="inline">{check.type}</dd>
              </div>
              <div>
                <dt className="inline font-semibold text-zinc-600 dark:text-zinc-300">Schedule:</dt>{" "}
                <dd className="inline font-mono">{check.schedule}</dd>
              </div>
              <div>
                <dt className="inline font-semibold text-zinc-600 dark:text-zinc-300">Connector:</dt>{" "}
                <dd className="inline">{check.connector.name}</dd>
              </div>
              {check.secondary_connector && (
                <div>
                  <dt className="inline font-semibold text-zinc-600 dark:text-zinc-300">Secondary connector:</dt>{" "}
                  <dd className="inline">{check.secondary_connector.name}</dd>
                </div>
              )}
              <div>
                <dt className="inline font-semibold text-zinc-600 dark:text-zinc-300">Enabled:</dt>{" "}
                <dd className="inline">{check.enabled ? "yes" : "no"}</dd>
              </div>
            </dl>
          </div>
          <div className="flex shrink-0 flex-wrap gap-2">
            <RunNowButton checkId={check.id} onDone={reload} />
            <EnabledToggle checkId={check.id} enabled={check.enabled} onDone={reload} />
            <Link
              to={`/projects/${slug}/databases/${dbSlug}/checks/${check.id}/edit`}
              className="rounded-md border border-border px-3 py-1.5 text-sm font-medium text-zinc-700 transition-colors hover:bg-zinc-100 dark:text-zinc-200 dark:hover:bg-zinc-800"
            >
              Edit
            </Link>
            <DeleteButton
              confirmMessage={`Delete check "${check.name}"? This also deletes its run history.`}
              onDelete={async () => {
                await api.deleteCheck(check.id);
                navigate(`/projects/${slug}/databases/${dbSlug}`);
              }}
            />
          </div>
        </header>

        <section className="mb-6 rounded-xl border border-border bg-surface p-4 shadow-sm">
          <h2 className="mb-2 text-sm font-semibold text-zinc-700 dark:text-zinc-300">Config</h2>
          <pre className="overflow-x-auto rounded-lg bg-zinc-50 p-3 text-xs text-zinc-700 dark:bg-black/30 dark:text-zinc-300">
            {JSON.stringify(check.config, null, 2)}
          </pre>
        </section>

        <section>
          <h2 className="mb-2 text-sm font-semibold text-zinc-700 dark:text-zinc-300">Run history</h2>
          <div className="space-y-3">
            {check.runs.map((run) => (
              <div key={run.id} className="rounded-xl border border-border bg-surface p-4 shadow-sm">
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-3">
                    <StatusBadge status={run.status} />
                    <span className="text-sm text-zinc-500 dark:text-zinc-400">{formatDateTime(run.started_at)}</span>
                    {run.duration_ms !== null && <span className="text-xs text-zinc-400">{run.duration_ms}ms</span>}
                  </div>
                </div>
                {run.message && <p className="mt-2 text-sm text-zinc-700 dark:text-zinc-300">{run.message}</p>}
                {run.metrics !== null && Object.keys(run.metrics).length > 0 && (
                  <pre className="mt-2 overflow-x-auto rounded-lg bg-zinc-50 p-2 text-xs text-zinc-600 dark:bg-black/30 dark:text-zinc-400">
                    {JSON.stringify(run.metrics, null, 2)}
                  </pre>
                )}

                {run.rca && (
                  <div className="mt-3 rounded-lg border border-amber-200 bg-amber-50 p-3 text-xs dark:border-amber-500/20 dark:bg-amber-500/10">
                    <div className="flex items-center justify-between">
                      <div className="font-semibold text-amber-800 dark:text-amber-300">Root Cause Analysis</div>
                      {run.rca.confidence !== null && (
                        <span className="rounded-full bg-amber-200 px-2 py-0.5 text-[11px] font-medium text-amber-900 dark:bg-amber-500/20 dark:text-amber-200">
                          {Math.round(run.rca.confidence * 100)}% confidence
                        </span>
                      )}
                    </div>
                    <div className="mt-1 text-amber-900 dark:text-amber-200">{run.rca.summary}</div>
                    {run.rca.root_cause && (
                      <div className="mt-2 text-amber-900 dark:text-amber-200">
                        <span className="font-semibold">Root cause: </span>
                        {run.rca.root_cause}
                      </div>
                    )}
                    {run.rca.next_steps && (
                      <div className="mt-1 text-amber-700 dark:text-amber-400">
                        <span className="font-semibold">Next steps: </span>
                        {run.rca.next_steps}
                      </div>
                    )}
                    {run.rca.suggested_owner && (
                      <div className="mt-1 text-amber-700 dark:text-amber-400">
                        <span className="font-semibold">Suggested owner: </span>
                        {run.rca.suggested_owner}
                      </div>
                    )}
                    {run.rca.evidence !== null && (
                      <details className="mt-2">
                        <summary className="cursor-pointer text-amber-700 dark:text-amber-400">Evidence</summary>
                        <pre className="mt-1 overflow-x-auto rounded bg-white/60 p-2 dark:bg-black/30">
                          {JSON.stringify(run.rca.evidence, null, 2)}
                        </pre>
                      </details>
                    )}
                  </div>
                )}

              </div>
            ))}

            {check.runs.length === 0 && (
              <div className="rounded-xl border border-dashed border-border p-6 text-center text-sm text-zinc-500">
                No runs yet. Click &quot;Run now&quot; or wait for the scheduler.
              </div>
            )}
          </div>
        </section>
      </div>
    </div>
  );
}
