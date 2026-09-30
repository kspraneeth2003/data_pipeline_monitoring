import { useCallback, useEffect, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";

import { AnomalyList } from "../components/AnomalyList";
import { Breadcrumbs } from "../components/Breadcrumbs";
import { Sparkline, type SparkPoint } from "../components/Sparkline";
import { profilingApi, type ColumnProfile, type ProfileTargetDetail } from "../lib/api";
import { formatDateTime, relativeTime } from "../lib/time";

/**
 * One table's profile: every column's current numbers, how they have moved,
 * and what was flagged.
 *
 * The column table is the primary view and doubles as the data view for the
 * sparklines - each trend sits next to the exact current value it ends at.
 * Text columns show lengths, not values: the profile never stores a value
 * from a text column, so there is nothing else it could show.
 */

const pct = (v: number) => `${(v * 100).toFixed(1)}%`;
const num = (v: number) =>
  Math.abs(v) >= 1000 || Number.isInteger(v) ? Math.round(v).toLocaleString() : v.toLocaleString(undefined, { maximumFractionDigits: 2 });

/** Common cron shapes in words; anything else is shown as written. */
function scheduleText(cron: string): string {
  if (cron === "0 * * * *") return "hourly";
  const everyN = /^\*\/(\d+) \* \* \* \*$/.exec(cron);
  if (everyN) return `every ${everyN[1]} min`;
  if (/^0 \d+ \* \* \*$/.test(cron)) return "daily";
  return cron;
}

/** Warehouse timestamps to the minute - milliseconds are noise in a range. */
const trimTime = (v: string | null) => (v && /^\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}/.test(v) ? v.slice(0, 16) : v);

/** Numeric and text (length) columns show their stored value as-is; temporal ones are trimmed to the minute. */
const minText = (c: ColumnProfile) => (c.family === "TEMPORAL" ? trimTime(c.min_value) : c.min_value) ?? "—";
const maxText = (c: ColumnProfile) => (c.family === "TEMPORAL" ? trimTime(c.max_value) : c.max_value) ?? "—";

type SortKey = "column" | "null" | "distinct" | "blank" | "min" | "max" | "mean";
type Sort = { key: SortKey; dir: "asc" | "desc" };

/** A column's blank rate as a fraction of non-null rows - the same number the Blank cell shows. */
function blankRatio(c: ColumnProfile): number | null {
  const nonNull = c.row_count - c.null_count;
  return c.blank_count === null || nonNull === 0 ? null : c.blank_count / nonNull;
}

function sortValue(c: ColumnProfile, key: SortKey): string | number | null {
  switch (key) {
    case "column":
      return c.column_name;
    case "null":
      return c.null_ratio;
    case "distinct":
      return c.distinct_count;
    case "blank":
      return blankRatio(c);
    case "min":
      return c.min_numeric;
    case "max":
      return c.max_numeric;
    case "mean":
      return c.mean_numeric;
  }
}

/** Up and down triangles stacked, like ⇕ - the direction not currently active is dimmed rather than hidden,
 *  so the header always shows "this can be sorted" and, once clicked, which way it currently is. */
function SortIcon({ direction }: { direction: "asc" | "desc" | null }) {
  return (
    <svg width="8" height="11" viewBox="0 0 8 11" aria-hidden="true" className="shrink-0">
      <path d="M4 0L7.5 4.5H0.5L4 0Z" fill="currentColor" opacity={direction === "asc" ? 1 : 0.35} />
      <path d="M4 11L0.5 6.5H7.5L4 11Z" fill="currentColor" opacity={direction === "desc" ? 1 : 0.35} />
    </svg>
  );
}

function SortableHeader({
  label,
  sortKey,
  sort,
  onSort,
  title,
  align = "left",
}: {
  label: string;
  sortKey: SortKey;
  sort: Sort | null;
  onSort: (key: SortKey) => void;
  title?: string;
  align?: "left" | "right";
}) {
  const active = sort?.key === sortKey;
  return (
    <th className={`font-normal ${align === "right" ? "text-right" : "text-left"}`} title={title}>
      <button
        type="button"
        onClick={() => onSort(sortKey)}
        className={`inline-flex w-full items-center gap-1.5 px-4 py-2.5 transition-colors hover:text-foreground ${
          align === "right" ? "flex-row-reverse" : ""
        } ${active ? "text-foreground" : "text-zinc-500"}`}
      >
        {label}
        <SortIcon direction={active ? sort.dir : null} />
      </button>
    </th>
  );
}

export function ProfileTable() {
  const { slug = "", targetId = "" } = useParams();
  const navigate = useNavigate();
  const [detail, setDetail] = useState<ProfileTargetDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [running, setRunning] = useState(false);
  const [showAcknowledged, setShowAcknowledged] = useState(false);
  const [sort, setSort] = useState<Sort | null>(null);

  const load = useCallback(() => {
    profilingApi
      .getTarget(targetId)
      .then((d) => {
        setDetail(d);
        setError(null);
      })
      .catch((e) => setError(e instanceof Error ? e.message : "Could not load this table"));
  }, [targetId]);

  useEffect(load, [load]);

  if (error) return <div className="mx-auto max-w-6xl px-6 py-8 text-sm text-amber-400">{error}</div>;
  if (!detail) return <div className="mx-auto max-w-6xl px-6 py-8 text-sm text-zinc-500">Loading…</div>;

  const run = detail.last_run;
  const latestRunId = detail.history[detail.history.length - 1]?.run_id;
  const flaggedRuns = (column: string | null, metrics: string[]) =>
    new Set(detail.anomalies.filter((a) => a.column_name === column && metrics.includes(a.metric)).map((a) => a.run_id));

  const rowFlags = flaggedRuns(null, ["row_volume", "row_count"]);
  const rowSeries: SparkPoint[] = detail.history.map((h) => ({
    at: h.at,
    value: h.row_count,
    flagged: rowFlags.has(h.run_id),
  }));

  // "Current" is what the latest successful profile found; older ones are history.
  const current = detail.anomalies.filter((a) => a.run_id === latestRunId && (showAcknowledged || !a.acknowledged_at));
  const past = detail.anomalies.filter((a) => a.run_id !== latestRunId);

  const runNow = async () => {
    setRunning(true);
    try {
      await profilingApi.runTarget(detail.id);
    } finally {
      setRunning(false);
      load();
    }
  };

  const togglePaused = async () => {
    await profilingApi.updateTarget(detail.id, { enabled: !detail.enabled });
    load();
  };

  const remove = async () => {
    if (!window.confirm(`Stop profiling ${detail.object} and delete its profile history? This cannot be undone.`)) return;
    await profilingApi.deleteTarget(detail.id);
    navigate(`/projects/${slug}/profiling`);
  };

  const acknowledge = async (id: string) => {
    await profilingApi.acknowledge(id);
    load();
  };

  // Click cycles a header through ascending, descending, then back to the
  // table's own column order - so "un-sorting" is always one more click away
  // rather than needing a separate reset control.
  const toggleSort = (key: SortKey) => {
    setSort((prev) => {
      if (!prev || prev.key !== key) return { key, dir: "asc" };
      if (prev.dir === "asc") return { key, dir: "desc" };
      return null;
    });
  };

  // Nulls sort to the end regardless of direction - "unranked" is not the
  // same as "smallest", and ascending sort putting every all-NULL column at
  // the top would bury the columns that actually have the lowest values.
  const sortedColumns = [...detail.columns].sort((a, b) => {
    if (!sort) return a.ordinal - b.ordinal;
    const av = sortValue(a, sort.key);
    const bv = sortValue(b, sort.key);
    if (av === null && bv === null) return 0;
    if (av === null) return 1;
    if (bv === null) return -1;
    const cmp = typeof av === "string" || typeof bv === "string" ? String(av).localeCompare(String(bv)) : av - bv;
    return sort.dir === "asc" ? cmp : -cmp;
  });

  return (
    <div className="mx-auto max-w-6xl px-6 py-8">
      <Breadcrumbs
        items={[
          { label: slug, to: `/projects/${slug}` },
          { label: "Profiling", to: `/projects/${slug}/profiling` },
          { label: detail.object },
        ]}
      />

      <div className="flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
        <div>
          <h1 className="font-mono text-lg text-foreground">{detail.object}</h1>
          <p className="mt-1 text-sm text-zinc-500">
            {[
              run?.row_count != null ? `${run.row_count.toLocaleString()} rows` : null,
              run?.column_count != null ? `${run.column_count} columns` : null,
              run ? `profiled ${relativeTime(run.finished_at ?? run.started_at)}` : "not profiled yet",
              detail.enabled ? `runs ${scheduleText(detail.schedule)}` : "paused",
            ]
              .filter(Boolean)
              .join(" · ")}
          </p>
          {detail.baseline_runs_needed > 0 && (
            <p className="mt-1 text-xs text-zinc-500">
              Learning normal: {detail.successful_runs} of {detail.successful_runs + detail.baseline_runs_needed} runs.
              Trends are judged once that is reached; findings like an all-NULL column are flagged from the first run.
            </p>
          )}
        </div>
        <div className="flex shrink-0 gap-2">
          <button
            type="button"
            onClick={runNow}
            disabled={running}
            className="bg-accent px-3 py-1.5 text-sm font-medium text-accent-foreground transition-colors hover:bg-accent-hover disabled:opacity-50"
          >
            {running ? "Profiling…" : "Run now"}
          </button>
          <button
            type="button"
            onClick={togglePaused}
            className="border border-border px-3 py-1.5 text-sm text-foreground transition-colors hover:border-accent"
          >
            {detail.enabled ? "Pause" : "Resume"}
          </button>
          <button
            type="button"
            onClick={remove}
            className="border border-border px-3 py-1.5 text-sm text-zinc-400 transition-colors hover:border-red-500 hover:text-red-400"
          >
            Delete
          </button>
        </div>
      </div>

      {run?.status === "ERROR" && (
        <p className="mt-4 border border-amber-500/40 bg-amber-500/10 px-4 py-3 text-sm text-amber-300">
          The last profile failed: {run.message}
        </p>
      )}

      <section className="mt-6">
        <div className="mb-2 flex items-center justify-between">
          <h2 className="font-mono text-xs uppercase tracking-[0.15em] text-zinc-500">Now · {current.length}</h2>
          <label className="flex items-center gap-2 text-xs text-zinc-500">
            <input type="checkbox" checked={showAcknowledged} onChange={(e) => setShowAcknowledged(e.target.checked)} />
            Show acknowledged
          </label>
        </div>
        <AnomalyList
          anomalies={current}
          slug={slug}
          showTable={false}
          onAcknowledge={acknowledge}
          empty="Nothing unusual on the latest profile."
        />
      </section>

      <section className="mt-8">
        <div className="mb-2 flex items-center justify-between">
          <h2 className="font-mono text-xs uppercase tracking-[0.15em] text-zinc-500">
            Columns · {detail.columns.length}
          </h2>
          {rowSeries.length > 1 && (
            <span className="flex items-center gap-2 text-xs text-zinc-500">
              Rows, last {rowSeries.length} runs
              <Sparkline points={rowSeries} format={num} label="Row count" />
            </span>
          )}
        </div>
        {detail.columns.length === 0 ? (
          <p className="border border-border bg-surface px-4 py-6 text-sm text-zinc-500">
            No successful profile yet. Run one to see this table's columns.
          </p>
        ) : (
          <div className="overflow-x-auto border border-border bg-surface">
            <table className="w-full text-left">
              <thead>
                <tr className="font-mono text-[11px] uppercase tracking-[0.15em] text-zinc-500">
                  <SortableHeader label="Column" sortKey="column" sort={sort} onSort={toggleSort} />
                  <SortableHeader
                    label="Null"
                    sortKey="null"
                    sort={sort}
                    onSort={toggleSort}
                    title="% of rows where the value is NULL - the field has nothing stored"
                  />
                  <SortableHeader label="Distinct" sortKey="distinct" sort={sort} onSort={toggleSort} />
                  <SortableHeader
                    label="Blank"
                    sortKey="blank"
                    sort={sort}
                    onSort={toggleSort}
                    title="% of non-null rows that are an empty or whitespace-only string - a value that passes NOT NULL but carries nothing"
                  />
                  <SortableHeader
                    label="Min"
                    sortKey="min"
                    sort={sort}
                    onSort={toggleSort}
                    title="Text columns sort and show the shortest value's length, not the value"
                  />
                  <SortableHeader
                    label="Max"
                    sortKey="max"
                    sort={sort}
                    onSort={toggleSort}
                    title="Text columns sort and show the longest value's length, not the value"
                  />
                  <SortableHeader label="Mean" sortKey="mean" sort={sort} onSort={toggleSort} />
                </tr>
              </thead>
              <tbody>
                {sortedColumns.map((c) => {
                  const flagged = current.some((a) => a.column_name === c.column_name);
                  const nonNull = c.row_count - c.null_count;
                  return (
                    <tr key={c.column_name} className="border-t border-border align-middle">
                      <td className="px-4 py-2.5">
                        <span className="font-mono text-sm text-foreground">{c.column_name}</span>
                        {flagged && <span className="ml-2 font-mono text-[11px] text-red-400">flagged</span>}
                        <p className="font-mono text-[11px] text-zinc-500">{c.data_type.toLowerCase()}</p>
                      </td>
                      <td
                        className={`px-4 py-2.5 font-mono text-sm ${
                          c.null_ratio === 1 && c.row_count > 0 ? "text-red-400" : "text-foreground"
                        }`}
                      >
                        {c.null_ratio === null ? "—" : pct(c.null_ratio)}
                      </td>
                      <td className="px-4 py-2.5 font-mono text-sm text-foreground">
                        {c.distinct_count === null ? "—" : c.distinct_count.toLocaleString()}
                        {c.distinct_count !== null && nonNull > 0 && (
                          <span className="ml-1 text-[11px] text-zinc-500">
                            ({pct(Math.min(c.distinct_count / nonNull, 1))})
                          </span>
                        )}
                      </td>
                      <td className="px-4 py-2.5 font-mono text-sm text-foreground">
                        {c.blank_count === null || nonNull === 0 ? "—" : pct(c.blank_count / nonNull)}
                      </td>
                      <td className="whitespace-nowrap px-4 py-2.5 font-mono text-xs text-foreground">{minText(c)}</td>
                      <td className="whitespace-nowrap px-4 py-2.5 font-mono text-xs text-foreground">{maxText(c)}</td>
                      <td className="whitespace-nowrap px-4 py-2.5 font-mono text-sm text-foreground">
                        {c.mean_numeric === null
                          ? "—"
                          : c.family === "BOOLEAN"
                            ? `${pct(c.mean_numeric)} true`
                            : c.family === "TEXT"
                              ? `${c.mean_numeric.toFixed(1)} chars`
                              : num(c.mean_numeric)}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
        <p className="mt-2 text-xs text-zinc-500">
          Null means the field has nothing stored; blank means it holds an empty or whitespace-only string, which
          passes a NOT NULL constraint but carries nothing. Text columns are profiled by length only - no value from
          a text column is ever stored. Distinct counts are approximate (±2%).
        </p>
      </section>

      {past.length > 0 && (
        <section className="mt-8">
          <h2 className="mb-2 font-mono text-xs uppercase tracking-[0.15em] text-zinc-500">Earlier runs</h2>
          <ul className="divide-y divide-border border border-border bg-surface">
            {past.slice(0, 30).map((a) => (
              <li key={a.id} className="px-4 py-2.5 text-sm">
                <span className="font-mono text-xs text-zinc-500">{formatDateTime(a.created_at)}</span>{" "}
                <span className="font-mono text-xs text-zinc-400">{a.severity}</span>{" "}
                <span className="text-foreground">{a.message}</span>
              </li>
            ))}
          </ul>
        </section>
      )}
    </div>
  );
}
