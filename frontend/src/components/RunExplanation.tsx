import { useState } from "react";
import type { CheckRun, Drilldown } from "../lib/api";

// Reader-facing names for the metrics a check can report without asserting.
// Keys match `ParityMetric` / `Scd2Metric` in backend/app/checks/config_schemas.py.
const METRIC_LABELS: Record<string, string> = {
  missingInSilver: "Missing in target",
  extraInSilver: "Extra in target",
  duplicateKeys: "Duplicate keys",
  valueMismatches: "Value mismatches",
  keysWithManyCurrent: "Keys with several current rows",
  keysWithNoCurrent: "Keys with no current row",
  overlappingVersions: "Overlapping versions",
  gappedVersions: "Gaps between versions",
  invalidWindows: "Malformed windows",
};

// Sample lists the engine records, in the order worth reading them.
const SAMPLE_LABELS: Record<string, string> = {
  duplicateKeys: "Duplicated",
  missingInSilver: "Missing",
  extraInSilver: "Extra",
  valueMismatch: "Value differs",
  currentFlagKeys: "Current-row problem",
  overlappingKeys: "Overlapping",
  gappedKeys: "Gapped",
};

const SAMPLES_SHOWN = 3;

type Reported = Record<string, { value: number; threshold: number }>;

function sampleText(value: unknown): string {
  return typeof value === "string" || typeof value === "number" ? String(value) : JSON.stringify(value);
}

/**
 * The short "what is off" box on a run.
 *
 * The status badge beside it is the verdict; this only says what the run
 * found. The source line matters: an AI summary is labelled as one, and the
 * samples and reported metrics underneath come straight from the run, so a
 * reader can check the sentence against the figures it was written from.
 */
export function RunExplanation({ run, drilldowns = [] }: { run: CheckRun; drilldowns?: Drilldown[] }) {
  const [open, setOpen] = useState(false);
  const explanation = run.explanation;
  if (!explanation) return null;

  const metrics = run.metrics ?? {};
  // Only findings this run actually has: a query for "0 missing" is noise.
  const offered = drilldowns.filter((d) => Number(metrics[d.metric] ?? 0) > 0);
  const reported = (metrics.reported ?? {}) as Reported;
  const samples = (metrics.samples ?? {}) as Record<string, unknown>;
  const sampleRows = Object.entries(SAMPLE_LABELS)
    .map(([key, label]) => ({ label, values: Array.isArray(samples[key]) ? (samples[key] as unknown[]) : [] }))
    .filter((row) => row.values.length > 0);

  const source =
    explanation.source === "ai"
      ? `AI summary${explanation.model ? ` · ${explanation.model}` : ""}`
      : "From the metrics";

  return (
    <div className="mt-2 border-l-2 border-accent-line bg-accent-soft px-3 py-2">
      <div className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-0.5">
        <span className="font-mono text-[11px] uppercase tracking-[0.1em] text-accent">What this run found</span>
        <span className="flex items-baseline gap-3 text-[11px] text-zinc-500">
          <span title={explanation.rejected ? `A model draft was discarded: ${explanation.rejected}` : undefined}>
            {source}
            {explanation.reusedFromRunId && " · unchanged since an earlier run"}
          </span>
          {offered.length > 0 && (
            <button
              type="button"
              onClick={() => setOpen(!open)}
              aria-expanded={open}
              className="text-zinc-400 underline-offset-2 transition-colors hover:text-accent hover:underline"
            >
              {open ? "Hide queries" : `Queries (${offered.length})`}
            </button>
          )}
        </span>
      </div>
      <p className="mt-1 text-sm text-foreground">{explanation.text}</p>

      {Object.keys(reported).length > 0 && (
        <p className="mt-1.5 text-xs text-zinc-500">
          <span className="font-semibold text-zinc-600 dark:text-zinc-300">Reported, not asserted: </span>
          {Object.entries(reported)
            .map(([metric, r]) => `${METRIC_LABELS[metric] ?? metric} ${r.value.toLocaleString()} (limit ${r.threshold.toLocaleString()})`)
            .join(" · ")}
        </p>
      )}

      {open && <DrilldownList drilldowns={offered} metrics={metrics} />}

      {sampleRows.length > 0 && (
        <dl className="mt-1.5 space-y-0.5 text-xs text-zinc-500">
          {sampleRows.map((row) => (
            <div key={row.label}>
              <dt className="inline font-semibold text-zinc-600 dark:text-zinc-300">{row.label}: </dt>
              <dd className="inline font-mono">
                {row.values.slice(0, SAMPLES_SHOWN).map(sampleText).join(", ")}
                {row.values.length > SAMPLES_SHOWN && ` +${row.values.length - SAMPLES_SHOWN} more`}
              </dd>
            </div>
          ))}
        </dl>
      )}
    </div>
  );
}

/**
 * The short "show me the rows" query for each finding, behind the Queries
 * button. Each lists exactly what its count on the run counts, and is meant to
 * be pasted into Snowflake as-is; the check's own statement stays on the SQL tab.
 */
function DrilldownList({ drilldowns, metrics }: { drilldowns: Drilldown[]; metrics: Record<string, unknown> }) {
  const [copied, setCopied] = useState<string | null>(null);

  const copy = async (drilldown: Drilldown) => {
    try {
      await navigator.clipboard.writeText(drilldown.sql);
      setCopied(drilldown.metric);
      window.setTimeout(() => setCopied((current) => (current === drilldown.metric ? null : current)), 1500);
    } catch {
      setCopied(null);
    }
  };

  return (
    <div className="mt-2 space-y-2">
      {drilldowns.map((drilldown) => (
        <div key={drilldown.metric} className="border border-border bg-background">
          <div className="flex items-center justify-between gap-2 border-b border-border px-2 py-1 text-xs">
            <span className="text-zinc-600 dark:text-zinc-300">
              {drilldown.label}
              <span className="ml-1.5 font-mono text-zinc-500">{Number(metrics[drilldown.metric]).toLocaleString()}</span>
            </span>
            <button
              type="button"
              onClick={() => copy(drilldown)}
              className="text-[11px] text-zinc-500 transition-colors hover:text-accent"
            >
              {copied === drilldown.metric ? "Copied" : "Copy"}
            </button>
          </div>
          <pre className="max-h-56 overflow-auto px-2 py-1.5 font-mono text-[11px] leading-relaxed text-zinc-600 dark:text-zinc-400">
            {drilldown.sql}
          </pre>
        </div>
      ))}
    </div>
  );
}
