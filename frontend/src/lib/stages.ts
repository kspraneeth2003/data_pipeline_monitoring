import type { CheckStage } from "./api";

/**
 * The four tabs a project's checks are filed under, in pipeline order.
 *
 * Ordered by the hop each one watches so the tab strip reads as the pipeline
 * does, left to right, with data quality last because it is the only one that
 * is not a movement.
 */
export const STAGES: {
  key: CheckStage;
  label: string;
  blurb: string;
  /**
   * Whether checks can be authored in this tab.
   *
   * Only data quality can. The three movement stages are populated by
   * derivation from the pipeline's own definition - a parity check states what
   * a MERGE already says, so writing one by hand means transcribing the MERGE
   * and then maintaining the transcription. Those tabs are for reviewing and
   * tuning what derivation produced.
   */
  canAdd: boolean;
}[] = [
  {
    key: "STG_TO_BRONZE",
    label: "Stg → Bronze",
    blurb: "Everything that landed in staging arrived in bronze, unchanged.",
    canAdd: false,
  },
  {
    key: "BRONZE_TO_SILVER",
    label: "Bronze → Silver",
    blurb: "Bronze rows survived cleaning and deduplication into silver, with their values intact.",
    canAdd: false,
  },
  {
    key: "SILVER_TO_GOLD",
    label: "Silver → Gold",
    blurb: "Silver rows are represented in the gold views built on top of them.",
    canAdd: false,
  },
  {
    key: "DATA_QUALITY",
    label: "Data Quality",
    blurb:
      "What one table should be true of on its own: nulls, freshness, schema, SCD2 history. SCD2 checks appear here automatically when a dimension warrants one.",
    canAdd: true,
  },
];

export function stageLabel(stage: CheckStage | string): string {
  return STAGES.find((s) => s.key === stage)?.label ?? "Data Quality";
}

export function stageCanAdd(stage: CheckStage | string): boolean {
  return STAGES.find((s) => s.key === stage)?.canAdd ?? false;
}

/**
 * Whether a check matches a search box.
 *
 * Searches the rationale as well as the name, because the rationale is where
 * the words a person actually remembers live - "settling lag", "WAREHOUSE_ID",
 * "SCD2" - while names tend to be terse and uniform. Config is searched as raw
 * JSON so a table name finds the checks that watch it, which is the single
 * most common way anyone arrives here.
 */
export function matchesQuery(
  check: { name: string; description: string | null; rationale: string | null; type: string; config: unknown },
  query: string,
): boolean {
  const q = query.trim().toLowerCase();
  if (!q) return true;
  const haystack = [
    check.name,
    check.description ?? "",
    check.rationale ?? "",
    check.type,
    JSON.stringify(check.config ?? {}),
  ]
    .join(" ")
    .toLowerCase();
  return q.split(/\s+/).every((term) => haystack.includes(term));
}
