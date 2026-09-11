import { useState } from "react";
import { Link } from "react-router-dom";
import { api, type TicketWithContext } from "../lib/api";

const COLUMNS: { status: TicketWithContext["status"]; label: string }[] = [
  { status: "TODO", label: "To Do" },
  { status: "IN_PROGRESS", label: "In Progress" },
  { status: "DONE", label: "Done" },
];

const PRIORITY_STYLES: Record<TicketWithContext["priority"], string> = {
  LOW: "bg-zinc-100 text-zinc-700 dark:bg-zinc-800 dark:text-zinc-300",
  MEDIUM: "bg-blue-50 text-blue-700 dark:bg-blue-500/10 dark:text-blue-400",
  HIGH: "bg-orange-50 text-orange-700 dark:bg-orange-500/10 dark:text-orange-400",
  CRITICAL: "bg-red-50 text-red-700 dark:bg-red-500/10 dark:text-red-400",
};

const PRIORITY_BORDER: Record<TicketWithContext["priority"], string> = {
  LOW: "border-l-zinc-300 dark:border-l-zinc-600",
  MEDIUM: "border-l-blue-400",
  HIGH: "border-l-orange-400",
  CRITICAL: "border-l-red-500",
};

const NEXT_STATUS: Record<TicketWithContext["status"], TicketWithContext["status"] | null> = {
  TODO: "IN_PROGRESS",
  IN_PROGRESS: "DONE",
  DONE: null,
};
const PREV_STATUS: Record<TicketWithContext["status"], TicketWithContext["status"] | null> = {
  TODO: null,
  IN_PROGRESS: "TODO",
  DONE: "IN_PROGRESS",
};

function initials(text: string): string {
  const namePart = text.includes("@") ? text.split("@")[0] : text;
  const parts = namePart.split(/[.\s_-]+/).filter(Boolean);
  return (parts[0]?.[0] ?? "?").toUpperCase() + (parts[1]?.[0]?.toUpperCase() ?? "");
}

function TicketCard({ ticket, onChanged }: { ticket: TicketWithContext; onChanged: () => void }) {
  const [busy, setBusy] = useState(false);

  async function move(status: TicketWithContext["status"]) {
    setBusy(true);
    try {
      await api.updateTicket(ticket.id, { status });
      onChanged();
    } catch (error) {
      alert(`Update failed: ${error instanceof Error ? error.message : String(error)}`);
    } finally {
      setBusy(false);
    }
  }

  const next = NEXT_STATUS[ticket.status];
  const prev = PREV_STATUS[ticket.status];

  return (
    <div className={`rounded-lg border border-l-4 border-border bg-surface p-3 text-sm shadow-sm transition-shadow hover:shadow-md ${PRIORITY_BORDER[ticket.priority]}`}>
      <div className="flex items-center justify-between">
        <span className="font-mono text-xs font-semibold text-zinc-500 dark:text-zinc-400">{ticket.key}</span>
        <span className={`rounded-full px-2 py-0.5 text-[10px] font-medium ${PRIORITY_STYLES[ticket.priority]}`}>{ticket.priority}</span>
      </div>
      <div className="mt-1 font-medium text-foreground">{ticket.title}</div>
      <p className="mt-1 line-clamp-3 whitespace-pre-line text-xs text-zinc-500 dark:text-zinc-400">{ticket.description}</p>
      <div className="mt-3 flex items-center justify-between text-xs">
        <Link to={`/checks/${ticket.check_id}`} className="text-zinc-500 hover:text-accent dark:text-zinc-400">
          {ticket.check_name}
        </Link>
        {ticket.assignee ? (
          <span title={ticket.assignee} className="flex h-5 w-5 items-center justify-center rounded-full bg-accent-soft text-[9px] font-semibold text-accent">
            {initials(ticket.assignee)}
          </span>
        ) : (
          <span className="text-zinc-400">Unassigned</span>
        )}
      </div>
      <div className="mt-2 flex gap-2 border-t border-border pt-2">
        {prev && (
          <button onClick={() => move(prev)} disabled={busy} className="rounded border border-border px-2 py-1 text-xs text-zinc-600 transition-colors hover:bg-zinc-100 disabled:opacity-50 dark:text-zinc-300 dark:hover:bg-zinc-800">
            &larr; {COLUMNS.find((c) => c.status === prev)?.label}
          </button>
        )}
        {next && (
          <button onClick={() => move(next)} disabled={busy} className="rounded border border-border px-2 py-1 text-xs text-zinc-600 transition-colors hover:bg-zinc-100 disabled:opacity-50 dark:text-zinc-300 dark:hover:bg-zinc-800">
            {COLUMNS.find((c) => c.status === next)?.label} &rarr;
          </button>
        )}
      </div>
    </div>
  );
}

export function TicketBoard({ tickets, onChanged }: { tickets: TicketWithContext[]; onChanged: () => void }) {
  return (
    <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
      {COLUMNS.map((col) => {
        const columnTickets = tickets.filter((t) => t.status === col.status);
        return (
          <div key={col.status} className="rounded-xl bg-zinc-100 p-3 dark:bg-white/[0.03]">
            <div className="mb-3 flex items-center justify-between px-1">
              <h2 className="text-sm font-semibold text-zinc-700 dark:text-zinc-300">{col.label}</h2>
              <span className="rounded-full bg-zinc-200 px-1.5 py-0.5 text-xs text-zinc-500 dark:bg-zinc-800 dark:text-zinc-400">
                {columnTickets.length}
              </span>
            </div>
            <div className="space-y-3">
              {columnTickets.map((t) => (
                <TicketCard key={t.id} ticket={t} onChanged={onChanged} />
              ))}
              {columnTickets.length === 0 && (
                <div className="rounded-lg border border-dashed border-border p-4 text-center text-xs text-zinc-400">Nothing here</div>
              )}
            </div>
          </div>
        );
      })}
    </div>
  );
}
