import { Link } from "react-router-dom";

import type { ProfileAnomaly } from "../lib/api";
import { relativeTime } from "../lib/time";
import { KindLabel, SeverityBadge } from "./SeverityBadge";

/**
 * Anomalies as a list a person works through: severity first, what and where,
 * the sentence that explains it, and one action.
 *
 * Acknowledging is offered on every item but means different things by kind,
 * and the button says so. A finding that is acknowledged stays quiet on later
 * runs ("this column is meant to be empty"); an anomaly is about one run
 * differing from the past, so acknowledging it only hides this occurrence.
 */
export function AnomalyList({
  anomalies,
  slug,
  onAcknowledge,
  showTable = true,
  empty,
}: {
  anomalies: ProfileAnomaly[];
  slug: string;
  onAcknowledge: (id: string) => void;
  showTable?: boolean;
  empty: string;
}) {
  if (anomalies.length === 0) {
    return <p className="border border-border bg-surface px-4 py-6 text-sm text-zinc-500">{empty}</p>;
  }
  return (
    <ul className="divide-y divide-border border border-border bg-surface">
      {anomalies.map((a) => (
        <li key={a.id} className="flex flex-col gap-2 px-4 py-3 sm:flex-row sm:items-start sm:justify-between">
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-2">
              <SeverityBadge severity={a.severity} />
              <KindLabel kind={a.kind} />
              <span className="font-mono text-xs text-foreground">
                {showTable ? (
                  <Link to={`/projects/${slug}/profiling/${a.target_id}`} className="hover:text-accent">
                    {a.object}
                  </Link>
                ) : null}
                {a.column_name ? (
                  <span className={showTable ? "text-zinc-400" : ""}>
                    {showTable ? "." : ""}
                    {a.column_name}
                  </span>
                ) : (
                  !showTable && <span className="text-zinc-400">table</span>
                )}
              </span>
            </div>
            <p className="mt-1.5 text-sm text-foreground">{a.message}</p>
            <p className="mt-1 text-xs text-zinc-500">
              {relativeTime(a.created_at)}
              {a.acknowledged_at ? " · acknowledged" : ""}
            </p>
          </div>
          {!a.acknowledged_at && (
            <button
              type="button"
              onClick={() => onAcknowledge(a.id)}
              title={
                a.kind === "FINDING"
                  ? "Mark as expected. Later runs stay quiet about this unless it changes."
                  : "Hide this occurrence. A new deviation on a later run is still reported."
              }
              className="shrink-0 border border-border px-2.5 py-1 text-xs text-foreground transition-colors hover:border-accent"
            >
              {a.kind === "FINDING" ? "Expected" : "Acknowledge"}
            </button>
          )}
        </li>
      ))}
    </ul>
  );
}
