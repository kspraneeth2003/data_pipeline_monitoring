import { useEffect, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { api, type Database, type Project } from "../lib/api";
import { Breadcrumbs } from "../components/Breadcrumbs";
import { DeleteButton } from "../components/DeleteButton";

export function DatabaseSettings() {
  const { slug = "", dbSlug = "" } = useParams();
  const navigate = useNavigate();
  const [project, setProject] = useState<Project | null>(null);
  const [database, setDatabase] = useState<Database | null>(null);
  const [description, setDescription] = useState("");
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    Promise.all([api.getProject(slug), api.getDatabase(slug, dbSlug)])
      .then(([p, d]) => {
        setProject(p);
        setDatabase(d);
        setDescription(d.description ?? "");
      })
      .catch((e) => setError(e.message));
  }, [slug, dbSlug]);

  async function save(event: React.FormEvent) {
    event.preventDefault();
    setSaving(true);
    setError(null);
    try {
      await api.updateDatabase(slug, dbSlug, { description: description || null });
      setSaved(true);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setSaving(false);
    }
  }

  if (!project || !database) return null;

  const checkCount = database.health.total_checks;

  return (
    <main className="mx-auto max-w-2xl px-6 py-10 sm:px-10">
      <Breadcrumbs
        items={[
          { label: "Projects", to: "/" },
          { label: project.name, to: `/projects/${slug}` },
          { label: database.name, to: `/projects/${slug}/databases/${dbSlug}` },
          { label: "Settings" },
        ]}
      />

      <h1 className="mb-6 text-2xl font-semibold tracking-tight text-foreground">Settings</h1>

      <form onSubmit={save} className="mb-8 space-y-5 rounded-xl border border-border bg-surface p-6">
        <div>
          <span className="block text-sm font-medium text-foreground">Database</span>
          <p className="mt-1 font-mono text-sm text-zinc-500">{database.name}</p>
          <p className="mt-1 text-xs text-zinc-500">
            The name is how the warehouse identifies it, so it is not editable here — remove and re-add to point
            at a different database.
          </p>
        </div>

        <div>
          <span className="block text-sm font-medium text-foreground">Connector</span>
          <p className="mt-1 text-sm text-zinc-500">{database.connector.name}</p>
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

      <section className="rounded-xl border border-red-200 bg-red-50/50 p-6 dark:border-red-900 dark:bg-red-950/20">
        <h2 className="text-sm font-semibold text-red-700 dark:text-red-400">Danger zone</h2>
        <p className="mt-1 text-sm text-zinc-600 dark:text-zinc-400">
          Removing {database.name} from {project.name} also deletes its {checkCount} check
          {checkCount === 1 ? "" : "s"} and their run history. The database itself is untouched.
        </p>
        <div className="mt-4">
          <DeleteButton
            confirmMessage={`Remove ${database.name} from ${project.name}? This deletes ${checkCount} check(s) and their run history. The database in Snowflake is not touched.`}
            onDelete={async () => {
              await api.deleteDatabase(slug, dbSlug);
              navigate(`/projects/${slug}`);
            }}
          />
        </div>
      </section>
    </main>
  );
}
