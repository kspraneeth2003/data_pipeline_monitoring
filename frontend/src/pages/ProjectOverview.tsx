import { useCallback, useEffect, useMemo, useState } from "react";
import { Link, useParams, useSearchParams } from "react-router-dom";
import { api, type Check, type CheckStage, type Project } from "../lib/api";
import { Breadcrumbs } from "../components/Breadcrumbs";
import { HealthPill } from "../components/HealthPill";
import { StatusBadge } from "../components/StatusBadge";
import { RunNowButton } from "../components/RunNowButton";
import { PinButton } from "../components/PinButton";
import { CheckFilterBar } from "../components/CheckFilterBar";
import { STAGES } from "../lib/stages";
import {
  applyFilters,
  describeFilters,
  facetCounts,
  groupChecks,
  hasActiveFilters,
  kindsIn,
  readFilters,
  writeFilters,
  type CheckFilters,
  type CheckGroup,
} from "../lib/check-filters";
import { formatDateTime } from "../lib/time";

/**
 * A project, as its checks rather than as its databases.
 *
 * The page used to open on a grid of databases, which meant a check was filed
 * under the database its row happens to live in. For a parity check that spans
 * two databases it was half a lie, and for everyone else it was the wrong
 * question: a failure is reasoned about as "the silver load broke", not as
 * "something in DPM_SRC_CRM broke". So the top level is now the four stages.
 *
 * The databases are still here, below the checks - they are how a check is
 * added and where connector settings live, just not the way in.
 */
export function ProjectOverview() {
  const { slug = "" } = useParams();
  const [searchParams, setSearchParams] = useSearchParams();
  const [project, setProject] = useState<Project | null>(null);
  const [checks, setChecks] = useState<Check[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [checksError, setChecksError] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  // Groups a person opened or closed by hand, keyed by grouping and group.
  // Anything not in here follows the default in `isExpanded` below.
  const [expanded, setExpanded] = useState<Record<string, boolean>>({});

  // The active tab lives in the URL so a link to "the bronze -> silver checks
  // of Customer 360" is a link, not a sequence of clicks.
  const requestedStage = searchParams.get("stage") as CheckStage | null;

  // Fetched independently rather than through one Promise.all. A rejected
  // checks request used to take the whole page down to "Not found", which says
  // the project does not exist - the one thing that was not wrong. The failure
  // that produced it was an API the frontend had outrun, and blaming the
  // project for it sent the reader looking in exactly the wrong place.
  const reload = useCallback(() => {
    api.getProject(slug).then(setProject).catch((e) => setError(e.message));
    api
      .listProjectChecks(slug)
      .then((c) => {
        setChecks(c);
        setChecksError(null);
      })
      .catch((e) => {
        setChecks([]);
        setChecksError(e.message);
      });
  }, [slug]);

  useEffect(reload, [reload]);

  const byStage = useMemo(() => {
    const groups = new Map<CheckStage, Check[]>(STAGES.map((s) => [s.key, []]));
    for (const check of checks ?? []) groups.get(check.stage)?.push(check);
    return groups;
  }, [checks]);

  if (error) {
    return (
      <main className="mx-auto max-w-5xl px-6 py-10 sm:px-10">
        <Breadcrumbs items={[{ label: "Projects", to: "/" }, { label: "Not found" }]} />
        <div className="rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700 dark:border-red-900 dark:bg-red-950 dark:text-red-400">
          {error}
        </div>
      </main>
    );
  }

  if (!project || !checks) {
    return (
      <main className="mx-auto max-w-5xl px-6 py-10 sm:px-10">
        <p className="text-sm text-zinc-500">Loading…</p>
      </main>
    );
  }

  // Only the add-check picker needs these now; the grid of database cards has
  // moved to its own page. Alphabetical, because a picker is read by name.
  const databases = [...project.databases].sort((a, b) => a.name.localeCompare(b.name));

  // With no tab named in the URL, open the first one in pipeline order that
  // actually holds something. Landing on an empty "Stg -> Bronze" says this
  // project is unmonitored when the checks are one tab to the right, and that
  // is a worse first impression than any amount of tidiness is worth.
  const firstPopulated = STAGES.find((s) => (byStage.get(s.key) ?? []).length > 0);
  const stage =
    (requestedStage && STAGES.find((s) => s.key === requestedStage)) ?? firstPopulated ?? STAGES[3];
  const stageChecks = byStage.get(stage.key) ?? [];
  const filters = readFilters(searchParams, stage.key);
  const filtering = hasActiveFilters(filters, query);
  const visible = applyFilters(stageChecks, filters, query);
  const counts = facetCounts(stageChecks, filters, query);

  const setFilters = (next: CheckFilters) =>
    setSearchParams(
      (params) => {
        // The tab has to be written out explicitly: with none in the URL the
        // page opens on the first populated one, which a filter must not move.
        const withStage = new URLSearchParams(params);
        withStage.set("stage", stage.key);
        return writeFilters(withStage, next, stage.key);
      },
      { replace: true },
    );
  const clearFilters = () => {
    setQuery("");
    setFilters({ ...filters, kinds: [], statuses: [] });
  };

  // Pinned first, then whatever is broken, then alphabetical. A pin is a
  // standing instruction to keep something in view, so it outranks even a
  // failure - someone pinned it because this is the one they are watching.
  const ordered = [...visible].sort((a, b) => {
    if (a.pinned !== b.pinned) return a.pinned ? -1 : 1;
    const rank = (c: Check) => {
      const status = c.runs[0]?.status;
      return status === "ERROR" ? 0 : status === "FAILED" ? 1 : status ? 3 : 2;
    };
    return rank(a) - rank(b) || a.name.localeCompare(b.name);
  });

  const groups = filters.group === "none" ? null : groupChecks(ordered, filters.group);
  // Open by default where there is something to see: a group with a failure
  // in it, every group once the list has been filtered down (the filter
  // already said what to look at), or all of them when there are few enough
  // that collapsing saves nothing. A hand toggle overrides all of that.
  const isExpanded = (group: CheckGroup) =>
    expanded[`${filters.group}:${group.key}`] ??
    (group.broken > 0 || filtering || (groups?.length ?? 0) <= 4);
  const toggleGroup = (group: CheckGroup) =>
    setExpanded((prev) => ({ ...prev, [`${filters.group}:${group.key}`]: !isExpanded(group) }));
  const setAllGroups = (open: boolean) =>
    setExpanded((prev) => ({
      ...prev,
      ...Object.fromEntries((groups ?? []).map((g) => [`${filters.group}:${g.key}`, open])),
    }));
  // Grouped by table, every row in a group shares a database, so the column
  // would repeat the heading's own prefix down the page.
  const showDatabase = filters.group !== "table";
  const columnCount = showDatabase ? 7 : 6;

  return (
    <main className="mx-auto max-w-5xl px-6 py-10 sm:px-10">
      <Breadcrumbs items={[{ label: "Projects", to: "/" }, { label: project.name }]} />

      <header className="mb-8">
        <div className="flex flex-wrap items-center gap-3">
          <h1 className="text-2xl font-semibold tracking-[0.08em] text-foreground">{project.name}</h1>
          <HealthPill health={project.health} />
        </div>
        {project.description && <p className="mt-1 max-w-2xl text-sm text-zinc-500">{project.description}</p>}

        {/* The two agents' output. Incidents is where the monitor reports -
            and the record of what went wrong, whether or not it reached
            Jira. Proposed changes is where the maintenance agent reports. */}
        <nav className="mt-3 flex flex-wrap gap-4 text-sm">
          <Link to={`/projects/${slug}/incidents`} className="text-accent hover:underline">
            Incidents
          </Link>
          <Link to={`/projects/${slug}/changes`} className="text-accent hover:underline">
            Proposed changes
          </Link>
        </nav>
      </header>

      <div className="mb-8 grid gap-4 sm:grid-cols-4">
        {[
          { label: "Databases", value: project.databases.length, to: `/projects/${slug}/databases` },
          { label: "Checks", value: project.health.total_checks },
          {
            label: "Needs attention",
            value: project.health.failing + project.health.erroring,
            tone:
              project.health.failing + project.health.erroring > 0 ? "text-red-400" : "text-foreground",
          },
          { label: "Open incidents", value: project.health.open_incidents, to: `/projects/${slug}/incidents` },
        ].map((stat) => {
          const body = (
            <>
              <dt className="text-xs font-medium uppercase tracking-wide text-zinc-500">{stat.label}</dt>
              <dd className={`mt-1 font-mono text-2xl font-semibold ${stat.tone ?? "text-foreground"}`}>{stat.value}</dd>
            </>
          );
          return stat.to ? (
            <Link
              key={stat.label}
              to={stat.to}
              className="rounded-xl border border-border bg-surface p-4 transition-colors hover:border-accent"
            >
              {body}
            </Link>
          ) : (
            <div key={stat.label} className="rounded-xl border border-border bg-surface p-4">
              {body}
            </div>
          );
        })}
      </div>

      <section>
        {checksError && (
          <div className="mb-4 rounded-lg border border-amber-500/40 bg-amber-500/10 px-4 py-3 text-sm text-amber-400">
            <p className="font-medium">The checks could not be loaded, so the tabs below are empty.</p>
            <p className="mt-1 text-amber-400/80">
              {checksError}. The project itself is fine — if this says &ldquo;Not Found&rdquo;, the
              backend is older than this page and needs restarting.
            </p>
          </div>
        )}

        <div className="mb-4 flex flex-wrap gap-1 border-b border-border">
          {STAGES.map((s) => {
            const group = byStage.get(s.key) ?? [];
            const broken = group.filter((c) => ["FAILED", "ERROR"].includes(c.runs[0]?.status ?? "")).length;
            const active = s.key === stage.key;
            return (
              <button
                key={s.key}
                type="button"
                onClick={() => setSearchParams({ stage: s.key }, { replace: true })}
                className={`-mb-px flex items-center gap-2 border-b-2 px-3 py-2 text-sm transition-colors ${
                  active
                    ? "border-accent font-medium text-foreground"
                    : "border-transparent text-zinc-500 hover:text-foreground"
                }`}
              >
                {s.label}
                <span className="font-mono text-xs text-zinc-500">{group.length}</span>
                {broken > 0 && (
                  <span className="h-1.5 w-1.5 rounded-full bg-red-500" aria-label={`${broken} failing`} />
                )}
              </button>
            );
          })}
        </div>

        <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
          <p className="max-w-2xl text-sm text-zinc-500">{stage.blurb}</p>
          {stage.canAdd ? (
            <AddCheckMenu slug={slug} databases={databases} />
          ) : (
            // Said out loud rather than shown as a missing button. A tab with
            // no add control and no explanation reads as unfinished, when it is
            // actually the design: these checks state what the pipeline's own
            // definition already says, so hand-writing one means transcribing a
            // MERGE and then maintaining the transcription.
            <p className="text-xs text-zinc-500">
              Derived from the pipeline — review and tune these, they are not authored here.
            </p>
          )}
        </div>

        {/* Hidden on a near-empty tab, where filtering three rows is noise -
            unless a filter is already set, e.g. arriving from a shared link,
            which has to stay visible to be undone. */}
        {(stageChecks.length > 3 || filtering) && (
          <CheckFilterBar
            query={query}
            onQueryChange={setQuery}
            filters={filters}
            onFiltersChange={setFilters}
            kinds={kindsIn(stageChecks)}
            kindCounts={counts.kinds}
            statusCounts={counts.statuses}
            active={filtering}
            onClear={clearFilters}
          />
        )}

        {stageChecks.length === 0 ? (
          <div className="rounded-xl border border-dashed border-border bg-surface px-6 py-12 text-center">
            <h3 className="text-base font-medium text-foreground">No {stage.label} checks</h3>
            <p className="mx-auto mt-1 max-w-md text-sm text-zinc-500">
              {stage.canAdd
                ? "Add a check to assert what one table should be true of on its own."
                : "Nothing in this project compares those two layers yet. These checks are derived when a repository is ingested."}
            </p>
          </div>
        ) : ordered.length === 0 ? (
          <div className="rounded-xl border border-dashed border-border px-6 py-10 text-center text-sm text-zinc-500">
            <p>No check in {stage.label} matches {describeFilters(filters, query)}.</p>
            <button type="button" onClick={clearFilters} className="mt-2 text-accent hover:underline">
              Clear filters
            </button>
          </div>
        ) : (
          <>
            <div className="mb-2 flex items-center justify-between gap-3 text-xs text-zinc-500">
              <span>
                {filtering
                  ? `Showing ${ordered.length} of ${stageChecks.length} checks`
                  : `${stageChecks.length} checks`}
                {groups &&
                  ` in ${groups.length} ${filters.group === "table" ? "table" : "kind"}${groups.length === 1 ? "" : "s"}`}
                {filtering && (
                  <span className="text-zinc-400"> · {describeFilters(filters, query)}</span>
                )}
              </span>
              {groups && groups.length > 1 && (
                <span className="flex gap-3">
                  <button type="button" onClick={() => setAllGroups(true)} className="hover:text-accent">
                    Expand all
                  </button>
                  <button type="button" onClick={() => setAllGroups(false)} className="hover:text-accent">
                    Collapse all
                  </button>
                </span>
              )}
            </div>
            <div className="overflow-x-auto border border-border bg-surface shadow-sm">
              <table className="min-w-full divide-y divide-border">
                <thead className="bg-zinc-50 dark:bg-white/[0.03]">
                  <tr>
                    <Th />
                    <Th>Check</Th>
                    {showDatabase && <Th>Database</Th>}
                    <Th>Type</Th>
                    <Th>Last run</Th>
                    <Th>Status</Th>
                    <Th />
                  </tr>
                </thead>
                {groups ? (
                  groups.map((group) => {
                    const open = isExpanded(group);
                    return (
                      <tbody key={group.key} className="divide-y divide-border border-b border-border">
                        <tr className="bg-surface-raised">
                          <td colSpan={columnCount} className="p-0">
                            <button
                              type="button"
                              aria-expanded={open}
                              onClick={() => toggleGroup(group)}
                              className="flex w-full items-center gap-3 px-3 py-2 text-left transition-colors hover:bg-white/[0.02]"
                            >
                              <span className="w-3 font-mono text-xs text-zinc-500">{open ? "▾" : "▸"}</span>
                              <span
                                className={`min-w-0 flex-1 truncate text-sm text-foreground ${
                                  group.mono ? "font-mono text-xs" : "font-medium"
                                }`}
                              >
                                {group.label}
                              </span>
                              <span className="font-mono text-xs text-zinc-500">
                                {group.checks.length} {group.checks.length === 1 ? "check" : "checks"}
                              </span>
                              <span className="w-24 text-right font-mono text-xs">
                                {group.broken > 0 ? (
                                  <span className="text-red-400">{group.broken} failing</span>
                                ) : group.unrun === group.checks.length ? (
                                  <span className="text-zinc-500">not run</span>
                                ) : (
                                  <span className="text-emerald-400">ok</span>
                                )}
                              </span>
                            </button>
                          </td>
                        </tr>
                        {open &&
                          group.checks.map((check) => (
                            <CheckRow
                              key={check.id}
                              check={check}
                              slug={slug}
                              showDatabase={showDatabase}
                              onChanged={reload}
                            />
                          ))}
                      </tbody>
                    );
                  })
                ) : (
                  <tbody className="divide-y divide-border">
                    {ordered.map((check) => (
                      <CheckRow
                        key={check.id}
                        check={check}
                        slug={slug}
                        showDatabase={showDatabase}
                        onChanged={reload}
                      />
                    ))}
                  </tbody>
                )}
              </table>
            </div>
          </>
        )}
      </section>

    </main>
  );
}

/**
 * "Add check", which needs a database first.
 *
 * A check is stored under a database, so the form cannot open without one.
 * With a single database there is nothing to ask and the button just goes;
 * with several, the choice is made here rather than as a field inside the
 * form, so nobody fills in a config and then discovers they picked wrong.
 */
function AddCheckMenu({
  slug,
  databases,
}: {
  slug: string;
  databases: { id: string; slug: string; name: string }[];
}) {
  const [open, setOpen] = useState(false);

  if (databases.length === 0) {
    return (
      <Link to={`/projects/${slug}/databases/new`} className="text-sm text-accent hover:underline">
        + Add a database first
      </Link>
    );
  }

  if (databases.length === 1) {
    return (
      <Link
        to={`/projects/${slug}/databases/${databases[0].slug}/checks/new`}
        className="rounded-md bg-accent px-3 py-1.5 text-sm font-medium text-accent-foreground shadow-sm transition-colors hover:bg-accent-hover"
      >
        + Add check
      </Link>
    );
  }

  return (
    <div className="relative">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="rounded-md bg-accent px-3 py-1.5 text-sm font-medium text-accent-foreground shadow-sm transition-colors hover:bg-accent-hover"
      >
        + Add check
      </button>
      {open && (
        <div className="absolute right-0 z-10 mt-1 w-56 rounded-md border border-border bg-surface py-1 shadow-lg">
          <p className="px-3 py-1.5 text-xs text-zinc-500">On which database?</p>
          {databases.map((database) => (
            <Link
              key={database.id}
              to={`/projects/${slug}/databases/${database.slug}/checks/new`}
              className="block px-3 py-1.5 font-mono text-xs text-foreground hover:bg-accent/10 hover:text-accent"
            >
              {database.name}
            </Link>
          ))}
        </div>
      )}
    </div>
  );
}

/** One check, the same whether it sits in a flat list or under a group. */
function CheckRow({
  check,
  slug,
  showDatabase,
  onChanged,
}: {
  check: Check;
  slug: string;
  showDatabase: boolean;
  onChanged: () => void;
}) {
  const lastRun = check.runs[0];
  return (
    <tr className="transition-colors dark:hover:bg-white/[0.02]">
      <td className="py-3 pl-3 pr-0 align-top">
        <PinButton checkId={check.id} pinned={check.pinned} checkName={check.name} onDone={onChanged} />
      </td>
      <td className="px-4 py-3">
        <Link
          to={`/projects/${slug}/databases/${check.database.slug}/checks/${check.id}`}
          className="font-medium text-foreground hover:text-accent"
        >
          {check.name}
        </Link>
        {lastRun?.message && <div className="mt-0.5 line-clamp-1 text-xs text-zinc-500">{lastRun.message}</div>}
      </td>
      {showDatabase && (
        <td className="whitespace-nowrap px-4 py-3">
          <Link
            to={`/projects/${slug}/databases/${check.database.slug}`}
            className="font-mono text-xs text-zinc-500 hover:text-accent"
          >
            {check.database.name}
          </Link>
        </td>
      )}
      <td className="px-4 py-3">
        <span className="rounded-md bg-zinc-100 px-1.5 py-0.5 text-xs font-medium text-zinc-600 dark:bg-zinc-800 dark:text-zinc-300">
          {check.type}
        </span>
      </td>
      <td className="px-4 py-3 text-sm text-zinc-500 dark:text-zinc-400">{formatDateTime(lastRun?.started_at)}</td>
      <td className="px-4 py-3">
        <StatusBadge status={lastRun?.status ?? "NONE"} />
      </td>
      <td className="whitespace-nowrap px-4 py-3 text-right">
        <RunNowButton checkId={check.id} onDone={onChanged} />
      </td>
    </tr>
  );
}

function Th({ children }: { children?: React.ReactNode }) {
  return (
    <th className="px-4 py-2 text-left text-xs font-semibold uppercase tracking-wide text-zinc-500 dark:text-zinc-400">
      {children}
    </th>
  );
}
