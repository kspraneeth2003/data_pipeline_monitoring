/**
 * The API returns naive timestamps (`2026-09-15T13:18:29.874450`) with no
 * timezone marker, and the database stores them in UTC. `new Date()` reads a
 * bare string like that as *local* time, which silently shifts every timestamp
 * by the viewer's offset - a run that just happened reads as hours old.
 *
 * Everything that renders a server timestamp goes through here.
 */
export function parseServerDate(iso: string): Date {
  const hasZone = /[zZ]$|[+-]\d{2}:?\d{2}$/.test(iso);
  return new Date(hasZone ? iso : `${iso}Z`);
}

export function formatDateTime(iso: string | null | undefined): string {
  if (!iso) return "never";
  return parseServerDate(iso).toLocaleString();
}

export function relativeTime(iso: string | null | undefined): string {
  if (!iso) return "never run";
  const mins = Math.round((Date.now() - parseServerDate(iso).getTime()) / 60000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins}m ago`;
  const hours = Math.round(mins / 60);
  if (hours < 24) return `${hours}h ago`;
  return `${Math.round(hours / 24)}d ago`;
}
