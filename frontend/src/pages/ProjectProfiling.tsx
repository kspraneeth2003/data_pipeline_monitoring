import { useCallback, useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";

import { AnomalyList } from "../components/AnomalyList";
import { Breadcrumbs } from "../components/Breadcrumbs";
import { StatusBadge } from "../components/StatusBadge";
import { profilingApi, type CatalogDatabase, type CatalogTable, type ProfileAnomaly } from "../lib/api";
import { relativeTime } from "../lib/time";

/**
 * What the data is actually like, across every table the project reaches.
 *
 * The table list is the warehouse's own catalog - every database, schema and
 * table, read live from INFORMATION_SCHEMA - not a list typed in here by hand.
 * Each table shows whether it is profiled; profiling is one click per table,
 * per schema or per database. Anomalies come first because they are the
 * reason to open the page.
 */

const RUN_STATUS: Record<string, string> = { SUCCEEDED: "PASSED", ERROR: "ERROR", RUNNING: "RUNNING" };

function baselineText(runs: number, needed: number): string {
  return needed > 0 ? `Learning normal · ${runs} of ${runs + needed} runs` : "Baseline ready";
}

function TableRow({
  table,
  slug,
  databaseId,
  onChanged,
}: {
  table: CatalogTable;
  slug: string;
  databaseId: string;
  onChanged: () => void;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const target = table.target;
  const run = target?.last_run ?? null;

  // Adding a table runs it straight away, so the first profile - and any
  // finding it has - is on screen without waiting for the hourly schedule.
  const act = async () => {
    setBusy(true);
    setError(null);
    try {
      const id = target
        ? target.id
        : (await profilingApi.createTarget(slug, { database_id: databaseId, object: table.object })).id;
      await profilingApi.runTarget(id);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Profile failed");
    } finally {
      setBusy(false);
      onChanged();
    }
  };

  return (
    <li className="grid grid-cols-1 gap-2 px-4 py-3 md:grid-cols-[minmax(0,2fr)_minmax(0,3fr)_auto] md:items-center">
      <div className="min-w-0">
        {target ? (
          <Link
            to={`/projects/${slug}/profiling/${target.id}`}
            className="break-all font-mono text-sm text-foreground hover:text-accent"
          >
            {table.table}
          </Link>
        ) : (
          <span className="break-all font-mono text-sm text-zinc-400">{table.table}</span>
        )}
        <p className="font-mono text-[11px] text-zinc-500">
          {table.row_count === null ? "—" : `${table.row_count.toLocaleString()} rows`}
        </p>
      </div>

      <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-zinc-400">
        {target ? (
          <>
            <StatusBadge status={run ? (RUN_STATUS[run.status] ?? "NONE") : "NONE"} />
            <span>{run ? `profiled ${relativeTime(run.finished_at ?? run.started_at)}` : "never profiled"}</span>
            {target.open_anomalies > 0 ? (
              <span className="font-mono text-red-400">{target.open_anomalies} open</span>
            ) : (
              run?.status === "SUCCEEDED" && <span>nothing unusual</span>
            )}
            <span title="History anomalies are judged once enough past runs exist. Findings are reported from the first run.">
              {baselineText(target.successful_runs, target.baseline_runs_needed)}
            </span>
            {!target.enabled && <span>paused</span>}
          </>
        ) : (
          <span className="text-zinc-500">Not profiled</span>
        )}
        {run?.status === "ERROR" && run.message && <p className="w-full text-amber-400">{run.message}</p>}
        {error && <p className="w-full text-amber-400">{error}</p>}
      </div>

      <button
        type="button"
        onClick={act}
        disabled={busy}
        className={`justify-self-start px-2.5 py-1 text-xs transition-colors disabled:opacity-50 md:justify-self-end ${
          target
            ? "border border-border text-foreground hover:border-accent"
            : "bg-accent font-medium text-accent-foreground hover:bg-accent-hover"
        }`}
      >
        {busy ? "Profiling…" : target ? "Run now" : "Profile"}
      </button>
    </li>
  );
}

function DatabaseSection({
  database,
  slug,
  onChanged,
}: {
  database: CatalogDatabase;
  slug: string;
  onChanged: () => void;
}) {
  const [busy, setBusy] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const tables = database.schemas.flatMap((s) => s.tables);
  const profiled = tables.filter((t) => t.target).length;

  const profileAll = async (schemaName?: string) => {
    setBusy(schemaName ?? "*");
    setNote(null);
    try {
      const result = await profilingApi.discover(slug, database.id, schemaName);
      setNote(
        result.added.length
          ? `Added ${result.added.length} table(s) to hourly profiling. Use Run now on any of them to profile immediately.`
          : "Every table here is already profiled.",
      );
    } catch (e) {
      setNote(e instanceof Error ? e.message : "Could not add tables");
    } finally {
      setBusy(null);
      onChanged();
    }
  };

  return (
    <section className="border border-border bg-surface">
      <header className="flex flex-col gap-2 border-b border-border px-4 py-3 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <h3 className="font-mono text-sm text-foreground">{database.name}</h3>
          <p className="text-xs text-zinc-500">
            {database.readable
              ? `${database.schemas.length} schema(s) · ${tables.length} table(s) · ${profiled} profiled`
              : "Not readable"}
          </p>
        </div>
        {database.readable && tables.length > profiled && (
          <button
            type="button"
            onClick={() => profileAll()}
            disabled={busy !== null}
            title="Adds every table in this database to hourly profiling. Each profile is one full scan of its table."
            className="self-start border border-border px-3 py-1.5 text-xs text-foreground transition-colors hover:border-accent disabled:opacity-50 sm:self-auto"
          >
            {busy === "*" ? "Adding…" : `Profile all ${tables.length - profiled} remaining`}
          </button>
        )}
      </header>

      {!database.readable && <p className="px-4 py-3 text-sm text-amber-400">{database.error}</p>}
      {note && <p className="border-b border-border px-4 py-2 text-xs text-zinc-400">{note}</p>}
      {database.readable && tables.length === 0 && (
        <p className="px-4 py-3 text-sm text-zinc-500">This database has no tables.</p>
      )}

      {database.schemas.map((schema) => {
        const remaining = schema.tables.filter((t) => !t.target).length;
        return (
          <div key={schema.name} className="border-b border-border last:border-b-0">
            <div className="flex items-center justify-between bg-surface-raised px-4 py-1.5">
              <span className="font-mono text-[11px] uppercase tracking-[0.15em] text-zinc-400">
                {schema.name} · {schema.tables.length}
              </span>
              {remaining > 0 && schema.tables.length > 1 && (
                <button
                  type="button"
                  onClick={() => profileAll(schema.name)}
                  disabled={busy !== null}
                  className="text-[11px] text-accent hover:underline disabled:opacity-50"
                >
                  {busy === schema.name ? "Adding…" : `Profile schema (${remaining})`}
                </button>
              )}
            </div>
            <ul className="divide-y divide-border">
              {schema.tables.map((table) => (
                <TableRow key={table.object} table={table} slug={slug} databaseId={database.id} onChanged={onChanged} />
              ))}
            </ul>
          </div>
        );
      })}
    </section>
  );
}

export function ProjectProfiling() {
  const { slug = "" } = useParams();
  const [catalog, setCatalog] = useState<CatalogDatabase[] | null>(null);
  const [anomalies, setAnomalies] = useState<ProfileAnomaly[]>([]);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => {
    profilingApi
      .getCatalog(slug)
      .then((c) => {
        setCatalog(c);
        setError(null);
      })
      .catch((e) => setError(e instanceof Error ? e.message : "Could not load the catalog"));
    profilingApi.listAnomalies(slug).then(setAnomalies).catch(() => setAnomalies([]));
  }, [slug]);

  useEffect(load, [load]);

  const acknowledge = async (id: string) => {
    await profilingApi.acknowledge(id);
    load();
  };

  const tableCount = catalog?.reduce((n, d) => n + d.schemas.reduce((m, s) => m + s.tables.length, 0), 0) ?? 0;
  const profiledCount =
    catalog?.reduce((n, d) => n + d.schemas.reduce((m, s) => m + s.tables.filter((t) => t.target).length, 0), 0) ?? 0;

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
            profiledCount === 0
              ? "Nothing is profiled yet. Pick a table below and press Profile."
              : "Nothing unusual on the latest profile of any table."
          }
        />
      </section>

      <section className="mt-8">
        <h2 className="mb-2 font-mono text-xs uppercase tracking-[0.15em] text-zinc-500">
          Tables{catalog ? ` · ${profiledCount} of ${tableCount} profiled` : ""}
        </h2>
        {error && <p className="text-sm text-amber-400">{error}</p>}
        {!catalog && !error && (
          <p className="text-sm text-zinc-500">Reading every database's table list from the warehouse…</p>
        )}
        {catalog && catalog.length === 0 && (
          <p className="text-sm text-zinc-500">
            This project has no databases yet.{" "}
            <Link to={`/projects/${slug}/databases/new`} className="text-accent hover:underline">
              Add one
            </Link>
            .
          </p>
        )}
        <div className="space-y-4">
          {catalog?.map((database) => (
            <DatabaseSection key={database.id} database={database} slug={slug} onChanged={load} />
          ))}
        </div>
      </section>
    </div>
  );
}
