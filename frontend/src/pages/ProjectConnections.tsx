import { useCallback, useEffect, useState } from "react";
import { useParams } from "react-router-dom";
import { api, type Connector, type Project } from "../lib/api";
import { Breadcrumbs } from "../components/Breadcrumbs";
import { DeleteButton } from "../components/DeleteButton";
import {
  SnowflakeCredentialFields,
  credentialsComplete,
  credentialsToConfig,
  emptyCredentials,
  type SnowflakeCredentials,
} from "../components/SnowflakeCredentialFields";

export function ProjectConnections() {
  const { slug = "" } = useParams();
  const [project, setProject] = useState<Project | null>(null);
  const [connections, setConnections] = useState<Connector[]>([]);
  const [adding, setAdding] = useState(false);
  const [name, setName] = useState("");
  const [credentials, setCredentials] = useState<SnowflakeCredentials>(emptyCredentials);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const reload = useCallback(() => {
    Promise.all([api.getProject(slug), api.listProjectConnectors(slug)])
      .then(([p, c]) => {
        setProject(p);
        setConnections(c);
      })
      .catch((e) => setError(e.message));
  }, [slug]);

  useEffect(reload, [reload]);

  async function add(event: React.FormEvent) {
    event.preventDefault();
    if (!project) return;
    setBusy(true);
    setError(null);
    try {
      await api.createConnector({
        name: name.trim() || "snowflake",
        project_id: project.id,
        type: "SNOWFLAKE",
        config: credentialsToConfig(credentials),
      });
      setAdding(false);
      setName("");
      setCredentials(emptyCredentials);
      reload();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  if (!project) return null;

  return (
    <main className="mx-auto max-w-3xl px-6 py-10 sm:px-10">
      <Breadcrumbs
        items={[
          { label: "Projects", to: "/" },
          { label: project.name, to: `/projects/${slug}` },
          { label: "Connections" },
        ]}
      />

      <header className="mb-6 flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight text-foreground">Connections</h1>
          <p className="mt-1 max-w-xl text-sm text-zinc-500">
            How {project.name} reaches its databases. Connections belong to this project, so changing one here
            cannot affect another project.
          </p>
        </div>
        {!adding && (
          <button
            onClick={() => setAdding(true)}
            className="rounded-md bg-accent px-3 py-1.5 text-sm font-medium text-accent-foreground shadow-sm transition-colors hover:bg-accent-hover"
          >
            Add connection
          </button>
        )}
      </header>

      {error && (
        <p className="mb-4 rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700 dark:border-red-900 dark:bg-red-950 dark:text-red-400">
          {error}
        </p>
      )}

      {adding && (
        <form onSubmit={add} className="mb-6 space-y-5 rounded-xl border border-border bg-surface p-6">
          <div>
            <label htmlFor="connName" className="block text-sm font-medium text-foreground">
              Name
            </label>
            <input
              id="connName"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="snowflake"
              className="mt-1.5 w-full rounded-md border border-border bg-background px-3 py-2 text-sm text-foreground outline-none focus:border-accent"
            />
          </div>

          <SnowflakeCredentialFields value={credentials} onChange={setCredentials} disabled={busy} />

          <div className="flex items-center gap-3">
            <button
              type="submit"
              disabled={busy || !credentialsComplete(credentials)}
              className="rounded-md bg-accent px-4 py-2 text-sm font-medium text-accent-foreground shadow-sm transition-colors hover:bg-accent-hover disabled:opacity-50"
            >
              {busy ? "Testing…" : "Test and save"}
            </button>
            <button
              type="button"
              onClick={() => {
                setAdding(false);
                setError(null);
              }}
              className="text-sm text-zinc-500 transition-colors hover:text-foreground"
            >
              Cancel
            </button>
          </div>
        </form>
      )}

      <div className="grid gap-4 sm:grid-cols-2">
        {connections.map((connection) => {
          const config = connection.config as { account?: string; username?: string; role?: string };
          const databases = project.databases.filter((d) => d.connector_id === connection.id);
          return (
            <div key={connection.id} className="rounded-xl border border-border bg-surface p-4">
              <div className="flex items-start justify-between gap-3">
                <div className="flex items-center gap-3">
                  <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-blue-50 text-sm font-bold text-blue-600 dark:bg-blue-500/10 dark:text-blue-400">
                    ❄
                  </span>
                  <div>
                    <div className="font-medium text-foreground">{connection.name}</div>
                    <div className="text-xs text-zinc-500">{connection.type}</div>
                  </div>
                </div>
                <DeleteButton
                  confirmMessage={
                    databases.length
                      ? `"${connection.name}" is used by ${databases.length} database(s) in this project. Delete it anyway? Their checks will stop running.`
                      : `Delete connection "${connection.name}"?`
                  }
                  onDelete={async () => {
                    await api.deleteConnector(connection.id);
                    reload();
                  }}
                />
              </div>

              <dl className="mt-3 space-y-1 text-xs text-zinc-500">
                {config.account && (
                  <div className="flex gap-1.5">
                    <dt>Account</dt>
                    <dd className="font-mono text-foreground">{config.account}</dd>
                  </div>
                )}
                {config.username && (
                  <div className="flex gap-1.5">
                    <dt>User</dt>
                    <dd className="text-foreground">{config.username}</dd>
                  </div>
                )}
                <div className="flex gap-1.5">
                  <dt>Databases</dt>
                  <dd className="text-foreground">{databases.length}</dd>
                </div>
              </dl>
            </div>
          );
        })}
      </div>

      {connections.length === 0 && !adding && (
        <div className="rounded-xl border border-dashed border-border bg-surface px-6 py-10 text-center">
          <h2 className="text-base font-medium text-foreground">No connections</h2>
          <p className="mx-auto mt-1 max-w-md text-sm text-zinc-500">
            {project.name} needs a connection before it can reach any databases.
          </p>
        </div>
      )}
    </main>
  );
}
