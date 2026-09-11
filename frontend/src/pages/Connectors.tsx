import { useEffect, useState } from "react";
import { api, type Connector } from "../lib/api";
import { NewConnectorForm } from "../components/NewConnectorForm";
import { DeleteButton } from "../components/DeleteButton";

export function Connectors() {
  const [connectors, setConnectors] = useState<Connector[]>([]);
  const [loading, setLoading] = useState(true);

  function reload() {
    api.listConnectors().then(setConnectors).finally(() => setLoading(false));
  }

  useEffect(reload, []);

  if (loading) return null;

  return (
    <div className="min-h-screen bg-background px-6 py-10 sm:px-10">
      <div className="mx-auto max-w-5xl">
        <header className="mb-6">
          <h1 className="text-2xl font-semibold tracking-tight text-foreground">Connectors</h1>
          <p className="mt-1 text-sm text-zinc-500 dark:text-zinc-400">
            Connect any Snowflake account - credentials are encrypted at rest. Adding a new source type (e.g. RDS)
            means adding one connector implementation, not touching the check engine.
          </p>
        </header>

        <div className="mb-6">
          <NewConnectorForm onCreated={reload} />
        </div>

        <div className="grid gap-4 sm:grid-cols-2">
          {connectors.map((c) => {
            const config = c.config as { account?: string; username?: string };
            return (
              <div key={c.id} className="rounded-xl border border-border bg-surface p-4 shadow-sm">
                <div className="flex items-start justify-between">
                  <div className="flex items-center gap-3">
                    <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-blue-50 text-sm font-bold text-blue-600 dark:bg-blue-500/10 dark:text-blue-400">
                      ❄
                    </span>
                    <div>
                      <div className="font-medium text-foreground">{c.name}</div>
                      <div className="text-xs text-zinc-500 dark:text-zinc-400">{c.type}</div>
                    </div>
                  </div>
                  <DeleteButton
                    confirmMessage={`Delete connector "${c.name}"?`}
                    onDelete={async () => {
                      await api.deleteConnector(c.id);
                      reload();
                    }}
                  />
                </div>

                {c.comment && <p className="mt-3 text-xs text-zinc-500 dark:text-zinc-400">{c.comment}</p>}

                <dl className="mt-3 space-y-1 text-xs text-zinc-500 dark:text-zinc-400">
                  {config.account && (
                    <div>
                      <dt className="inline font-medium text-zinc-600 dark:text-zinc-300">Account:</dt>{" "}
                      <dd className="inline font-mono">{config.account}</dd>
                    </div>
                  )}
                  {config.username && (
                    <div>
                      <dt className="inline font-medium text-zinc-600 dark:text-zinc-300">User:</dt>{" "}
                      <dd className="inline font-mono">{config.username}</dd>
                    </div>
                  )}
                </dl>

                <div className="mt-3 border-t border-border pt-3 text-xs text-zinc-500 dark:text-zinc-400">
                  Used by {c.checks_count} check{c.checks_count === 1 ? "" : "s"}
                </div>
              </div>
            );
          })}

          {connectors.length === 0 && (
            <div className="col-span-2 rounded-xl border border-dashed border-border p-10 text-center text-sm text-zinc-500">
              No connectors yet.
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
