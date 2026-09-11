import { useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { CHECK_TYPES, getCheckTypeMeta } from "../lib/check-types";
import { api } from "../lib/api";

type ConnectorOption = { id: string; name: string };

type InitialCheck = {
  id: string;
  name: string;
  description: string | null;
  type: string;
  schedule: string;
  enabled: boolean;
  connector_id: string;
  secondary_connector_id: string | null;
  config: Record<string, unknown>;
};

function configFieldToString(value: unknown, kind: string): string {
  if (value === undefined || value === null) return "";
  if (kind === "json") return JSON.stringify(value, null, 2);
  return String(value);
}

export function CheckForm({ connectors, initial }: { connectors: ConnectorOption[]; initial?: InitialCheck }) {
  const navigate = useNavigate();
  const isEdit = Boolean(initial);

  const [name, setName] = useState(initial?.name ?? "");
  const [description, setDescription] = useState(initial?.description ?? "");
  const [type, setType] = useState(initial?.type ?? CHECK_TYPES[0].value);
  const [schedule, setSchedule] = useState(initial?.schedule ?? "*/5 * * * *");
  const [enabled, setEnabled] = useState(initial?.enabled ?? true);
  const [connectorId, setConnectorId] = useState(initial?.connector_id ?? connectors[0]?.id ?? "");
  const [secondaryConnectorId, setSecondaryConnectorId] = useState(initial?.secondary_connector_id ?? "");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  const meta = useMemo(() => getCheckTypeMeta(type)!, [type]);

  const [fieldValues, setFieldValues] = useState<Record<string, string>>(() => {
    const initialValues: Record<string, string> = {};
    if (initial) {
      const initialMeta = getCheckTypeMeta(initial.type);
      for (const field of initialMeta?.fields ?? []) {
        initialValues[field.key] = configFieldToString(initial.config[field.key], field.kind);
      }
    }
    return initialValues;
  });

  function setField(key: string, value: string) {
    setFieldValues((prev) => ({ ...prev, [key]: value }));
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setSubmitting(true);

    try {
      const config: Record<string, unknown> = {};
      for (const field of meta.fields) {
        const raw = fieldValues[field.key];
        if (raw === undefined || raw === "") {
          if (!field.optional) {
            setError(`Missing required field: ${field.label}`);
            setSubmitting(false);
            return;
          }
          continue;
        }
        if (field.kind === "number") {
          config[field.key] = Number(raw);
        } else if (field.kind === "json") {
          try {
            config[field.key] = JSON.parse(raw);
          } catch {
            setError(`Field "${field.label}" is not valid JSON`);
            setSubmitting(false);
            return;
          }
        } else {
          config[field.key] = raw;
        }
      }

      const payload = {
        name,
        description: description || undefined,
        type,
        schedule,
        enabled,
        connector_id: connectorId,
        secondary_connector_id: meta.needsSecondaryConnector ? secondaryConnectorId : undefined,
        config,
      };

      const result = isEdit ? await api.updateCheck(initial!.id, payload) : await api.createCheck(payload);
      navigate(`/checks/${result.id}`);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <form onSubmit={handleSubmit} className="space-y-5 rounded-xl border border-border bg-surface p-5 shadow-sm">
      <div className="grid gap-4 sm:grid-cols-2">
        <label className="flex flex-col gap-1 text-sm sm:col-span-2">
          <span className="font-medium text-zinc-700 dark:text-zinc-300">Name</span>
          <input required value={name} onChange={(e) => setName(e.target.value)} className={inputClass} />
        </label>

        <label className="flex flex-col gap-1 text-sm sm:col-span-2">
          <span className="font-medium text-zinc-700 dark:text-zinc-300">Description (optional)</span>
          <input value={description} onChange={(e) => setDescription(e.target.value)} className={inputClass} />
        </label>

        <label className="flex flex-col gap-1 text-sm">
          <span className="font-medium text-zinc-700 dark:text-zinc-300">Type</span>
          <select value={type} onChange={(e) => setType(e.target.value)} className={inputClass}>
            {CHECK_TYPES.map((t) => (
              <option key={t.value} value={t.value}>
                {t.label}
              </option>
            ))}
          </select>
          <span className="text-xs text-zinc-500 dark:text-zinc-400">{meta.description}</span>
        </label>

        <label className="flex flex-col gap-1 text-sm">
          <span className="font-medium text-zinc-700 dark:text-zinc-300">Schedule (cron)</span>
          <input required value={schedule} onChange={(e) => setSchedule(e.target.value)} className={`${inputClass} font-mono`} />
        </label>

        <label className="flex flex-col gap-1 text-sm">
          <span className="font-medium text-zinc-700 dark:text-zinc-300">
            {meta.needsSecondaryConnector ? "Primary connector" : "Connector"}
          </span>
          <select required value={connectorId} onChange={(e) => setConnectorId(e.target.value)} className={inputClass}>
            {connectors.map((c) => (
              <option key={c.id} value={c.id}>
                {c.name}
              </option>
            ))}
          </select>
        </label>

        {meta.needsSecondaryConnector && (
          <label className="flex flex-col gap-1 text-sm">
            <span className="font-medium text-zinc-700 dark:text-zinc-300">Secondary connector</span>
            <select
              required
              value={secondaryConnectorId}
              onChange={(e) => setSecondaryConnectorId(e.target.value)}
              className={inputClass}
            >
              <option value="" disabled>
                Select…
              </option>
              {connectors.map((c) => (
                <option key={c.id} value={c.id}>
                  {c.name}
                </option>
              ))}
            </select>
          </label>
        )}

        <label className="flex items-center gap-2 text-sm">
          <input type="checkbox" checked={enabled} onChange={(e) => setEnabled(e.target.checked)} />
          <span className="font-medium text-zinc-700 dark:text-zinc-300">Enabled</span>
        </label>
      </div>

      <fieldset className="space-y-3 border-t border-border pt-4">
        <legend className="mb-1 text-sm font-semibold text-zinc-700 dark:text-zinc-300">{meta.label} config</legend>
        {meta.fields.map((field) => (
          <label key={field.key} className="flex flex-col gap-1 text-sm">
            <span className="font-medium text-zinc-700 dark:text-zinc-300">
              {field.label}
              {field.optional && <span className="ml-1 text-xs text-zinc-400">(optional)</span>}
            </span>
            {field.kind === "json" ? (
              <textarea
                value={fieldValues[field.key] ?? ""}
                onChange={(e) => setField(field.key, e.target.value)}
                placeholder={field.placeholder}
                rows={4}
                className={`${inputClass} font-mono text-xs`}
              />
            ) : (
              <input
                type={field.kind === "number" ? "number" : "text"}
                step={field.kind === "number" ? "any" : undefined}
                value={fieldValues[field.key] ?? ""}
                onChange={(e) => setField(field.key, e.target.value)}
                placeholder={field.placeholder}
                className={inputClass}
              />
            )}
          </label>
        ))}
      </fieldset>

      {error && <p className="text-sm text-red-600">{error}</p>}

      <div className="flex gap-2">
        <button
          type="submit"
          disabled={submitting || !connectorId}
          className="rounded-md bg-accent px-4 py-2 text-sm font-medium text-accent-foreground shadow-sm transition-colors hover:bg-accent-hover disabled:opacity-50"
        >
          {submitting ? "Saving…" : isEdit ? "Save changes" : "Create check"}
        </button>
      </div>
    </form>
  );
}

const inputClass =
  "rounded-md border border-border bg-background px-2.5 py-1.5 text-sm outline-none transition-colors focus:border-accent focus:ring-2 focus:ring-accent/20";
