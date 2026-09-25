import { Fragment, useCallback, useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";

import { AnomalyList } from "../components/AnomalyList";
import { Breadcrumbs } from "../components/Breadcrumbs";
import { profilingApi, type CatalogDatabase, type CatalogTable, type ProfileAnomaly } from "../lib/api";
import { relativeTime } from "../lib/time";

/**
 * What the data is actually like, across every table the project reaches.
 *
 * The table list is the warehouse's own catalog - every database, schema and
 * table, read live from INFORMATION_SCHEMA - so nothing has to be typed in.
 * One row per table, one action per row. A profile does not pass or fail, so
 * its state is a dot and a time rather than a PASSED badge; what it found is
 * the Findings column.
 */

function Stat({ label, value, tone }: { label: string; value: string; tone?: "alert" | "warn" }) {
  const color = tone === "alert" ? "text-red-400" : tone === "warn" ? "text-amber-400" : "text-foreground";
  return (
    <div className="border border-border bg-surface px-4 py-3">
      <p className="font-mono text-[11px] uppercase tracking-[0.15em] text-zinc-500">{label}</p>
      <p className={`mt-1 font-mono text-2xl ${color}`}>{value}</p>
    </div>
  );
}

function LastProfile({ table }: { table: CatalogTable }) {
  const target = table.target;
  const run = target?.last_run;
  if (!target) return <span className="text-zinc-600">Not profiled</span>;
  if (!run) return <span className="text-zinc-500">Queued</span>;

  const failed = run.status === "ERROR";
  const learning = target.baseline_runs_needed > 0;
  return (
    <span className="inline-flex items-center gap-2" title={failed ? (run.message ?? "") : undefined}>
      <span className={`h-1.5 w-1.5 shrink-0 ${failed ? "bg-amber-500" : "bg-emerald-500"}`} />
      <span className={failed ? "text-amber-400" : "text-zinc-300"}>
        {failed ? "Failed" : relativeTime(run.finished_at ?? run.started_at)}
      </span>
      {learning && !failed && (
        <span
          className="text-[11px] text-zinc-500"
          title={`History anomalies start after ${target.successful_runs + target.baseline_runs_needed} runs. Findings such as an all-NULL column are reported from the first run.`}
        >
          learning {target.successful_runs}/{target.successful_runs + target.baseline_runs_needed}
        </span>
      )}
    </span>
  );
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
  const target = table.target;
  const [schema, name] = [table.object.split(".")[1], table.table];

  // Adding a table runs it straight away, so its first profile - and any
  // finding - appears without waiting for the hourly schedule.
  const act = async () => {
    setBusy(true);
    try {
      const id = target
        ? target.id
        : (await profilingApi.createTarget(slug, { database_id: databaseId, object: table.object })).id;
      await profilingApi.runTarget(id);
    } finally {
      setBusy(false);
      onChanged();
    }
  };

  const label = (
    <span className="truncate font-mono text-sm" title={table.object}>
      <span className="text-zinc-500">{schema}.</span>
      <span className={target ? "text-foreground" : "text-zinc-400"}>{name}</span>
    </span>
  );

  return (
    <tr className="border-t border-border transition-colors hover:bg-white/[0.02]">
      <td className="max-w-0 px-4 py-2.5">
        {target ? (
          <Link to={`/projects/${slug}/profiling/${target.id}`} className="block truncate hover:[&_span]:text-accent">
            {label}
          </Link>
        ) : (
          <div className="truncate">{label}</div>
        )}
      </td>
      <td className="whitespace-nowrap px-4 py-2.5 text-right font-mono text-sm text-zinc-400">
        {table.row_count === null ? "—" : table.row_count.toLocaleString()}
      </td>
      <td className="whitespace-nowrap px-4 py-2.5 text-sm">
        <LastProfile table={table} />
      </td>
      <td className="whitespace-nowrap px-4 py-2.5 text-sm">
        {target && target.open_anomalies > 0 ? (
          <Link
            to={`/projects/${slug}/profiling/${target.id}`}
            className="border border-red-500/40 bg-red-500/10 px-1.5 py-0.5 font-mono text-xs text-red-400 hover:border-red-400"
          >
            {target.open_anomalies} open
          </Link>
        ) : (
          <span className="text-zinc-600">—</span>
        )}
      </td>
      <td className="whitespace-nowrap px-4 py-2.5 text-right">
        <button
          type="button"
          onClick={act}
          disabled={busy}
          className={`w-20 py-1 text-xs transition-colors disabled:opacity-50 ${
            target
              ? "border border-border text-zinc-300 hover:border-accent hover:text-foreground"
              : "border border-accent-line bg-accent-soft text-accent hover:bg-accent hover:text-accent-foreground"
          }`}
        >
          {busy ? "…" : target ? "Run" : "Profile"}
        </button>
      </td>
    </tr>
  );
}

function DatabaseRows({
  database,
  slug,
  onChanged,
}: {
  database: CatalogDatabase;
  slug: string;
  onChanged: () => void;
}) {
  const [busy, setBusy] = useState(false);
  const tables = database.schemas.flatMap((s) => s.tables);
  const remaining = tables.filter((t) => !t.target).length;

  const profileAll = async () => {
    setBusy(true);
    try {
      await profilingApi.discover(slug, database.id);
    } finally {
      setBusy(false);
      onChanged();
    }
  };

  return (
    <Fragment>
      <tr className="border-t border-border bg-surface-raised">
        <td colSpan={5} className="px-4 py-2">
          <div className="flex items-center justify-between gap-4">
            <div className="flex min-w-0 items-baseline gap-3">
              <span className="truncate font-mono text-xs font-medium tracking-wide text-foreground">{database.name}</span>
              <span className="whitespace-nowrap text-xs text-zinc-500">
                {database.readable ? `${tables.length - remaining} of ${tables.length} profiled` : "not readable"}
              </span>
            </div>
            {database.readable && remaining > 0 && (
              <button
                type="button"
                onClick={profileAll}
                disabled={busy}
                title="Adds every remaining table in this database to hourly profiling. Each profile is one scan of its table."
                className="whitespace-nowrap text-xs text-accent hover:underline disabled:opacity-50"
              >
                {busy ? "Adding…" : `Profile all ${remaining}`}
              </button>
            )}
          </div>
        </td>
      </tr>
      {!database.readable && (
        <tr className="border-t border-border">
          <td colSpan={5} className="px-4 py-2.5 text-xs text-zinc-500" title={database.error ?? undefined}>
            This connection can't read {database.name} - it was removed, or its role hasn't been granted access.
          </td>
        </tr>
      )}
      {database.readable && tables.length === 0 && (
        <tr className="border-t border-border">
          <td colSpan={5} className="px-4 py-2.5 text-xs text-zinc-500">No tables.</td>
        </tr>
      )}
      {tables.map((table) => (
        <TableRow key={table.object} table={table} slug={slug} databaseId={database.id} onChanged={onChanged} />
      ))}
    </Fragment>
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

  const tables = catalog?.flatMap((d) => d.schemas.flatMap((s) => s.tables)) ?? [];
  const profiled = tables.filter((t) => t.target).length;
  const unreadable = catalog?.filter((d) => !d.readable).length ?? 0;

  return (
    <div className="mx-auto max-w-6xl px-6 py-8">
      <Breadcrumbs items={[{ label: slug, to: `/projects/${slug}` }, { label: "Profiling" }]} />

      <h1 className="text-xl font-semibold text-foreground">Profiling</h1>
      <p className="mt-1 max-w-2xl text-sm text-zinc-500">
        Each column's null rate, distinct values, ranges and lengths, compared with its own history. No rules
        needed - it catches what nobody wrote a check for.
      </p>

      <div className="mt-6 grid grid-cols-2 gap-3 sm:grid-cols-3">
        <Stat label="Tables profiled" value={catalog ? `${profiled} / ${tables.length}` : "—"} />
        <Stat label="Open findings" value={String(anomalies.length)} tone={anomalies.length ? "alert" : undefined} />
        {unreadable > 0 && <Stat label="Unreadable databases" value={String(unreadable)} tone="warn" />}
      </div>

      {anomalies.length > 0 && (
        <section className="mt-8">
          <h2 className="mb-2 font-mono text-xs uppercase tracking-[0.15em] text-zinc-500">Open findings</h2>
          <AnomalyList anomalies={anomalies} slug={slug} onAcknowledge={acknowledge} empty="" />
        </section>
      )}

      <section className="mt-8">
        <h2 className="mb-2 font-mono text-xs uppercase tracking-[0.15em] text-zinc-500">Tables</h2>
        {error && <p className="text-sm text-amber-400">{error}</p>}
        {!catalog && !error && <p className="text-sm text-zinc-500">Reading table lists from the warehouse…</p>}
        {catalog && catalog.length === 0 && (
          <p className="text-sm text-zinc-500">
            This project has no databases yet.{" "}
            <Link to={`/projects/${slug}/databases/new`} className="text-accent hover:underline">
              Add one
            </Link>
            .
          </p>
        )}
        {catalog && catalog.length > 0 && (
          <div className="border border-border bg-surface">
            <table className="w-full table-fixed text-left">
              <colgroup>
                <col />
                <col className="w-28" />
                <col className="w-56" />
                <col className="w-28" />
                <col className="w-28" />
              </colgroup>
              <thead>
                <tr className="font-mono text-[11px] uppercase tracking-[0.15em] text-zinc-500">
                  <th className="px-4 py-2.5 font-normal">Table</th>
                  <th className="px-4 py-2.5 text-right font-normal">Rows</th>
                  <th className="px-4 py-2.5 font-normal">Last profile</th>
                  <th className="px-4 py-2.5 font-normal">Findings</th>
                  <th className="px-4 py-2.5" />
                </tr>
              </thead>
              <tbody>
                {catalog.map((database) => (
                  <DatabaseRows key={database.id} database={database} slug={slug} onChanged={load} />
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </div>
  );
}
