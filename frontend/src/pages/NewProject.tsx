import { useCallback, useEffect, useRef, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import {
  api,
  type GitHubRepository,
  type GitHubStatus,
  type IngestJob,
  type ProposedCheck,
  type RepoAnalysis,
} from "../lib/api";
import { GitHubRepoPicker } from "../components/GitHubRepoPicker";
import { Breadcrumbs } from "../components/Breadcrumbs";
import { SnowflakeCredentialFields } from "../components/SnowflakeCredentialFields";
import {
  credentialsComplete,
  credentialsToConfig,
  emptyCredentials,
  type SnowflakeCredentials,
} from "../lib/snowflake-credentials";

type Step = 1 | 2 | 3;
type Source = "github" | "url";

const POLL_INTERVAL_MS = 1500;

/**
 * Fetches GitHub state without touching React state.
 *
 * The paste-a-URL path does not depend on GitHub at all, so anything going
 * wrong here has to narrow the options rather than break the screen - hence
 * an unconfigured-looking result on failure instead of a thrown error.
 */
async function fetchGitHub(): Promise<{
  status: GitHubStatus;
  repositories: GitHubRepository[];
}> {
  try {
    const status = await api.getGitHubStatus();
    // Only ask for repositories when there is something to ask about, so an
    // unconfigured server does not hit the GitHub API to be told "nothing".
    const repositories =
      status.configured && !status.error && status.installations.length > 0
        ? await api.listGitHubRepositories()
        : [];
    return { status, repositories };
  } catch {
    return {
      status: { configured: false, install_url: null, installations: [], error: null },
      repositories: [],
    };
  }
}

/**
 * Setup driven by the repository that defines the pipeline.
 *
 * The ordering is the point. A repository states what the pipeline *should*
 * be - which databases exist, which MERGE feeds what, on what key - and all of
 * that is readable without any warehouse credentials. So the repo comes first,
 * the review of what was found comes second, and credentials come last, at the
 * only moment they are actually needed: writing the project and connecting the
 * checks to something they can run against.
 *
 * Nothing is written until the final submit, so a wrong password or a
 * misparsed repo never leaves a half-built project behind.
 */
export function NewProject() {
  const navigate = useNavigate();
  const [step, setStep] = useState<Step>(1);

  const [source, setSource] = useState<Source>("github");
  const [repoUrl, setRepoUrl] = useState("");
  const [token, setToken] = useState("");
  const [ref, setRef] = useState("");
  const [showAdvanced, setShowAdvanced] = useState(false);

  const [githubStatus, setGithubStatus] = useState<GitHubStatus | null>(null);
  const [repositories, setRepositories] = useState<GitHubRepository[]>([]);
  // Starts true: the mount-time load is already in flight by first paint, so
  // initialising here rather than setting it from the effect keeps the effect
  // free of synchronous state updates.
  const [loadingRepos, setLoadingRepos] = useState(true);
  const [reloadCount, setReloadCount] = useState(0);

  const [job, setJob] = useState<IngestJob | null>(null);
  const [analysis, setAnalysis] = useState<RepoAnalysis | null>(null);

  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [selectedDatabases, setSelectedDatabases] = useState<Set<string>>(new Set());
  const [selectedChecks, setSelectedChecks] = useState<Set<string>>(new Set());

  const [credentials, setCredentials] = useState<SnowflakeCredentials>(emptyCredentials);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const jobIdRef = useRef<string | null>(null);

  const applyAnalysis = useCallback((result: RepoAnalysis) => {
    setAnalysis(result);
    setName(result.project_name);
    setDescription(result.project_description);
    // Everything found is selected by default. The review step is for removing
    // what does not belong, not for rebuilding the list by hand.
    setSelectedDatabases(new Set(result.databases.map((d) => d.name)));
    setSelectedChecks(new Set(result.checks.map((c) => c.key)));
    setStep(2);
  }, []);

  useEffect(() => {
    // Split deliberately: fetchGitHub only fetches, the effect only applies.
    // Keeping every setState behind an await (and behind the `active` guard)
    // means no synchronous state update runs during the effect, and nothing
    // is written after the page has been navigated away from.
    let active = true;
    void (async () => {
      const result = await fetchGitHub();
      if (!active) return;
      setGithubStatus(result.status);
      setRepositories(result.repositories);
      if (!result.status.configured) setSource("url");
      setLoadingRepos(false);
    })();
    return () => {
      active = false;
    };
  }, [reloadCount]);

  useEffect(() => {
    if (!job || job.status !== "RUNNING") return;
    const timer = setTimeout(async () => {
      try {
        const next = await api.getIngestion(job.id);
        setJob(next);
        if (next.status === "DONE" && next.analysis) applyAnalysis(next.analysis);
        if (next.status === "ERROR") setError(next.error ?? "Analysis failed.");
      } catch (e) {
        setError((e as Error).message);
        setJob(null);
      }
    }, POLL_INTERVAL_MS);
    return () => clearTimeout(timer);
  }, [job, applyAnalysis]);

  async function start(payload: {
    repo_url: string;
    token?: string;
    ref?: string;
    installation_id?: number;
  }) {
    setBusy(true);
    setError(null);
    try {
      const started = await api.startIngestion(payload);
      jobIdRef.current = started.id;
      setJob(started);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  async function analyze(event: React.FormEvent) {
    event.preventDefault();
    await start({
      repo_url: repoUrl.trim(),
      token: token.trim() || undefined,
      ref: ref.trim() || undefined,
    });
  }

  async function pickRepository(repository: GitHubRepository) {
    setRepoUrl(repository.clone_url);
    // The installation id goes over instead of a credential: the server mints
    // a short-lived token from it at clone time, so nothing readable ever
    // passes through the browser.
    await start({
      repo_url: repository.clone_url,
      ref: repository.default_branch,
      installation_id: repository.installation_id,
    });
  }

  async function finish() {
    if (!jobIdRef.current) return;
    setBusy(true);
    setError(null);
    try {
      const project = await api.createProjectFromIngestion(jobIdRef.current, {
        name,
        description: description || null,
        connector_name: "snowflake",
        config: credentialsToConfig(credentials),
        databases: [...selectedDatabases],
        checks: [...selectedChecks],
      });
      navigate(`/projects/${project.slug}`);
    } catch (e) {
      setError((e as Error).message);
      setBusy(false);
    }
  }

  function toggle(set: Set<string>, value: string, update: (next: Set<string>) => void) {
    const next = new Set(set);
    if (next.has(value)) next.delete(value);
    else next.add(value);
    update(next);
  }

  function toggleDatabase(database: string) {
    const next = new Set(selectedDatabases);
    if (next.has(database)) {
      next.delete(database);
      // A check reaches its project through its database, so a check whose
      // database is gone has nowhere to live. Dropping them together keeps the
      // count at the bottom of the page honest.
      const orphaned = new Set(selectedChecks);
      analysis?.checks.filter((c) => c.database === database).forEach((c) => orphaned.delete(c.key));
      setSelectedChecks(orphaned);
    } else {
      next.add(database);
    }
    setSelectedDatabases(next);
  }

  const analyzing = job?.status === "RUNNING";
  const checksFor = (database: string) => analysis?.checks.filter((c) => c.database === database) ?? [];
  const selectedCheckCount = selectedChecks.size;

  return (
    <main className="mx-auto max-w-3xl px-6 py-10 sm:px-10">
      <Breadcrumbs items={[{ label: "Projects", to: "/" }, { label: "New project" }]} />

      <header className="mb-6">
        <h1 className="text-2xl font-semibold tracking-tight text-foreground">New project</h1>
        <p className="mt-1 text-sm text-zinc-500">
          Point at the repository that defines your pipeline. The databases and a starting set of
          checks are derived from its DDL.
        </p>
      </header>

      <ol className="mb-6 flex items-center gap-2 text-sm">
        {[
          { n: 1 as Step, label: "Repository" },
          { n: 2 as Step, label: "Review" },
          { n: 3 as Step, label: "Connect" },
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
        <div className="space-y-5 rounded-xl border border-border bg-surface p-6">
          <div className="flex gap-1 rounded-lg border border-border bg-background p-1">
            {([
              { key: "github" as Source, label: "From GitHub" },
              { key: "url" as Source, label: "From a URL" },
            ]).map((option) => (
              <button
                key={option.key}
                type="button"
                onClick={() => setSource(option.key)}
                disabled={analyzing}
                className={`flex-1 rounded-md px-3 py-1.5 text-sm transition-colors ${
                  source === option.key
                    ? "bg-accent text-accent-foreground shadow-sm"
                    : "text-zinc-500 hover:text-foreground"
                }`}
              >
                {option.label}
              </button>
            ))}
          </div>

          {source === "github" && githubStatus && (
            <GitHubRepoPicker
              status={githubStatus}
              repositories={repositories}
              loading={loadingRepos}
              disabled={busy || Boolean(analyzing)}
              onPick={pickRepository}
              onRefresh={() => {
                setLoadingRepos(true);
                setReloadCount((n) => n + 1);
              }}
            />
          )}

          {source === "url" && (
        <form onSubmit={analyze} className="space-y-5">
          <div>
            <label htmlFor="repo" className="block text-sm font-medium text-foreground">
              Repository URL
            </label>
            <input
              id="repo"
              required
              autoFocus
              value={repoUrl}
              onChange={(e) => setRepoUrl(e.target.value)}
              placeholder="https://github.com/acme/data-platform"
              disabled={analyzing}
              className="mt-1.5 w-full rounded-md border border-border bg-background px-3 py-2 font-mono text-sm text-foreground outline-none focus:border-accent disabled:opacity-60"
            />
            <p className="mt-1.5 text-xs text-zinc-500">
              Any repository containing Snowflake DDL — <code>CREATE TABLE</code>, <code>MERGE</code>,
              tasks and streams.
            </p>
          </div>

          <div>
            <button
              type="button"
              onClick={() => setShowAdvanced((v) => !v)}
              className="text-sm text-zinc-500 transition-colors hover:text-foreground"
            >
              {showAdvanced ? "−" : "+"} Private repository or a specific branch
            </button>
          </div>

          {showAdvanced && (
            <div className="space-y-4 rounded-lg border border-border bg-background p-4">
              <div>
                <label htmlFor="token" className="block text-sm font-medium text-foreground">
                  Access token <span className="font-normal text-zinc-500">(optional)</span>
                </label>
                <input
                  id="token"
                  type="password"
                  value={token}
                  onChange={(e) => setToken(e.target.value)}
                  placeholder="ghp_…"
                  disabled={analyzing}
                  className="mt-1.5 w-full rounded-md border border-border bg-surface px-3 py-2 font-mono text-sm text-foreground outline-none focus:border-accent"
                />
                <p className="mt-1.5 text-xs text-zinc-500">
                  Used for this fetch only, then discarded — it is never stored.
                </p>
              </div>
              <div>
                <label htmlFor="ref" className="block text-sm font-medium text-foreground">
                  Branch or tag <span className="font-normal text-zinc-500">(optional)</span>
                </label>
                <input
                  id="ref"
                  value={ref}
                  onChange={(e) => setRef(e.target.value)}
                  placeholder="main"
                  disabled={analyzing}
                  className="mt-1.5 w-full rounded-md border border-border bg-surface px-3 py-2 font-mono text-sm text-foreground outline-none focus:border-accent"
                />
              </div>
            </div>
          )}

          <div className="flex items-center gap-3">
            <button
              type="submit"
              disabled={busy || analyzing || !repoUrl.trim()}
              className="rounded-md bg-accent px-4 py-2 text-sm font-medium text-accent-foreground shadow-sm transition-colors hover:bg-accent-hover disabled:opacity-50"
            >
              {analyzing ? "Analysing…" : "Analyse repository"}
            </button>
          </div>
        </form>
          )}

          {analyzing && (
            <div className="flex items-center gap-3 rounded-lg border border-accent/30 bg-accent-soft/40 px-4 py-3 text-sm">
              <span className="h-2 w-2 animate-pulse rounded-full bg-accent" />
              <span className="text-foreground">{job?.stage}…</span>
            </div>
          )}

          <Link
            to="/projects/new/manual"
            className="inline-block text-sm text-zinc-500 transition-colors hover:text-foreground"
          >
            Set up without a repository
          </Link>
        </div>
      )}

      {step === 2 && analysis && (
        <div className="space-y-5">
          <div className="rounded-lg border border-emerald-200 bg-emerald-50 px-4 py-3 text-sm dark:border-emerald-900 dark:bg-emerald-950/40">
            <p className="font-medium text-emerald-800 dark:text-emerald-300">
              Read {analysis.sql_files.length} SQL file{analysis.sql_files.length === 1 ? "" : "s"} ·{" "}
              {analysis.table_count} table{analysis.table_count === 1 ? "" : "s"} ·{" "}
              {analysis.databases.length} database{analysis.databases.length === 1 ? "" : "s"}
            </p>
            {analysis.repo_commit && (
              <p className="mt-0.5 font-mono text-xs text-emerald-700 dark:text-emerald-400">
                {analysis.repo_ref}@{analysis.repo_commit.slice(0, 8)}
                {analysis.repo_commit_subject ? ` · ${analysis.repo_commit_subject}` : ""}
              </p>
            )}
          </div>

          {/* Coverage sits above the proposals on purpose. A list of checks
              reads as success on its own, including when it came from a repo
              the parser barely understood - this is the line that tells the
              two apart. */}
          <div className="rounded-md border border-border bg-surface px-4 py-3 text-sm">
            <p className="font-medium text-foreground">{analysis.coverage.summary}</p>
            {analysis.coverage.uncovered.length > 0 && (
              <ul className="mt-2 space-y-1.5 text-zinc-500">
                {analysis.coverage.uncovered.map((table) => (
                  <li key={table.table}>
                    <span className="font-mono text-xs text-foreground">{table.table}</span>
                    {table.gaps.map((gap) => (
                      <span key={gap} className="block text-xs">
                        {gap}
                      </span>
                    ))}
                  </li>
                ))}
              </ul>
            )}
          </div>

          {analysis.warnings.length > 0 && (
            <ul className="space-y-1 rounded-md border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-800 dark:border-amber-900 dark:bg-amber-950 dark:text-amber-300">
              {analysis.warnings.map((warning) => (
                <li key={warning}>{warning}</li>
              ))}
            </ul>
          )}

          <div className="space-y-4 rounded-xl border border-border bg-surface p-6">
            <div>
              <label htmlFor="pname" className="block text-sm font-medium text-foreground">
                Project name
              </label>
              <input
                id="pname"
                value={name}
                onChange={(e) => setName(e.target.value)}
                className="mt-1.5 w-full rounded-md border border-border bg-background px-3 py-2 text-sm text-foreground outline-none focus:border-accent"
              />
            </div>
            <div>
              <label htmlFor="pdesc" className="block text-sm font-medium text-foreground">
                Description
              </label>
              <textarea
                id="pdesc"
                rows={2}
                value={description}
                onChange={(e) => setDescription(e.target.value)}
                className="mt-1.5 w-full rounded-md border border-border bg-background px-3 py-2 text-sm text-foreground outline-none focus:border-accent"
              />
            </div>
          </div>

          {analysis.databases.length === 0 ? (
            <p className="rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-800 dark:border-amber-900 dark:bg-amber-950 dark:text-amber-300">
              No Snowflake databases could be read from this repository. Check the URL and branch, or
              set the project up without a repository.
            </p>
          ) : (
            <div className="space-y-3">
              {analysis.databases.map((database) => {
                const on = selectedDatabases.has(database.name);
                return (
                  <section
                    key={database.name}
                    className={`rounded-xl border bg-surface transition-opacity ${
                      on ? "border-border" : "border-border opacity-50"
                    }`}
                  >
                    <label className="flex cursor-pointer items-start gap-3 p-4">
                      <input
                        type="checkbox"
                        checked={on}
                        onChange={() => toggleDatabase(database.name)}
                        className="mt-0.5 h-4 w-4 rounded border-border accent-[var(--accent)]"
                      />
                      <span className="min-w-0 flex-1">
                        <span className="block font-mono text-sm font-medium text-foreground">
                          {database.name}
                        </span>
                        <span className="mt-0.5 block text-xs text-zinc-500">{database.description}</span>
                        <span className="mt-1 block font-mono text-[11px] text-zinc-400">
                          {Object.entries(database.repo_paths)
                            .map(([schema, path]) => `${schema} → ${path}`)
                            .join("  ·  ")}
                        </span>
                      </span>
                    </label>

                    {on && checksFor(database.name).length > 0 && (
                      <ul className="border-t border-border px-4 py-2">
                        {checksFor(database.name).map((check) => (
                          <CheckRow
                            key={check.key}
                            check={check}
                            checked={selectedChecks.has(check.key)}
                            onToggle={() => toggle(selectedChecks, check.key, setSelectedChecks)}
                          />
                        ))}
                      </ul>
                    )}
                  </section>
                );
              })}
            </div>
          )}

          <div className="flex items-center gap-3">
            <button
              type="button"
              onClick={() => setStep(3)}
              disabled={selectedDatabases.size === 0 || !name.trim()}
              className="rounded-md bg-accent px-4 py-2 text-sm font-medium text-accent-foreground shadow-sm transition-colors hover:bg-accent-hover disabled:opacity-50"
            >
              Continue with {selectedDatabases.size} database{selectedDatabases.size === 1 ? "" : "s"} and{" "}
              {selectedCheckCount} check{selectedCheckCount === 1 ? "" : "s"}
            </button>
            <button
              type="button"
              onClick={() => {
                setStep(1);
                setJob(null);
              }}
              className="text-sm text-zinc-500 transition-colors hover:text-foreground"
            >
              Back
            </button>
          </div>
        </div>
      )}

      {step === 3 && (
        <div className="space-y-5 rounded-xl border border-border bg-surface p-6">
          <div>
            <h2 className="text-base font-medium text-foreground">Connect Snowflake</h2>
            <p className="mt-1 text-sm text-zinc-500">
              The repository said what to check; this says where to run it. Nothing is saved until the
              connection succeeds.
            </p>
          </div>

          <SnowflakeCredentialFields value={credentials} onChange={setCredentials} disabled={busy} />

          <div className="flex items-center gap-3">
            <button
              type="button"
              onClick={finish}
              disabled={busy || !credentialsComplete(credentials)}
              className="rounded-md bg-accent px-4 py-2 text-sm font-medium text-accent-foreground shadow-sm transition-colors hover:bg-accent-hover disabled:opacity-50"
            >
              {busy ? "Creating…" : "Create project"}
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

/**
 * One proposed check. The rationale is shown rather than hidden behind a
 * disclosure: a check the reviewer cannot justify is one they should be
 * deselecting, and making them click to find out why guarantees they will not.
 */
function CheckRow({
  check,
  checked,
  onToggle,
}: {
  check: ProposedCheck;
  checked: boolean;
  onToggle: () => void;
}) {
  return (
    <li>
      <label className="flex cursor-pointer items-start gap-3 rounded px-2 py-2 transition-colors hover:bg-accent-soft/40">
        <input
          type="checkbox"
          checked={checked}
          onChange={onToggle}
          className="mt-0.5 h-4 w-4 rounded border-border accent-[var(--accent)]"
        />
        <span className="min-w-0 flex-1">
          <span className="flex flex-wrap items-center gap-2">
            <span className="text-sm text-foreground">{check.name}</span>
            <span className="rounded bg-zinc-100 px-1.5 py-0.5 font-mono text-[10px] uppercase tracking-wide text-zinc-500 dark:bg-zinc-800">
              {check.type.replaceAll("_", " ")}
            </span>
            <span
              className={`rounded px-1.5 py-0.5 text-[10px] uppercase tracking-wide ${
                check.source === "llm"
                  ? "bg-accent-soft text-accent"
                  : "bg-zinc-100 text-zinc-500 dark:bg-zinc-800"
              }`}
            >
              {check.source === "llm" ? "agent" : "from DDL"}
            </span>
          </span>
          <span className="mt-0.5 block text-xs text-zinc-500">{check.rationale}</span>
          {check.concerns.map((concern) => (
            <span
              key={concern}
              className="mt-1.5 block rounded border border-amber-200 bg-amber-50 px-2 py-1 text-xs text-amber-800 dark:border-amber-900 dark:bg-amber-950 dark:text-amber-300"
            >
              {concern}
            </span>
          ))}
        </span>
      </label>
    </li>
  );
}
