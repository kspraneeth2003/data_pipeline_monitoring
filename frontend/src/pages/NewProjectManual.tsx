import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { api, type ProbeResult } from "../lib/api";
import { Breadcrumbs } from "../components/Breadcrumbs";
import { SnowflakeCredentialFields } from "../components/SnowflakeCredentialFields";
import {
  credentialsComplete,
  credentialsToConfig,
  emptyCredentials,
  type SnowflakeCredentials,
} from "../lib/snowflake-credentials";

type Step = 1 | 2 | 3;

/**
 * Setup in one pass: name it, connect it, pick its databases.
 *
 * The connection is *probed* at step 2 - credentials are tested and the real
 * database list comes back - but nothing is written until the final submit. So
 * a wrong password is caught while the form is still open, and a half-created
 * project never exists to clean up. Step 3 is a pick from what the role can
 * actually see, which also surfaces "connected, but this role sees nothing"
 * immediately rather than as a failing check tomorrow.
 */
export function NewProject() {
  const navigate = useNavigate();
  const [step, setStep] = useState<Step>(1);

  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [credentials, setCredentials] = useState<SnowflakeCredentials>(emptyCredentials);
  const [probe, setProbe] = useState<ProbeResult | null>(null);
  const [selected, setSelected] = useState<Set<string>>(new Set());

  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function connect(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const result = await api.probeConnection({ type: "SNOWFLAKE", config: credentialsToConfig(credentials) });
      setProbe(result);
      setStep(3);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  async function finish() {
    setBusy(true);
    setError(null);
    try {
      const project = await api.setupProject({
        name,
        description: description || null,
        connector_name: "snowflake",
        config: credentialsToConfig(credentials),
        databases: [...selected],
      });
      navigate(`/projects/${project.slug}`);
    } catch (e) {
      setError((e as Error).message);
      setBusy(false);
    }
  }

  function toggle(database: string) {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(database)) next.delete(database);
      else next.add(database);
      return next;
    });
  }

  return (
    <main className="mx-auto max-w-2xl px-6 py-10 sm:px-10">
      <Breadcrumbs items={[{ label: "Projects", to: "/" }, { label: "New project" }]} />

      <header className="mb-6">
        <h1 className="text-2xl font-semibold tracking-tight text-foreground">New project</h1>
        <p className="mt-1 text-sm text-zinc-500">
          A project is a data product — the databases that together serve one domain.
        </p>
      </header>

      <ol className="mb-6 flex items-center gap-2 text-sm">
        {[
          { n: 1 as Step, label: "Name" },
          { n: 2 as Step, label: "Connect" },
          { n: 3 as Step, label: "Databases" },
        ].map((s, i) => (
          <li key={s.n} className="flex items-center gap-2">
            {i > 0 && <span className="text-zinc-300 dark:text-zinc-700">›</span>}
            <span
              className={`flex items-center gap-1.5 ${
                step === s.n ? "font-medium text-accent" : step > s.n ? "text-foreground" : "text-zinc-400"
              }`}
            >
              <span
                className={`flex h-5 w-5 items-center justify-center rounded-full text-xs ${
                  step === s.n
                    ? "bg-accent text-accent-foreground"
                    : step > s.n
                      ? "bg-emerald-100 text-emerald-700 dark:bg-emerald-950 dark:text-emerald-400"
                      : "bg-zinc-100 text-zinc-400 dark:bg-zinc-800"
                }`}
              >
                {step > s.n ? "✓" : s.n}
              </span>
              {s.label}
            </span>
          </li>
        ))}
      </ol>

      {error && (
        <p className="mb-4 rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700 dark:border-red-900 dark:bg-red-950 dark:text-red-400">
          {error}
        </p>
      )}

      {step === 1 && (
        <form
          onSubmit={(e) => {
            e.preventDefault();
            setStep(2);
          }}
          className="space-y-5 rounded-xl border border-border bg-surface p-6"
        >
          <div>
            <label htmlFor="name" className="block text-sm font-medium text-foreground">
              Name
            </label>
            <input
              id="name"
              required
              autoFocus
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="Customer 360"
              className="mt-1.5 w-full rounded-md border border-border bg-background px-3 py-2 text-sm text-foreground outline-none focus:border-accent"
            />
          </div>
          <div>
            <label htmlFor="description" className="block text-sm font-medium text-foreground">
              Description <span className="font-normal text-zinc-500">(optional)</span>
            </label>
            <textarea
              id="description"
              rows={2}
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              placeholder="CRM and billing sources feeding the gold 360 view."
              className="mt-1.5 w-full rounded-md border border-border bg-background px-3 py-2 text-sm text-foreground outline-none focus:border-accent"
            />
          </div>
          <div className="flex items-center gap-3">
            <button
              type="submit"
              disabled={!name.trim()}
              className="rounded-md bg-accent px-4 py-2 text-sm font-medium text-accent-foreground shadow-sm transition-colors hover:bg-accent-hover disabled:opacity-50"
            >
              Continue
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
      )}

      {step === 2 && (
        <form onSubmit={connect} className="space-y-5 rounded-xl border border-border bg-surface p-6">
          <div>
            <h2 className="text-base font-medium text-foreground">Connect Snowflake</h2>
            <p className="mt-1 text-sm text-zinc-500">
              This connection belongs to {name}. Nothing is saved until you finish.
            </p>
          </div>

          <SnowflakeCredentialFields value={credentials} onChange={setCredentials} disabled={busy} />

          <div className="flex items-center gap-3">
            <button
              type="submit"
              disabled={busy || !credentialsComplete(credentials)}
              className="rounded-md bg-accent px-4 py-2 text-sm font-medium text-accent-foreground shadow-sm transition-colors hover:bg-accent-hover disabled:opacity-50"
            >
              {busy ? "Connecting…" : "Connect"}
            </button>
            <button
              type="button"
              onClick={() => setStep(1)}
              className="text-sm text-zinc-500 transition-colors hover:text-foreground"
            >
              Back
            </button>
          </div>
        </form>
      )}

      {step === 3 && probe && (
        <div className="space-y-5 rounded-xl border border-border bg-surface p-6">
          <div className="rounded-lg border border-emerald-200 bg-emerald-50 px-4 py-3 text-sm dark:border-emerald-900 dark:bg-emerald-950/40">
            <p className="font-medium text-emerald-800 dark:text-emerald-300">Connected</p>
            <p className="mt-0.5 text-emerald-700 dark:text-emerald-400">
              {probe.username} on {probe.account}
              {probe.role ? ` · role ${probe.role}` : ""}
              {probe.warehouse ? ` · warehouse ${probe.warehouse}` : ""}
            </p>
          </div>

          <div>
            <h2 className="text-base font-medium text-foreground">Which databases does {name} use?</h2>
            <p className="mt-1 text-sm text-zinc-500">
              Checks live inside a database. You can add more later.
            </p>
          </div>

          {probe.databases.length === 0 ? (
            <p className="rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-800 dark:border-amber-900 dark:bg-amber-950 dark:text-amber-300">
              This role cannot see any databases. You can still create the project and add them later, but a
              role with access is needed before checks can run.
            </p>
          ) : (
            <ul className="max-h-72 space-y-1 overflow-y-auto rounded-md border border-border p-2">
              {probe.databases.map((database) => (
                <li key={database}>
                  <label className="flex cursor-pointer items-center gap-2.5 rounded px-2 py-1.5 transition-colors hover:bg-accent-soft/40">
                    <input
                      type="checkbox"
                      checked={selected.has(database)}
                      onChange={() => toggle(database)}
                      className="h-4 w-4 rounded border-border accent-[var(--accent)]"
                    />
                    <span className="font-mono text-sm text-foreground">{database}</span>
                  </label>
                </li>
              ))}
            </ul>
          )}

          <div className="flex items-center gap-3">
            <button
              type="button"
              onClick={finish}
              disabled={busy}
              className="rounded-md bg-accent px-4 py-2 text-sm font-medium text-accent-foreground shadow-sm transition-colors hover:bg-accent-hover disabled:opacity-50"
            >
              {busy
                ? "Creating…"
                : selected.size > 0
                  ? `Create project with ${selected.size} database${selected.size === 1 ? "" : "s"}`
                  : "Create project"}
            </button>
            <button
              type="button"
              onClick={() => setStep(2)}
              className="text-sm text-zinc-500 transition-colors hover:text-foreground"
            >
              Back
            </button>
          </div>
        </div>
      )}
    </main>
  );
}
