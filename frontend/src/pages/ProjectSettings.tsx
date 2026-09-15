import { useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api, type Check, type Project } from "../lib/api";
import { Breadcrumbs } from "../components/Breadcrumbs";
import { DeleteButton } from "../components/DeleteButton";

export function ProjectSettings() {
  const { slug = "" } = useParams();
  const navigate = useNavigate();
  const [project, setProject] = useState<Project | null>(null);
  const [checks, setChecks] = useState<Check[]>([]);
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    Promise.all([api.getProject(slug), api.listProjectChecks(slug)])
      .then(([p, c]) => {
        setProject(p);
        setChecks(c);
        setName(p.name);
        setDescription(p.description ?? "");
      })
      .catch((e) => setError(e.message));
  }, [slug]);

  async function save(event: React.FormEvent) {
    event.preventDefault();
    setSaving(true);
    setError(null);
    try {
      const updated = await api.updateProject(slug, { name, description: description || null });
      setSaved(true);
      if (updated.slug !== slug) navigate(`/projects/${updated.slug}/settings`, { replace: true });
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setSaving(false);
    }
  }

  if (error && !project) {
    return (
      <main className="mx-auto max-w-2xl px-6 py-10 sm:px-10">
        <div className="rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700 dark:border-red-900 dark:bg-red-950 dark:text-red-400">
          {error}
        </div>
      </main>
    );
  }

  if (!project) return null;

  // Which connectors this project depends on. Connectors are workspace-level,
  // so this is a read-only view of what it uses rather than something to edit
  // here - that would imply per-project credentials, which is not the model.
  const connectorsInUse = Array.from(new Map(checks.map((c) => [c.connector.id, c.connector])).values());

  return (
    <main className="mx-auto max-w-2xl px-6 py-10 sm:px-10">
      <Breadcrumbs
        items={[{ label: "Projects", to: "/" }, { label: project.name, to: `/projects/${slug}` }, { label: "Settings" }]}
      />

      <h1 className="mb-6 text-2xl font-semibold tracking-tight text-foreground">Settings</h1>

      <form onSubmit={save} className="mb-8 space-y-5 rounded-xl border border-border bg-surface p-6">
        <div>
          <label htmlFor="name" className="block text-sm font-medium text-foreground">
            Name
          </label>
          <input
            id="name"
            required
            value={name}
            onChange={(e) => {
              setName(e.target.value);
              setSaved(false);
            }}
            className="mt-1.5 w-full rounded-md border border-border bg-background px-3 py-2 text-sm text-foreground outline-none focus:border-accent"
          />
        </div>

        <div>
          <label htmlFor="description" className="block text-sm font-medium text-foreground">
            Description
          </label>
          <textarea
            id="description"
            rows={3}
            value={description}
            onChange={(e) => {
              setDescription(e.target.value);
              setSaved(false);
            }}
            className="mt-1.5 w-full rounded-md border border-border bg-background px-3 py-2 text-sm text-foreground outline-none focus:border-accent"
          />
        </div>

        <div>
          <span className="block text-sm font-medium text-foreground">URL</span>
          <p className="mt-1 font-mono text-sm text-zinc-500">/projects/{project.slug}</p>
        </div>

        {error && (
          <p className="rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700 dark:border-red-900 dark:bg-red-950 dark:text-red-400">
            {error}
          </p>
        )}

        <div className="flex items-center gap-3">
          <button
            type="submit"
            disabled={saving}
            className="rounded-md bg-accent px-4 py-2 text-sm font-medium text-accent-foreground shadow-sm transition-colors hover:bg-accent-hover disabled:opacity-50"
          >
            {saving ? "Saving…" : "Save changes"}
          </button>
          {saved && <span className="text-sm text-emerald-600 dark:text-emerald-400">Saved</span>}
        </div>
      </form>

      <section className="mb-8 rounded-xl border border-border bg-surface p-6">
        <h2 className="text-sm font-semibold text-foreground">Connectors in use</h2>
        <p className="mt-1 text-sm text-zinc-500">
          Connectors are shared across projects, so they are managed{" "}
          <Link to="/connectors" className="text-accent hover:underline">
            at the workspace level
          </Link>
          .
        </p>
        {connectorsInUse.length === 0 ? (
          <p className="mt-3 text-sm text-zinc-500">None — this project has no checks yet.</p>
        ) : (
          <ul className="mt-3 space-y-1.5">
            {connectorsInUse.map((connector) => (
              <li key={connector.id} className="flex items-center gap-2 text-sm text-foreground">
                <span className="h-1.5 w-1.5 rounded-full bg-accent" />
                {connector.name}
              </li>
            ))}
          </ul>
        )}
      </section>

      <section className="rounded-xl border border-red-200 bg-red-50/50 p-6 dark:border-red-900 dark:bg-red-950/20">
        <h2 className="text-sm font-semibold text-red-700 dark:text-red-400">Danger zone</h2>
        <p className="mt-1 text-sm text-zinc-600 dark:text-zinc-400">
          Deleting {project.name} also deletes its {checks.length} check{checks.length === 1 ? "" : "s"} and all of
          their run history and tickets.
        </p>
        <div className="mt-4">
          <DeleteButton
            confirmMessage={`Delete project "${project.name}"? This deletes ${checks.length} check(s), their run history, and their tickets.`}
            onDelete={async () => {
              await api.deleteProject(slug);
              navigate("/");
            }}
          />
        </div>
      </section>
    </main>
  );
}
