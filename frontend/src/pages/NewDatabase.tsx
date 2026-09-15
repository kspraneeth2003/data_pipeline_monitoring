import { useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api, type Connector, type Project } from "../lib/api";
import { Breadcrumbs } from "../components/Breadcrumbs";

export function NewDatabase() {
  const { slug = "" } = useParams();
  const navigate = useNavigate();

  const [project, setProject] = useState<Project | null>(null);
  const [connectors, setConnectors] = useState<Connector[]>([]);
  const [connectorId, setConnectorId] = useState("");
  const [available, setAvailable] = useState<string[] | null>(null);
  const [discovering, setDiscovering] = useState(false);
  const [discoverError, setDiscoverError] = useState<string | null>(null);
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    Promise.all([api.getProject(slug), api.listProjectConnectors(slug)])
      .then(([p, c]) => {
        setProject(p);
        setConnectors(c);
        setConnectorId(c[0]?.id ?? "");
      })
      .catch((e) => setError(e.message));
  }, [slug]);

  // Ask the warehouse what it actually has. Picking from a real list beats a
  // free-text field where a typo only surfaces as a failing check later.
  useEffect(() => {
    if (!connectorId) return;
    setDiscovering(true);
    setDiscoverError(null);
    setAvailable(null);
    api
      .discoverDatabases(connectorId)
      .then(setAvailable)
      .catch((e) => setDiscoverError(e.message))
      .finally(() => setDiscovering(false));
  }, [connectorId]);

  const alreadyAdded = new Set(project?.databases.map((d) => d.name) ?? []);

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setSaving(true);
    setError(null);
    try {
      const database = await api.createDatabase(slug, {
        name,
        connector_id: connectorId,
        description: description || null,
      });
      navigate(`/projects/${slug}/databases/${database.slug}`);
    } catch (e) {
      setError((e as Error).message);
      setSaving(false);
    }
  }

  if (!project) return null;

  return (
    <main className="mx-auto max-w-2xl px-6 py-10 sm:px-10">
      <Breadcrumbs
        items={[
          { label: "Projects", to: "/" },
          { label: project.name, to: `/projects/${slug}` },
          { label: "Add a database" },
        ]}
      />

      <header className="mb-6">
        <h1 className="text-2xl font-semibold tracking-tight text-foreground">Add a database</h1>
        <p className="mt-1 text-sm text-zinc-500">
          Databases this project reads from or writes to. Checks live inside one.
        </p>
      </header>

      {connectors.length === 0 ? (
        <div className="rounded-xl border border-dashed border-border bg-surface px-6 py-10 text-center">
          <h2 className="text-base font-medium text-foreground">No connections yet</h2>
          <p className="mx-auto mt-1 max-w-md text-sm text-zinc-500">
            A database is reached through a connection, and {project?.name ?? "this project"} has none yet.
          </p>
          <Link
            to={`/projects/${slug}/connections`}
            className="mt-5 inline-block rounded-md bg-accent px-4 py-2 text-sm font-medium text-accent-foreground shadow-sm transition-colors hover:bg-accent-hover"
          >
            Add a connection
          </Link>
        </div>
      ) : (
        <form onSubmit={submit} className="space-y-5 rounded-xl border border-border bg-surface p-6">
          <div>
            <label htmlFor="connector" className="block text-sm font-medium text-foreground">
              Connection
            </label>
            <select
              id="connector"
              value={connectorId}
              onChange={(e) => setConnectorId(e.target.value)}
              className="mt-1.5 w-full rounded-md border border-border bg-background px-3 py-2 text-sm text-foreground outline-none focus:border-accent"
            >
              {connectors.map((c) => (
                <option key={c.id} value={c.id}>
                  {c.name}
                </option>
              ))}
            </select>
          </div>

          <div>
            <label htmlFor="name" className="block text-sm font-medium text-foreground">
              Database
            </label>

            {discovering && <p className="mt-1.5 text-sm text-zinc-500">Reading databases from the connector…</p>}

            {discoverError && (
              <p className="mt-1.5 text-sm text-amber-700 dark:text-amber-400">
                Could not list databases ({discoverError}). Type the name instead.
              </p>
            )}

            {available && available.length > 0 ? (
              <select
                id="name"
                required
                value={name}
                onChange={(e) => setName(e.target.value)}
                className="mt-1.5 w-full rounded-md border border-border bg-background px-3 py-2 font-mono text-sm text-foreground outline-none focus:border-accent"
              >
                <option value="">Select a database…</option>
                {available.map((databaseName) => (
                  <option key={databaseName} value={databaseName} disabled={alreadyAdded.has(databaseName)}>
                    {databaseName}
                    {alreadyAdded.has(databaseName) ? " — already in this project" : ""}
                  </option>
                ))}
              </select>
            ) : (
              !discovering && (
                <input
                  id="name"
                  required
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                  placeholder="DPM_SRC_CRM"
                  className="mt-1.5 w-full rounded-md border border-border bg-background px-3 py-2 font-mono text-sm text-foreground outline-none focus:border-accent"
                />
              )
            )}
          </div>

          <div>
            <label htmlFor="description" className="block text-sm font-medium text-foreground">
              Description <span className="font-normal text-zinc-500">(optional)</span>
            </label>
            <textarea
              id="description"
              rows={2}
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              placeholder="Bronze landing and silver cleaned customer records."
              className="mt-1.5 w-full rounded-md border border-border bg-background px-3 py-2 text-sm text-foreground outline-none focus:border-accent"
            />
          </div>

          {error && (
            <p className="rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700 dark:border-red-900 dark:bg-red-950 dark:text-red-400">
              {error}
            </p>
          )}

          <div className="flex items-center gap-3">
            <button
              type="submit"
              disabled={saving || !name.trim() || !connectorId}
              className="rounded-md bg-accent px-4 py-2 text-sm font-medium text-accent-foreground shadow-sm transition-colors hover:bg-accent-hover disabled:opacity-50"
            >
              {saving ? "Adding…" : "Add database"}
            </button>
            <button
              type="button"
              onClick={() => navigate(`/projects/${slug}`)}
              className="text-sm text-zinc-500 transition-colors hover:text-foreground"
            >
              Cancel
            </button>
          </div>
        </form>
      )}
    </main>
  );
}
