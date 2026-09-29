import {
  GROUP_OPTIONS,
  STATUS_OPTIONS,
  kindLabel,
  type CheckFilters,
  type GroupBy,
  type StatusKey,
} from "../lib/check-filters";

/**
 * Search, the kind and status chips, and the grouping switch for one tab.
 *
 * Chips rather than dropdowns because the counts are the point: "Null rate 24,
 * Freshness 14" says what a tab holds before anything is clicked, and a chip
 * at 0 says there is nothing there without anyone having to try it.
 */
export function CheckFilterBar({
  query,
  onQueryChange,
  filters,
  onFiltersChange,
  kinds,
  kindCounts,
  statusCounts,
  showSearch,
  active,
  onClear,
}: {
  query: string;
  onQueryChange: (query: string) => void;
  filters: CheckFilters;
  onFiltersChange: (filters: CheckFilters) => void;
  /** The kinds present in this tab. With one or none, there is nothing to filter by. */
  kinds: string[];
  kindCounts: Map<string, number>;
  statusCounts: Map<StatusKey, number>;
  showSearch: boolean;
  active: boolean;
  onClear: () => void;
}) {
  const toggle = <T extends string>(values: T[], value: T): T[] =>
    values.includes(value) ? values.filter((v) => v !== value) : [...values, value];

  return (
    <div className="mb-3 space-y-2">
      {showSearch && (
        <input
          type="search"
          value={query}
          onChange={(e) => onQueryChange(e.target.value)}
          placeholder="Search name, rationale or table…"
          className="w-full rounded-md border border-border bg-surface px-3 py-2 text-sm text-foreground placeholder:text-zinc-500 focus:border-accent focus:outline-none"
        />
      )}

      <div className="flex flex-wrap items-start justify-between gap-x-6 gap-y-2">
        <div className="space-y-2">
          {kinds.length > 1 && (
            <ChipRow label="Kind">
              <Chip
                label="All"
                selected={filters.kinds.length === 0}
                onClick={() => onFiltersChange({ ...filters, kinds: [] })}
              />
              {kinds.map((kind) => (
                <Chip
                  key={kind}
                  label={kindLabel(kind)}
                  count={kindCounts.get(kind) ?? 0}
                  selected={filters.kinds.includes(kind)}
                  onClick={() => onFiltersChange({ ...filters, kinds: toggle(filters.kinds, kind) })}
                />
              ))}
            </ChipRow>
          )}

          <ChipRow label="Status">
            <Chip
              label="All"
              selected={filters.statuses.length === 0}
              onClick={() => onFiltersChange({ ...filters, statuses: [] })}
            />
            {STATUS_OPTIONS.map((option) => {
              const count = statusCounts.get(option.key) ?? 0;
              const selected = filters.statuses.includes(option.key);
              if (option.hideWhenEmpty && count === 0 && !selected) return null;
              return (
                <Chip
                  key={option.key}
                  label={option.label}
                  count={count}
                  selected={selected}
                  tone={option.key === "FAILED" ? "red" : option.key === "ERROR" ? "amber" : undefined}
                  onClick={() =>
                    onFiltersChange({ ...filters, statuses: toggle(filters.statuses, option.key) })
                  }
                />
              );
            })}
          </ChipRow>
        </div>

        <div className="flex items-center gap-3">
          {active && (
            <button type="button" onClick={onClear} className="text-xs text-accent hover:underline">
              Clear filters
            </button>
          )}
          <GroupSwitch value={filters.group} onChange={(group) => onFiltersChange({ ...filters, group })} />
        </div>
      </div>
    </div>
  );
}

function ChipRow({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex flex-wrap items-center gap-1.5" role="group" aria-label={label}>
      <span className="w-12 shrink-0 text-xs font-medium uppercase tracking-wide text-zinc-500">{label}</span>
      {children}
    </div>
  );
}

function Chip({
  label,
  count,
  selected,
  tone,
  onClick,
}: {
  label: string;
  count?: number;
  selected: boolean;
  /** A status colour for the count, so a failing total reads as one before it is read. */
  tone?: "red" | "amber";
  onClick: () => void;
}) {
  // An empty chip stays visible but dimmed - hiding it would make the row
  // reshuffle as other filters change - and stays clickable only to undo it.
  const empty = count === 0 && !selected;
  const countTone =
    count && tone === "red" ? "text-red-400" : count && tone === "amber" ? "text-amber-400" : "";
  return (
    <button
      type="button"
      aria-pressed={selected}
      disabled={empty}
      onClick={onClick}
      className={`inline-flex items-center gap-1.5 border px-2 py-0.5 text-xs transition-colors ${
        selected
          ? "border-accent-line bg-accent-soft text-accent"
          : "border-border text-zinc-400 hover:border-zinc-600 hover:text-foreground"
      } ${empty ? "cursor-default opacity-40 hover:border-border hover:text-zinc-400" : ""}`}
    >
      {label}
      {count !== undefined && (
        <span className={`font-mono text-[11px] ${selected ? "text-accent" : countTone || "text-zinc-500"}`}>
          {count}
        </span>
      )}
    </button>
  );
}

function GroupSwitch({ value, onChange }: { value: GroupBy; onChange: (group: GroupBy) => void }) {
  return (
    <div className="flex items-center gap-2" role="group" aria-label="Group by">
      <span className="text-xs font-medium uppercase tracking-wide text-zinc-500">Group</span>
      <div className="flex border border-border">
        {GROUP_OPTIONS.map((option) => (
          <button
            key={option.key}
            type="button"
            aria-pressed={value === option.key}
            onClick={() => onChange(option.key)}
            className={`px-2 py-0.5 text-xs transition-colors ${
              value === option.key
                ? "bg-accent-soft text-accent"
                : "text-zinc-400 hover:text-foreground"
            }`}
          >
            {option.label}
          </button>
        ))}
      </div>
    </div>
  );
}
