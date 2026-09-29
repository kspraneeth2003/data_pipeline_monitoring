import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api, type Connector, type Database } from "../lib/api";
import { CheckForm } from "../components/CheckForm";
import { Breadcrumbs } from "../components/Breadcrumbs";

export function NewCheck() {
  const { slug = "", dbSlug = "" } = useParams();
  const [connectors, setConnectors] = useState<Connector[] | null>(null);
  const [database, setDatabase] = useState<Database | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    Promise.all([api.listProjectConnectors(slug), api.getDatabase(slug, dbSlug)])
      .then(([c, d]) => {
        setConnectors(c);
        setDatabase(d);
      })
      .catch((e) => setError(e.message));
  }, [slug, dbSlug]);

  if (error) {
    return (
      <main className="mx-auto max-w-3xl px-6 py-10 sm:px-10">
        <div className="rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700 dark:border-red-900 dark:bg-red-950 dark:text-red-400">
          {error}
        </div>
      </main>
    );
  }

  if (!connectors || !database) return null;

  return (
    <main className="mx-auto max-w-3xl px-6 py-10 sm:px-10">
      <Breadcrumbs
        items={[
          { label: "Projects", to: "/" },
          { label: "Project", to: `/projects/${slug}` },
          { label: database.name, to: `/projects/${slug}/databases/${dbSlug}` },
          { label: "New check" },
        ]}
      />

      <header className="mb-6">
        <h1 className="text-2xl font-semibold tracking-[0.08em] text-foreground">New check</h1>
        <p className="mt-1 text-sm text-zinc-500">
          This check will run against <span className="font-mono font-medium text-foreground">{database.name}</span>.
          It can still reference objects in sibling databases.
        </p>
      </header>

      {connectors.length === 0 ? (
        <div className="rounded-xl border border-dashed border-border bg-surface px-6 py-10 text-center">
          <h2 className="text-base font-medium text-foreground">No connections yet</h2>
          <p className="mx-auto mt-1 max-w-md text-sm text-zinc-500">
            A check needs a connection to query through.
          </p>
          <Link
            to={`/projects/${slug}/connections`}
            className="mt-5 inline-block rounded-md bg-accent px-4 py-2 text-sm font-medium text-accent-foreground shadow-sm transition-colors hover:bg-accent-hover"
          >
            Add a connection
          </Link>
        </div>
      ) : (
        <CheckForm connectors={connectors} databaseId={database.id} projectSlug={slug} databaseSlug={dbSlug} />
      )}
    </main>
  );
}
