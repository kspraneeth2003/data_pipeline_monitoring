import { useCallback, useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";

import { AnomalyList } from "../components/AnomalyList";
import { Breadcrumbs } from "../components/Breadcrumbs";
import { StatusBadge } from "../components/StatusBadge";
import { api, profilingApi, type Database, type ProfileAnomaly, type ProfileTarget } from "../lib/api";
import { relativeTime } from "../lib/time";

/**
 * What the data is actually like, table by table, and what changed.
 *
 * The counterpart to the checks. A check asserts what someone decided should
 * be true; a profile measures what is, and compares each column with its own
 * past. Anomalies come first on this page because they are the reason to open
 * it - the table list is where profiling is set up and inspected.
 */

const RUN_STATUS: Record<string, string> = { SUCCEEDED: "PASSED", ERROR: "ERROR", RUNNING: "RUNNING" };

function AddTable({ slug, databases, onAdded }: { slug: string; databases: Database[]; onAdded: () => void }) {
  const [databaseId, setDatabaseId] = useState("");
  const [object, setObject] = useState("");
  const [busy, setBusy] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);

  const selected = databaseId || databases[0]?.id || "";

  const add = async (event: React.FormEvent) => {
    event.preventDefault();
    setBusy("add");
    setNote(null);
    try {
      const target = await profilingApi.createTarget(slug, { database_id: selected, object });
      setObject("");
      setNote(`Added ${target.object}. Run it to take the first profile.`);
      onAdded();
    } catch (error) {
      setNote(error instanceof Error ? error.message : "Could not add the table");
    } finally {
      setBusy(null);
    }
  };

  const discover = async () => {
    setBusy("discover");
    setNote(null);
    try {
      const result = await profilingApi.discover(slug, selected);
      setNote(
        result.added.length
          ? `Added ${result.added.length} table(s). Each is profiled hourly; run one now to see it immediately.`
          : `Every table in this database is already profiled (${result.tables.length}).`,
      );
      onAdded();
    } catch (error) {
      setNote(error instanceof Error ? error.message : "Could not list tables");
    } finally {
      setBusy(null);
    }
  };

  if (databases.length === 0) {
    return (
      <p className="text-sm text-zinc-500">
        This project has no databases yet.{" "}
        <Link to={`/projects/${slug}/databases/new`} className="text-accent hover:underline">
          Add one
        </Link>{" "}
        to profile its tables.
      </p>
    );
  }

  return (
    <div className="border border-border bg-surface p-4">
      <form onSubmit={add} className="flex flex-col gap-3 sm:flex-row sm:items-end">
        <label className="flex flex-col gap-1 text-xs text-zinc-500">
          Database
          <select
            value={selected}
            onChange={(e) => setDatabaseId(e.target.value)}
            className="border border-border bg-background px-2 py-1.5 font-mono text-sm text-foreground"
          >
            {databases.map((d) => (
              <option key={d.id} value={d.id}>
                {d.name}
              </option>
            ))}
          </select>
        </label>
        <label className="flex flex-1 flex-col gap-1 text-xs text-zinc-500">
          Table
          <input
            value={object}
            onChange={(e) => setObject(e.target.value)}
            placeholder="SCHEMA.TABLE, e.g. SILVER.PRODUCTS"
            className="border border-border bg-background px-2 py-1.5 font-mono text-sm text-foreground placeholder:text-zinc-600"
          />
        </label>
        <button
          type="submit"
          disabled={!object.trim() || busy !== null}
          className="bg-accent px-3 py-1.5 text-sm font-medium text-accent-foreground transition-colors hover:bg-accent-hover disabled:opacity-50"
        >
          {busy === "add" ? "Adding…" : "Add table"}
        </button>
        <button
          type="button"
          onClick={discover}
          disabled={busy !== null}
          title="Adds every base table in the selected database. Each profile is one full scan per run."
          className="border border-border px-3 py-1.5 text-sm text-foreground transition-colors hover:border-accent disabled:opacity-50"
        >
          {busy === "discover" ? "Listing…" : "Profile every table"}
        </button>
      </form>
      {note && <p className="mt-3 text-sm text-zinc-400">{note}</p>}
    </div>
  );
}

function TargetRow({ target, slug, onChanged }: { target: ProfileTarget; slug: string; onChanged: () => void }) {
  const [running, setRunning] = useState(false);
  const run = target.last_run;

  const runNow = async () => {
    setRunning(true);
    try {
      await profilingApi.runTarget(target.id);
    } finally {
      setRunning(false);
      onChanged();
    }
  };

  return (
    <tr className="border-t border-border align-top">
      <td className="px-4 py-3">
        <Link to={`/projects/${slug}/profiling/${target.id}`} className="font-mono text-sm text-foreground hover:text-accent">
          {target.object}
        </Link>
        {run?.status === "ERROR" && run.message && (
          <p className="mt-1 max-w-md text-xs text-amber-400">{run.message}</p>
        )}
        {!target.enabled && <p className="mt-1 text-xs text-zinc-500">Paused</p>}
      </td>
      <td className="px-4 py-3 font-mono text-sm text-foreground">{run?.row_count?.toLocaleString() ?? "—"}</td>
      <td className="px-4 py-3 font-mono text-sm text-foreground">{run?.column_count ?? "—"}</td>
      <td className="px-4 py-3 text-sm text-zinc-400">
        {target.baseline_runs_needed > 0 ? (
          <span title="History anomalies are judged once enough past runs exist. Findings are reported from the first run.">
            Learning · {target.successful_runs}/{target.successful_runs + target.baseline_runs_needed}
          </span>
        ) : (
          "Ready"
        )}
      </td>
      <td className="px-4 py-3 text-sm">
        {target.open_anomalies > 0 ? (
          <span className="font-mono text-red-400">{target.open_anomalies} open</span>
        ) : (
          <span className="text-zinc-500">none</span>
        )}
      </td>
      <td className="px-4 py-3">
        {run ? <StatusBadge status={RUN_STATUS[run.status] ?? "NONE"} /> : <StatusBadge status="NONE" />}
        <p className="mt-1 text-xs text-zinc-500">{run ? relativeTime(run.finished_at ?? run.started_at) : "never run"}</p>
      </td>
      <td className="px-4 py-3 text-right">
        <button
          type="button"
          onClick={runNow}
          disabled={running}
          className="border border-border px-2.5 py-1 text-xs text-foreground transition-colors hover:border-accent disabled:opacity-50"
        >
          {running ? "Profiling…" : "Run now"}
        </button>
      </td>
    </tr>
  );
}

export function ProjectProfiling() {
  const { slug = "" } = useParams();
  const [targets, setTargets] = useState<ProfileTarget[] | null>(null);
  const [anomalies, setAnomalies] = useState<ProfileAnomaly[]>([]);
  const [databases, setDatabases] = useState<Database[]>([]);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => {
    profilingApi
      .listTargets(slug)
      .then((t) => {
        setTargets(t);
        setError(null);
      })
      .catch((e) => setError(e instanceof Error ? e.message : "Could not load profiling"));
    profilingApi.listAnomalies(slug).then(setAnomalies).catch(() => setAnomalies([]));
  }, [slug]);

  useEffect(load, [load]);
  useEffect(() => {
    api.listDatabases(slug).then(setDatabases).catch(() => setDatabases([]));
  }, [slug]);

  const acknowledge = async (id: string) => {
    await profilingApi.acknowledge(id);
    load();
  };

  return (
    <div className="mx-auto max-w-6xl px-6 py-8">
      <Breadcrumbs items={[{ label: slug, to: `/projects/${slug}` }, { label: "Profiling" }]} />

      <h1 className="text-xl font-semibold text-foreground">Profiling</h1>
      <p className="mt-1 max-w-3xl text-sm text-zinc-500">
        What each column is actually like - null rate, distinct values, ranges, lengths - measured every run and
        compared with its own history. Needs no rules and no code, so it catches what nobody wrote a check for,
        including a mapping bug the pipeline's own code agrees with.
      </p>

      <section className="mt-6">
        <h2 className="mb-2 font-mono text-xs uppercase tracking-[0.15em] text-zinc-500">
          Open anomalies · {anomalies.length}
        </h2>
        <AnomalyList
          anomalies={anomalies}
          slug={slug}
          onAcknowledge={acknowledge}
          empty={
            targets && targets.length === 0
              ? "Nothing is profiled yet. Add a table below."
              : "Nothing unusual on the latest profile of any table."
          }
        />
      </section>

      <section className="mt-8">
        <h2 className="mb-2 font-mono text-xs uppercase tracking-[0.15em] text-zinc-500">Profiled tables</h2>
        <AddTable slug={slug} databases={databases} onAdded={load} />

        {error && <p className="mt-4 text-sm text-amber-400">{error}</p>}

        {targets && targets.length > 0 && (
          <div className="mt-4 overflow-x-auto border border-border bg-surface">
            <table className="w-full text-left">
              <thead>
                <tr className="font-mono text-[11px] uppercase tracking-[0.15em] text-zinc-500">
                  <th className="px-4 py-2.5 font-normal">Table</th>
                  <th className="px-4 py-2.5 font-normal">Rows</th>
                  <th className="px-4 py-2.5 font-normal">Columns</th>
                  <th className="px-4 py-2.5 font-normal">Baseline</th>
                  <th className="px-4 py-2.5 font-normal">Anomalies</th>
                  <th className="px-4 py-2.5 font-normal">Last profile</th>
                  <th className="px-4 py-2.5" />
                </tr>
              </thead>
              <tbody>
                {targets.map((t) => (
                  <TargetRow key={t.id} target={t} slug={slug} onChanged={load} />
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </div>
  );
}
