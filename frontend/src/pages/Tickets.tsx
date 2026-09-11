import { useEffect, useState } from "react";
import { api, type TicketWithContext } from "../lib/api";
import { TicketBoard } from "../components/TicketBoard";

export function Tickets() {
  const [tickets, setTickets] = useState<TicketWithContext[]>([]);
  const [loading, setLoading] = useState(true);

  function reload() {
    api.listTickets().then(setTickets).finally(() => setLoading(false));
  }

  useEffect(reload, []);

  if (loading) return null;

  return (
    <div className="min-h-screen bg-background px-6 py-10 sm:px-10">
      <div className="mx-auto max-w-5xl">
        <header className="mb-6">
          <h1 className="text-2xl font-semibold tracking-tight text-foreground">Tickets</h1>
          <p className="mt-1 text-sm text-zinc-500 dark:text-zinc-400">
            Simulated Jira board - tickets are auto-filed here (pre-filled from RCA) when a check fails, since no
            real Jira credentials are wired up yet.
          </p>
        </header>

        <TicketBoard tickets={tickets} onChanged={reload} />
      </div>
    </div>
  );
}
