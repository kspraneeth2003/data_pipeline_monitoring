import { useEffect, useState } from "react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";
import { api, type Check, type CheckVersion } from "../lib/api";
import { Breadcrumbs } from "../components/Breadcrumbs";
import { formatDateTime } from "../lib/time";
import { StatusBadge } from "../components/StatusBadge";
import { RunNowButton } from "../components/RunNowButton";
import { EnabledToggle } from "../components/EnabledToggle";
import { DeleteButton } from "../components/DeleteButton";
import { CheckLogic, CheckSql } from "../components/CheckExplanation";
import { PinButton } from "../components/PinButton";
import { stageLabel } from "../lib/stages";

export function CheckDetail() {
  const { id, slug = "", dbSlug = "" } = useParams<{ id: string; slug: string; dbSlug: string }>();
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const [check, setCheck] = useState<Check | null>(null);
  const [versions, setVersions] = useState<CheckVersion[] | null>(null);

  // Subtab in the URL, so a link can point at the SQL or at the history
  // rather than at "the check, then click twice".
  const tab = (searchParams.get("tab") as TabKey) ?? "overview";

  function reload() {
    if (!id) return;
    api.getCheck(id).then(setCheck);
    api.listCheckVersions(id).then(setVersions).catch(() => setVersions([]));
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
            <h1 className="text-2xl font-semibold tracking-[0.08em] text-foreground">{check.name}</h1>
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
                <dt className="inline font-semibold text-zinc-600 dark:text-zinc-300">Stage:</dt>{" "}
                <dd className="inline">
                  <Link to={`/projects/${slug}?stage=${check.stage}`} className="hover:text-accent">
                    {stageLabel(check.stage)}
                  </Link>
                  {check.stage_locked && " (set by hand)"}
                </dd>
              </div>
              <div>
                <dt className="inline font-semibold text-zinc-600 dark:text-zinc-300">Origin:</dt>{" "}
                <dd className="inline">
                  {check.origin.toLowerCase()}
                  {/* Worth saying where it is read, not only in a settings
                      page. A derived check someone has edited is no longer
                      kept current when the DDL moves, and that is a change in
                      what the check is for. */}
                  {check.origin !== "HUMAN" && check.human_edited_at && " — edited, no longer agent-maintained"}
                </dd>
              </div>
              <div>
                <dt className="inline font-semibold text-zinc-600 dark:text-zinc-300">Enabled:</dt>{" "}
                <dd className="inline">{check.enabled ? "yes" : "no"}</dd>
              </div>
            </dl>
          </div>
          <div className="flex shrink-0 flex-wrap items-center gap-2">
            <PinButton checkId={check.id} pinned={check.pinned} checkName={check.name} onDone={reload} />
            <RunNowButton checkId={check.id} onDone={reload} />
            <EnabledToggle checkId={check.id} enabled={check.enabled} onDone={reload} />
            <Link
              to={`/projects/${slug}/databases/${dbSlug}/checks/${check.id}/edit`}
              className="rounded-md border border-border px-3 py-1.5 text-sm font-medium text-zinc-700 transition-colors dark:text-zinc-200 dark:hover:bg-zinc-800"
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

        <div className="mb-6 flex flex-wrap gap-1 border-b border-border">
          {TABS.map(({ key, label }) => (
            <button
              key={key}
              type="button"
              onClick={() => setSearchParams(key === "overview" ? {} : { tab: key }, { replace: true })}
              className={`-mb-px flex items-center gap-2 border-b-2 px-3 py-2 text-sm transition-colors ${
                tab === key
                  ? "border-accent font-medium text-foreground"
                  : "border-transparent text-zinc-500 hover:text-foreground"
              }`}
            >
              {label}
              {key === "versions" && versions && versions.length > 0 && (
                <span className="font-mono text-xs text-zinc-500">{versions.length}</span>
              )}
              {key === "runs" && check.runs.length > 0 && (
                <span className="font-mono text-xs text-zinc-500">{check.runs.length}</span>
              )}
            </button>
          ))}
        </div>

        {tab === "overview" && (
          <>
            <CheckLogic rationale={check.rationale} />
            <section className="mb-6 rounded-xl border border-border bg-surface p-4 shadow-sm">
              <h2 className="mb-2 text-sm font-semibold text-zinc-700 dark:text-zinc-300">Config</h2>
              <p className="mb-2 text-xs text-zinc-500">
                What this check compares, and the tolerances it allows. This is the editable part —
                the SQL is generated from it.
              </p>
              <pre className="overflow-x-auto rounded-lg bg-zinc-50 p-3 text-xs text-zinc-700 dark:bg-black/30 dark:text-zinc-300">
                {JSON.stringify(check.config, null, 2)}
              </pre>
            </section>
          </>
        )}

        {tab === "sql" && (
          <>
            <p className="mb-4 text-sm text-zinc-500">
              Generated from the config above, on every read. This is not a description of the
              query — it is the same function the engine calls to build what it executes, so the two
              cannot drift apart. Change the config and this changes with it.
            </p>
            <CheckSql statements={check.statements} statementsError={check.statements_error} />
          </>
        )}

        {tab === "versions" && (
          <VersionHistory
            versions={versions}
            onRestore={async (version) => {
              if (!confirm(`Restore version ${version}? This is applied as a new version on top — nothing in the history is removed.`)) return;
              await api.restoreCheckVersion(check.id, version);
              reload();
            }}
          />
        )}

        {tab === "runs" && (
        <section>
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
                        <span className="rounded-none bg-amber-200 px-2 py-0.5 text-[11px] font-medium text-amber-900 dark:bg-amber-500/20 dark:text-amber-200">
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
        )}
      </div>
    </div>
  );
}

type TabKey = "overview" | "sql" | "versions" | "runs";

const TABS: { key: TabKey; label: string }[] = [
  { key: "overview", label: "Overview" },
  // Behind a click rather than always on screen: the config is what someone
  // acts on, the SQL is what they consult to understand it.
  { key: "sql", label: "Underlying SQL" },
  { key: "versions", label: "Versions" },
  { key: "runs", label: "Runs" },
];

/**
 * What this check's logic has been, over time.
 *
 * The SQL shown per version is the snapshot taken when that version was saved,
 * not a re-render of its config through today's builders - which is the point.
 * Two configs side by side say very little; two queries say what actually
 * changed about the assertion.
 */
function VersionHistory({
  versions,
  onRestore,
}: {
  versions: CheckVersion[] | null;
  onRestore: (version: number) => void | Promise<void>;
}) {
  const [open, setOpen] = useState<string | null>(null);

  if (!versions) return <p className="text-sm text-zinc-500">Loading…</p>;
  if (versions.length === 0) {
    return (
      <div className="rounded-xl border border-dashed border-border p-6 text-center text-sm text-zinc-500">
        No versions recorded yet. One is written every time this check&rsquo;s logic changes.
      </div>
    );
  }

  return (
    <div className="space-y-3">
      {versions.map((version, index) => (
        <div key={version.id} className="rounded-xl border border-border bg-surface p-4 shadow-sm">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <div className="flex flex-wrap items-center gap-3">
              <span className="font-mono text-sm font-semibold text-foreground">v{version.version}</span>
              {index === 0 && (
                <span className="border border-accent/40 bg-accent/10 px-2 py-0.5 font-mono text-[11px] uppercase tracking-[0.1em] text-accent">
                  current
                </span>
              )}
              <span className="text-xs text-zinc-500">by {version.author}</span>
              <span className="text-xs text-zinc-500">{formatDateTime(version.created_at)}</span>
            </div>
            <div className="flex gap-2">
              <button
                type="button"
                onClick={() => setOpen(open === version.id ? null : version.id)}
                className="border border-border px-2 py-1 text-xs text-zinc-500 transition-colors hover:text-accent"
              >
                {open === version.id ? "Hide SQL" : "Show SQL"}
              </button>
              {index !== 0 && (
                <button
                  type="button"
                  onClick={() => onRestore(version.version)}
                  className="border border-border px-2 py-1 text-xs text-zinc-500 transition-colors hover:text-accent"
                >
                  Restore
                </button>
              )}
            </div>
          </div>

          {version.note && <p className="mt-2 text-sm text-zinc-600 dark:text-zinc-300">{version.note}</p>}

          {open === version.id && (
            <div className="mt-3 space-y-3">
              {version.statements_error ? (
                <p className="text-sm text-red-600 dark:text-red-400">
                  This version&rsquo;s config could not produce a statement: {version.statements_error}
                </p>
              ) : version.sql_text ? (
                <pre className="overflow-x-auto rounded-lg bg-zinc-50 p-3 text-xs leading-relaxed text-zinc-700 dark:bg-black/30 dark:text-zinc-300">
                  {version.sql_text}
                </pre>
              ) : (
                // The backfilled version 1 of a pre-existing check. Rendering
                // its SQL now would claim to be what that config produced at
                // the time, when it is only what it produces today.
                <p className="text-sm italic text-zinc-500">
                  No SQL snapshot — this version predates SQL being recorded.
                </p>
              )}
              <details>
                <summary className="cursor-pointer text-xs text-zinc-500">Config</summary>
                <pre className="mt-1 overflow-x-auto rounded-lg bg-zinc-50 p-3 text-xs text-zinc-600 dark:bg-black/30 dark:text-zinc-400">
                  {JSON.stringify(version.config, null, 2)}
                </pre>
              </details>
            </div>
          )}
        </div>
      ))}
    </div>
  );
}
