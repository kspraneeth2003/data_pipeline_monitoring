import { useState } from "react";
import { api } from "../lib/api";

type AuthMethod = "SNOWFLAKE_JWT" | "SNOWFLAKE";

const initialState = {
  name: "",
  comment: "",
  account: "",
  username: "",
  authMethod: "SNOWFLAKE_JWT" as AuthMethod,
  password: "",
  privateKey: "",
  role: "",
  warehouse: "",
  database: "",
};

export function NewConnectorForm({ onCreated }: { onCreated: () => void }) {
  const [open, setOpen] = useState(false);
  const [form, setForm] = useState(initialState);
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  function set<K extends keyof typeof initialState>(key: K, value: (typeof initialState)[K]) {
    setForm((prev) => ({ ...prev, [key]: value }));
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setSubmitting(true);
    setError(null);
    try {
      const config: Record<string, unknown> = {
        account: form.account,
        username: form.username,
        authenticator: form.authMethod,
        role: form.role || undefined,
        warehouse: form.warehouse || undefined,
        database: form.database || undefined,
      };
      if (form.authMethod === "SNOWFLAKE_JWT") {
        config.privateKey = form.privateKey;
      } else {
        config.password = form.password;
      }

      await api.createConnector({ name: form.name, type: "SNOWFLAKE", comment: form.comment || undefined, config });
      setForm(initialState);
      setOpen(false);
      onCreated();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setSubmitting(false);
    }
  }

  if (!open) {
    return (
      <button
        onClick={() => setOpen(true)}
        className="rounded-md bg-accent px-3 py-1.5 text-sm font-medium text-accent-foreground shadow-sm transition-colors hover:bg-accent-hover"
      >
        + Connect a Snowflake account
      </button>
    );
  }

  return (
    <form onSubmit={handleSubmit} className="space-y-4 rounded-xl border border-border bg-surface p-5 shadow-sm">
      <p className="text-sm text-zinc-500 dark:text-zinc-400">
        Connect any Snowflake account - credentials are encrypted before being stored.
      </p>

      <div className="grid gap-3 sm:grid-cols-2">
        <Field label="Connector name">
          <input required value={form.name} onChange={(e) => set("name", e.target.value)} placeholder="prod-snowflake" className={inputClass} />
        </Field>
        <Field label="Comment (optional)">
          <input value={form.comment} onChange={(e) => set("comment", e.target.value)} className={inputClass} />
        </Field>

        <Field label="Account identifier">
          <input
            required
            value={form.account}
            onChange={(e) => set("account", e.target.value)}
            placeholder="xy12345 or org-account"
            className={inputClass}
          />
        </Field>
        <Field label="Username">
          <input required value={form.username} onChange={(e) => set("username", e.target.value)} className={inputClass} />
        </Field>

        <Field label="Auth method">
          <select value={form.authMethod} onChange={(e) => set("authMethod", e.target.value as AuthMethod)} className={inputClass}>
            <option value="SNOWFLAKE_JWT">Key pair (private key)</option>
            <option value="SNOWFLAKE">Password</option>
          </select>
        </Field>
        <Field label="Role (optional)">
          <input value={form.role} onChange={(e) => set("role", e.target.value)} className={inputClass} />
        </Field>

        {form.authMethod === "SNOWFLAKE_JWT" ? (
          <Field label="Private key (PEM)" full>
            <textarea
              required
              value={form.privateKey}
              onChange={(e) => set("privateKey", e.target.value)}
              rows={5}
              placeholder={"-----BEGIN PRIVATE KEY-----\n...\n-----END PRIVATE KEY-----"}
              className={`${inputClass} font-mono text-xs`}
            />
          </Field>
        ) : (
          <Field label="Password" full>
            <input required type="password" value={form.password} onChange={(e) => set("password", e.target.value)} className={inputClass} />
          </Field>
        )}

        <Field label="Warehouse (optional)">
          <input value={form.warehouse} onChange={(e) => set("warehouse", e.target.value)} className={inputClass} />
        </Field>
        <Field label="Default database (optional)">
          <input value={form.database} onChange={(e) => set("database", e.target.value)} className={inputClass} />
        </Field>
      </div>

      {error && <p className="text-sm text-red-600">{error}</p>}

      <div className="flex gap-2">
        <button
          type="submit"
          disabled={submitting}
          className="rounded-md bg-accent px-3 py-1.5 text-sm font-medium text-accent-foreground shadow-sm transition-colors hover:bg-accent-hover disabled:opacity-50"
        >
          {submitting ? "Testing connection…" : "Test & create connector"}
        </button>
        <button
          type="button"
          onClick={() => setOpen(false)}
          className="rounded-md border border-border px-3 py-1.5 text-sm font-medium text-zinc-700 hover:bg-zinc-100 dark:text-zinc-200 dark:hover:bg-zinc-800"
        >
          Cancel
        </button>
      </div>
    </form>
  );
}

const inputClass =
  "rounded-md border border-border bg-background px-2.5 py-1.5 text-sm outline-none transition-colors focus:border-accent focus:ring-2 focus:ring-accent/20";

function Field({ label, full, children }: { label: string; full?: boolean; children: React.ReactNode }) {
  return (
    <label className={`flex flex-col gap-1 text-sm ${full ? "sm:col-span-2" : ""}`}>
      <span className="font-medium text-zinc-700 dark:text-zinc-300">{label}</span>
      {children}
    </label>
  );
}
