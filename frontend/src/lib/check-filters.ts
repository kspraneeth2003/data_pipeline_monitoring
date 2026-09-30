import type { Check, CheckStage } from "./api";
import { matchesQuery } from "./stages";

/**
 * Filtering and grouping for the project page's check list.
 *
 * Everything here is derived from what the page already loaded - type, the
 * last run, the object the config names - so narrowing a tab of fifty checks
 * costs no request and cannot disagree with the rows it filters.
 *
 * The state lives in the URL (`?kind=`, `?status=`, `?group=`), the same way
 * the tab does, so "the failing freshness checks in Customer 360" is a link
 * someone can send rather than a sequence of clicks they have to describe.
 */

/**
 * Short names for the chips. The check form's labels are written to explain a
 * type to someone configuring one ("Layer parity (source → target)"); a chip
 * row has to be scannable, so these are the words people actually say.
 */
const KIND_LABELS: Record<string, string> = {
  NULL_RATE: "Null rate",
  FRESHNESS: "Freshness",
  SCHEMA_DRIFT: "Schema drift",
  SCD2_INTEGRITY: "SCD2",
  ROW_COUNT: "Row count",
  BRONZE_TO_SILVER_PARITY: "Parity",
  CROSS_SOURCE_PARITY: "Cross-source",
};

export function kindLabel(type: string): string {
  return KIND_LABELS[type] ?? type;
}

export type StatusKey = "FAILED" | "ERROR" | "UNREACHABLE" | "PASSED" | "RUNNING" | "INVALID" | "NONE";

/**
 * Broken first, because that is the question the status row is for. RUNNING
 * is transient and is only offered while something is actually running.
 */
export const STATUS_OPTIONS: { key: StatusKey; label: string; hideWhenEmpty?: boolean }[] = [
  { key: "FAILED", label: "Failing" },
  { key: "ERROR", label: "Error" },
  { key: "UNREACHABLE", label: "Couldn't run", hideWhenEmpty: true },
  { key: "PASSED", label: "Passing" },
  { key: "RUNNING", label: "Running", hideWhenEmpty: true },
  { key: "INVALID", label: "Not monitored", hideWhenEmpty: true },
  { key: "NONE", label: "Never run" },
];

export function checkStatus(check: Check): StatusKey {
  const status = check.runs[0]?.status;
  return status === "FAILED" ||
    status === "ERROR" ||
    status === "PASSED" ||
    status === "RUNNING" ||
    status === "INVALID" ||
    status === "UNREACHABLE"
    ? status
    : "NONE";
}

export function isBroken(check: Check): boolean {
  const status = checkStatus(check);
  return status === "FAILED" || status === "ERROR";
}

/**
 * The one table a check is about.
 *
 * For parity that is the *target*: when silver disagrees with bronze, silver
 * is the table holding the wrong data, and "everything about SILVER.MEMBERS"
 * should include the check that says MEMBERS lost rows. A cross-source check
 * compares two free-form queries and names no table, so it has none.
 */
export function checkTable(check: Check): string | null {
  const config = check.config ?? {};
  for (const key of ["silverObject", "object"]) {
    const value = config[key];
    if (typeof value === "string" && value.trim()) return value.trim().toUpperCase();
  }
  return null;
}

export type GroupBy = "none" | "table" | "kind";

export const GROUP_OPTIONS: { key: GroupBy; label: string }[] = [
  { key: "table", label: "Table" },
  { key: "kind", label: "Kind" },
  { key: "none", label: "None" },
];

export type CheckFilters = {
  kinds: string[];
  statuses: StatusKey[];
  group: GroupBy;
};

/**
 * Data quality is read table by table - "how is this table doing" - so it
 * opens grouped. The movement tabs hold one parity check per hop, where a
 * group would mostly be a heading over a single row.
 */
export function defaultGroup(stage: CheckStage): GroupBy {
  return stage === "DATA_QUALITY" ? "table" : "none";
}

function listParam(params: URLSearchParams, name: string): string[] {
  return (params.get(name) ?? "")
    .split(",")
    .map((v) => v.trim())
    .filter(Boolean);
}

/** Reads filters from the URL, ignoring any value this page does not know. */
export function readFilters(params: URLSearchParams, stage: CheckStage): CheckFilters {
  const statusKeys = new Set<string>(STATUS_OPTIONS.map((s) => s.key));
  const group = params.get("group");
  return {
    kinds: listParam(params, "kind"),
    statuses: listParam(params, "status").filter((s): s is StatusKey => statusKeys.has(s)),
    group: GROUP_OPTIONS.some((g) => g.key === group) ? (group as GroupBy) : defaultGroup(stage),
  };
}

/**
 * Writes filters back, keeping every other parameter (the tab, above all).
 * A default is written as absence, so an untouched page keeps a clean URL.
 */
export function writeFilters(
  params: URLSearchParams,
  filters: CheckFilters,
  stage: CheckStage,
): URLSearchParams {
  const next = new URLSearchParams(params);
  const set = (name: string, values: string[]) =>
    values.length ? next.set(name, values.join(",")) : next.delete(name);
  set("kind", filters.kinds);
  set("status", filters.statuses);
  if (filters.group === defaultGroup(stage)) next.delete("group");
  else next.set("group", filters.group);
  return next;
}

export function hasActiveFilters(filters: CheckFilters, query: string): boolean {
  return filters.kinds.length > 0 || filters.statuses.length > 0 || query.trim() !== "";
}

/**
 * The active filters in words, for the empty state: "nothing matches" alone
 * leaves the reader to reconstruct which of three controls did it.
 */
export function describeFilters(filters: CheckFilters, query: string): string {
  const parts: string[] = [];
  if (query.trim()) parts.push(`“${query.trim()}”`);
  if (filters.kinds.length) parts.push(filters.kinds.map(kindLabel).join(" or "));
  if (filters.statuses.length)
    parts.push(
      filters.statuses
        .map((s) => STATUS_OPTIONS.find((o) => o.key === s)?.label.toLowerCase() ?? s)
        .join(" or "),
    );
  return parts.length ? parts.join(", ") : "these filters";
}

type Dimension = "kind" | "status";

function passes(check: Check, filters: CheckFilters, query: string, ignore?: Dimension): boolean {
  if (!matchesQuery(check, query)) return false;
  if (ignore !== "kind" && filters.kinds.length && !filters.kinds.includes(check.type)) return false;
  if (ignore !== "status" && filters.statuses.length && !filters.statuses.includes(checkStatus(check)))
    return false;
  return true;
}

export function applyFilters(checks: Check[], filters: CheckFilters, query: string): Check[] {
  return checks.filter((c) => passes(c, filters, query));
}

/**
 * The number on each chip: how many checks it would show given every *other*
 * filter. Counting a dimension against itself would make each chip read 0 the
 * moment a sibling is selected, which says "nothing here" when there is.
 */
export function facetCounts(
  checks: Check[],
  filters: CheckFilters,
  query: string,
): { kinds: Map<string, number>; statuses: Map<StatusKey, number> } {
  const kinds = new Map<string, number>();
  const statuses = new Map<StatusKey, number>();
  for (const check of checks) {
    if (passes(check, filters, query, "kind")) kinds.set(check.type, (kinds.get(check.type) ?? 0) + 1);
    if (passes(check, filters, query, "status")) {
      const status = checkStatus(check);
      statuses.set(status, (statuses.get(status) ?? 0) + 1);
    }
  }
  return { kinds, statuses };
}

/** The kinds present in a tab, most numerous first - the order chips read in. */
export function kindsIn(checks: Check[]): string[] {
  const counts = new Map<string, number>();
  for (const check of checks) counts.set(check.type, (counts.get(check.type) ?? 0) + 1);
  return [...counts.entries()]
    .sort((a, b) => b[1] - a[1] || kindLabel(a[0]).localeCompare(kindLabel(b[0])))
    .map(([kind]) => kind);
}

export type CheckGroup = {
  key: string;
  label: string;
  /** Rendered in monospace - it is an identifier, not prose. */
  mono: boolean;
  checks: Check[];
  broken: number;
  /** Checks with no run yet. A group of these is unknown, not healthy. */
  unrun: number;
};

/**
 * Buckets already-ordered checks, keeping their order within each bucket.
 * Groups holding something broken come first, then the rest by name, so the
 * top of a grouped view is as urgent as the top of a flat one.
 */
export function groupChecks(checks: Check[], by: Exclude<GroupBy, "none">): CheckGroup[] {
  const groups = new Map<string, CheckGroup>();
  for (const check of checks) {
    const table = by === "table" ? checkTable(check) : null;
    const key = by === "table" ? (table ?? "") : check.type;
    let group = groups.get(key);
    if (!group) {
      group = {
        key,
        label: by === "table" ? (table ?? "No single table") : kindLabel(check.type),
        mono: by === "table" && table !== null,
        checks: [],
        broken: 0,
        unrun: 0,
      };
      groups.set(key, group);
    }
    group.checks.push(check);
    if (isBroken(check)) group.broken += 1;
    if (checkStatus(check) === "NONE") group.unrun += 1;
  }
  return [...groups.values()].sort((a, b) => {
    if ((a.broken > 0) !== (b.broken > 0)) return a.broken > 0 ? -1 : 1;
    // The catch-all bucket is not a table and should not sort among them.
    if (!a.key !== !b.key) return a.key ? -1 : 1;
    return a.label.localeCompare(b.label);
  });
}
