import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api, type Connector, type Project } from "../lib/api";
import { CheckForm } from "../components/CheckForm";
import { Breadcrumbs } from "../components/Breadcrumbs";

export function NewCheck() {
  const { slug = "" } = useParams();
  const [connectors, setConnectors] = useState<Connector[] | null>(null);
  const [project, setProject] = useState<Project | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    Promise.all([api.listConnectors(), api.getProject(slug)])
      .then(([c, p]) => {
        setConnectors(c);
        setProject(p);
      })
      .catch((e) => setError(e.message));
  }, [slug]);

  if (error) {
    return (
      <main className="mx-auto max-w-3xl px-6 py-10 sm:px-10">
        <div className="rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700 dark:border-red-900 dark:bg-red-950 dark:text-red-400">
          {error}
        </div>
      </main>
    );
  }

  if (!connectors || !project) return null;

  return (
    <main className="mx-auto max-w-3xl px-6 py-10 sm:px-10">
      <Breadcrumbs
        items={[
          { label: "Projects", to: "/" },
          { label: project.name, to: `/projects/${slug}` },
          { label: "Checks", to: `/projects/${slug}/checks` },
          { label: "New check" },
        ]}
      />

      <header className="mb-6">
        <h1 className="text-2xl font-semibold tracking-tight text-foreground">New check</h1>
        <p className="mt-1 text-sm text-zinc-500">
          This check will belong to <span className="font-medium text-foreground">{project.name}</span>.
        </p>
      </header>

      {connectors.length === 0 ? (
        <div className="rounded-xl border border-dashed border-border bg-surface px-6 py-10 text-center">
          <h2 className="text-base font-medium text-foreground">No connectors yet</h2>
          <p className="mx-auto mt-1 max-w-md text-sm text-zinc-500">
            A check needs a connector to query. Connectors are shared across all projects.
          </p>
          <Link
            to="/connectors"
            className="mt-5 inline-block rounded-md bg-accent px-4 py-2 text-sm font-medium text-accent-foreground shadow-sm transition-colors hover:bg-accent-hover"
          >
            Add a connector
          </Link>
        </div>
      ) : (
        <CheckForm connectors={connectors} projectId={project.id} projectSlug={slug} />
      )}
    </main>
  );
}
