import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api, type Check, type Connector } from "../lib/api";
import { CheckForm } from "../components/CheckForm";

export function EditCheck() {
  const { id } = useParams<{ id: string }>();
  const [check, setCheck] = useState<Check | null>(null);
  const [connectors, setConnectors] = useState<Connector[] | null>(null);

  useEffect(() => {
    if (!id) return;
    api.getCheck(id).then(setCheck);
    api.listConnectors().then(setConnectors);
  }, [id]);

  if (!check || !connectors) return null;

  return (
    <div className="min-h-screen bg-background px-6 py-10 sm:px-10">
      <div className="mx-auto max-w-3xl">
        <Link to={`/checks/${check.id}`} className="text-sm text-zinc-500 hover:text-accent dark:text-zinc-400">
          &larr; Back to check
        </Link>
        <h1 className="mt-3 mb-6 text-2xl font-semibold tracking-tight text-foreground">Edit check</h1>

        <CheckForm
          connectors={connectors}
          initial={{
            id: check.id,
            name: check.name,
            description: check.description,
            type: check.type,
            schedule: check.schedule,
            enabled: check.enabled,
            connector_id: check.connector_id,
            secondary_connector_id: check.secondary_connector_id,
            config: check.config,
          }}
        />
      </div>
    </div>
  );
}
