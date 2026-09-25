import { useState } from "react";

import { formatDateTime } from "../lib/time";

/**
 * One metric across a table's profile runs, small enough to sit in a table row.
 *
 * A single series, so no legend: the column header names it. The line is the
 * accent token; a run flagged as anomalous gets a larger marker in the
 * critical color *and* is named in the tooltip, so the flag never rests on
 * color alone. Every point has a hover target wider than the point itself,
 * because a 2px line is not something anyone can aim at.
 */

export type SparkPoint = { at: string; value: number | null; flagged?: boolean };

const WIDTH = 120;
const HEIGHT = 28;
const PAD = 4;

export function Sparkline({
  points,
  format,
  label,
}: {
  points: SparkPoint[];
  format: (value: number) => string;
  label: string;
}) {
  const [hover, setHover] = useState<number | null>(null);
  const values = points.map((p) => p.value).filter((v): v is number => v !== null);

  if (values.length < 2) {
    return <span className="font-mono text-[11px] text-zinc-500">{values.length ? "1 run" : "—"}</span>;
  }

  const min = Math.min(...values);
  const max = Math.max(...values);
  const span = max - min || 1;
  const step = (WIDTH - PAD * 2) / Math.max(points.length - 1, 1);
  const x = (i: number) => PAD + i * step;
  // A flat series sits mid-height rather than on the floor, so it reads as
  // "steady" and not as "zero".
  const y = (v: number) => (max === min ? HEIGHT / 2 : HEIGHT - PAD - ((v - min) / span) * (HEIGHT - PAD * 2));

  const path = points
    .map((p, i) => (p.value === null ? null : `${x(i).toFixed(1)},${y(p.value).toFixed(1)}`))
    .filter(Boolean)
    .join(" L ");

  const active = hover !== null ? points[hover] : null;

  return (
    <span className="relative inline-flex items-center">
      <svg
        width={WIDTH}
        height={HEIGHT}
        viewBox={`0 0 ${WIDTH} ${HEIGHT}`}
        role="img"
        aria-label={`${label}: ${values.length} runs, latest ${format(values[values.length - 1])}`}
        onMouseLeave={() => setHover(null)}
      >
        <path d={`M ${path}`} fill="none" stroke="var(--accent)" strokeWidth={2} strokeLinejoin="round" strokeLinecap="round" />
        {points.map((p, i) =>
          p.value !== null && (p.flagged || i === hover) ? (
            <circle
              key={`m-${i}`}
              cx={x(i)}
              cy={y(p.value)}
              r={p.flagged ? 4 : 3}
              fill={p.flagged ? "rgb(239 68 68)" : "var(--accent)"}
              stroke="var(--surface)"
              strokeWidth={2}
            />
          ) : null,
        )}
        {points.map((_, i) => (
          <rect
            key={`h-${i}`}
            x={x(i) - step / 2}
            y={0}
            width={Math.max(step, 6)}
            height={HEIGHT}
            fill="transparent"
            onMouseEnter={() => setHover(i)}
          />
        ))}
      </svg>
      {active && active.value !== null && (
        <span className="pointer-events-none absolute bottom-full left-1/2 z-20 mb-1 -translate-x-1/2 whitespace-nowrap border border-border bg-surface-raised px-2 py-1 text-[11px] text-foreground shadow-lg">
          <span className="font-mono">{format(active.value)}</span>
          <span className="text-zinc-500"> · {formatDateTime(active.at)}</span>
          {active.flagged && <span className="text-red-400"> · anomaly</span>}
        </span>
      )}
    </span>
  );
}
