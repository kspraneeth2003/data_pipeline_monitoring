import { useEffect, useRef, useState } from "react";
import {
  GROUP_OPTIONS,
  STATUS_OPTIONS,
  kindLabel,
  type CheckFilters,
  type GroupBy,
  type StatusKey,
} from "../lib/check-filters";

/**
 * Search, a Filter button, and the grouping switch - one row.
 *
 * The categories live behind the button rather than as rows of chips: two
 * rows of a dozen chips above every list was more chrome than list. What is
 * applied still shows without opening it - the button carries the count, and
 * the line above the table says the filters in words.
 */
export function CheckFilterBar({
  query,
  onQueryChange,
  filters,
  onFiltersChange,
  kinds,
  kindCounts,
  statusCounts,
  active,
  onClear,
}: {
  query: string;
  onQueryChange: (query: string) => void;
  filters: CheckFilters;
  onFiltersChange: (filters: CheckFilters) => void;
  /** The kinds present in this tab. With one or none, the Kind section is left out. */
  kinds: string[];
  kindCounts: Map<string, number>;
  statusCounts: Map<StatusKey, number>;
  active: boolean;
  onClear: () => void;
}) {
  return (
    <div className="mb-3 flex flex-wrap items-center gap-3">
      <input
        type="search"
        value={query}
        onChange={(e) => onQueryChange(e.target.value)}
        placeholder="Search name, rationale or table…"
        className="min-w-0 flex-1 basis-64 rounded-md border border-border bg-surface px-3 py-2 text-sm text-foreground placeholder:text-zinc-500 focus:border-accent focus:outline-none"
      />
      <FilterMenu
        filters={filters}
        onFiltersChange={onFiltersChange}
        kinds={kinds}
        kindCounts={kindCounts}
        statusCounts={statusCounts}
        active={active}
        onClear={onClear}
      />
      <GroupSwitch value={filters.group} onChange={(group) => onFiltersChange({ ...filters, group })} />
    </div>
  );
}

function FilterMenu({
  filters,
  onFiltersChange,
  kinds,
  kindCounts,
  statusCounts,
  active,
  onClear,
}: {
  filters: CheckFilters;
  onFiltersChange: (filters: CheckFilters) => void;
  kinds: string[];
  kindCounts: Map<string, number>;
  statusCounts: Map<StatusKey, number>;
  active: boolean;
  onClear: () => void;
}) {
  const [open, setOpen] = useState(false);
  const root = useRef<HTMLDivElement>(null);

  // Closes on a click anywhere else or on Escape - the two ways anyone
  // expects to dismiss a menu without hunting for a close button.
  useEffect(() => {
    if (!open) return;
    const onPointer = (e: PointerEvent) => {
      if (root.current && !root.current.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    document.addEventListener("pointerdown", onPointer);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("pointerdown", onPointer);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  const toggle = <T extends string>(values: T[], value: T): T[] =>
    values.includes(value) ? values.filter((v) => v !== value) : [...values, value];
  const selectedCount = filters.kinds.length + filters.statuses.length;

  return (
    <div ref={root} className="relative">
      <button
        type="button"
        aria-haspopup="true"
        aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
        className={`flex items-center gap-2 border px-3 py-2 text-sm transition-colors ${
          selectedCount > 0 || open
            ? "border-accent-line bg-accent-soft text-accent"
            : "border-border bg-surface text-zinc-300 hover:border-zinc-600 hover:text-foreground"
        }`}
      >
        <FunnelIcon />
        Filter
        {selectedCount > 0 && (
          <span className="bg-accent px-1.5 font-mono text-[11px] text-accent-foreground">{selectedCount}</span>
        )}
      </button>

      {open && (
        <div
          role="dialog"
          aria-label="Filter checks"
          className="absolute right-0 z-20 mt-1 w-72 border border-border bg-surface-raised shadow-xl"
        >
          {kinds.length > 1 && (
            <Section title="Kind">
              {kinds.map((kind) => (
                <Option
                  key={kind}
                  label={kindLabel(kind)}
                  count={kindCounts.get(kind) ?? 0}
                  checked={filters.kinds.includes(kind)}
                  onChange={() => onFiltersChange({ ...filters, kinds: toggle(filters.kinds, kind) })}
                />
              ))}
            </Section>
          )}
          <Section title="Status">
            {STATUS_OPTIONS.map((option) => {
              const count = statusCounts.get(option.key) ?? 0;
              const checked = filters.statuses.includes(option.key);
              if (option.hideWhenEmpty && count === 0 && !checked) return null;
              return (
                <Option
                  key={option.key}
                  label={option.label}
                  count={count}
                  checked={checked}
                  tone={option.key === "FAILED" ? "red" : option.key === "ERROR" ? "amber" : undefined}
                  onChange={() =>
                    onFiltersChange({ ...filters, statuses: toggle(filters.statuses, option.key) })
                  }
                />
              );
            })}
          </Section>
          <div className="flex items-center justify-between border-t border-border px-3 py-2 text-xs">
            <button
              type="button"
              onClick={onClear}
              disabled={!active}
              className="text-accent hover:underline disabled:cursor-default disabled:text-zinc-600 disabled:no-underline"
            >
              Clear all
            </button>
            <button type="button" onClick={() => setOpen(false)} className="text-zinc-400 hover:text-foreground">
              Done
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <fieldset className="border-b border-border px-3 py-2 last-of-type:border-b-0">
      <legend className="sr-only">{title}</legend>
      <p aria-hidden className="mb-1 text-[11px] font-medium uppercase tracking-wide text-zinc-500">
        {title}
      </p>
      {children}
    </fieldset>
  );
}

function Option({
  label,
  count,
  checked,
  tone,
  onChange,
}: {
  label: string;
  count: number;
  checked: boolean;
  /** A status colour for the count, so a failing total reads as one before it is read. */
  tone?: "red" | "amber";
  onChange: () => void;
}) {
  // An option that would show nothing stays listed but dimmed - removing it
  // would make the list reshuffle as other filters change - and can only be
  // unticked, never ticked into an empty result.
  const empty = count === 0 && !checked;
  const countTone = count && tone === "red" ? "text-red-400" : count && tone === "amber" ? "text-amber-400" : "text-zinc-500";
  return (
    <label
      className={`flex items-center gap-2 py-1 text-sm ${
        empty ? "cursor-default opacity-40" : "cursor-pointer text-foreground hover:text-accent"
      }`}
    >
      <input
        type="checkbox"
        checked={checked}
        disabled={empty}
        onChange={onChange}
        className="h-3.5 w-3.5 accent-[var(--accent)]"
      />
      <span className="flex-1">{label}</span>
      <span className={`font-mono text-xs ${countTone}`}>{count}</span>
    </label>
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
            className={`px-2.5 py-2 text-xs transition-colors ${
              value === option.key ? "bg-accent-soft text-accent" : "text-zinc-400 hover:text-foreground"
            }`}
          >
            {option.label}
          </button>
        ))}
      </div>
    </div>
  );
}

function FunnelIcon() {
  return (
    <svg aria-hidden viewBox="0 0 16 16" className="h-3.5 w-3.5" fill="none" stroke="currentColor" strokeWidth="1.5">
      <path d="M2 3h12l-4.5 5.5V13l-3-1.5v-3L2 3z" strokeLinejoin="round" />
    </svg>
  );
}
