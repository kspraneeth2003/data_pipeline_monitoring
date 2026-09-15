import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "../lib/api";
import { Breadcrumbs } from "../components/Breadcrumbs";

export function NewProject() {
  const navigate = useNavigate();
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setSaving(true);
    setError(null);
    try {
      const project = await api.createProject({ name, description: description || null });
      // Land on the new project rather than back on the list: the next thing
      // anyone wants after creating a project is to put a check in it.
      navigate(`/projects/${project.slug}`);
    } catch (e) {
      setError((e as Error).message);
      setSaving(false);
    }
  }

  return (
    <main className="mx-auto max-w-2xl px-6 py-10 sm:px-10">
      <Breadcrumbs items={[{ label: "Projects", to: "/" }, { label: "New project" }]} />

      <header className="mb-6">
        <h1 className="text-2xl font-semibold tracking-tight text-foreground">New project</h1>
        <p className="mt-1 text-sm text-zinc-500">
          A project groups the checks for one source system or pipeline.
        </p>
      </header>

      <form onSubmit={submit} className="space-y-5 rounded-xl border border-border bg-surface p-6">
        <div>
          <label htmlFor="name" className="block text-sm font-medium text-foreground">
            Name
          </label>
          <input
            id="name"
            required
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="CRM"
            className="mt-1.5 w-full rounded-md border border-border bg-background px-3 py-2 text-sm text-foreground outline-none focus:border-accent"
          />
          <p className="mt-1 text-xs text-zinc-500">The URL is derived from this and can be changed later.</p>
        </div>

        <div>
          <label htmlFor="description" className="block text-sm font-medium text-foreground">
            Description <span className="font-normal text-zinc-500">(optional)</span>
          </label>
          <textarea
            id="description"
            rows={3}
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            placeholder="Customer records landing from the CRM into bronze, cleaned into silver."
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
            disabled={saving || !name.trim()}
            className="rounded-md bg-accent px-4 py-2 text-sm font-medium text-accent-foreground shadow-sm transition-colors hover:bg-accent-hover disabled:opacity-50"
          >
            {saving ? "Creating…" : "Create project"}
          </button>
          <button
            type="button"
            onClick={() => navigate("/")}
            className="text-sm text-zinc-500 transition-colors hover:text-foreground"
          >
            Cancel
          </button>
        </div>
      </form>
    </main>
  );
}
