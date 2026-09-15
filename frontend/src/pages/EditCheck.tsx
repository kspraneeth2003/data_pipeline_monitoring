import { useEffect, useState } from "react";
import { useParams } from "react-router-dom";
import { api, type Check, type Connector } from "../lib/api";
import { CheckForm } from "../components/CheckForm";
import { Breadcrumbs } from "../components/Breadcrumbs";

export function EditCheck() {
  const { id, slug = "", dbSlug = "" } = useParams<{ id: string; slug: string; dbSlug: string }>();
  const [check, setCheck] = useState<Check | null>(null);
  const [connectors, setConnectors] = useState<Connector[] | null>(null);

  useEffect(() => {
    if (!id) return;
    api.getCheck(id).then(setCheck);
    api.listProjectConnectors(slug).then(setConnectors);
  }, [id, slug]);

  if (!check || !connectors) return null;

  return (
    <div className="min-h-screen bg-background px-6 py-10 sm:px-10">
      <div className="mx-auto max-w-3xl">
        <Breadcrumbs
          items={[
            { label: "Projects", to: "/" },
            { label: check.database.project.name, to: `/projects/${slug}` },
            { label: check.database.name, to: `/projects/${slug}/databases/${dbSlug}` },
            { label: check.name, to: `/projects/${slug}/databases/${dbSlug}/checks/${check.id}` },
            { label: "Edit" },
          ]}
        />
        <h1 className="mb-6 text-2xl font-semibold tracking-tight text-foreground">Edit check</h1>

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
          databaseId={check.database_id}
          projectSlug={slug}
          databaseSlug={dbSlug}
        />
      </div>
    </div>
  );
}
