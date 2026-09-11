import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, type Connector } from "../lib/api";
import { CheckForm } from "../components/CheckForm";

export function NewCheck() {
  const [connectors, setConnectors] = useState<Connector[] | null>(null);

  useEffect(() => {
    api.listConnectors().then(setConnectors);
  }, []);

  if (!connectors) return null;

  return (
    <div className="min-h-screen bg-background px-6 py-10 sm:px-10">
      <div className="mx-auto max-w-3xl">
        <Link to="/" className="text-sm text-zinc-500 hover:text-accent dark:text-zinc-400">
          &larr; All checks
        </Link>
        <h1 className="mt-3 mb-6 text-2xl font-semibold tracking-tight text-foreground">New check</h1>

        {connectors.length === 0 ? (
          <p className="text-sm text-zinc-600 dark:text-zinc-400">
            No connectors yet.{" "}
            <Link to="/connectors" className="underline">
              Create one first
            </Link>
            .
          </p>
        ) : (
          <CheckForm connectors={connectors} />
        )}
      </div>
    </div>
  );
}
