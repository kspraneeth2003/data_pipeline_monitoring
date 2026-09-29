import { useMemo, useState } from "react";
import type { GitHubRepository, GitHubStatus } from "../lib/api";
// GitHub returns ISO timestamps *with* a zone, which parseServerDate detects -
// so this is safe to route through the same helper as the API's naive ones.
import { relativeTime } from "../lib/time";

/**
 * Choose from the repositories GitHub has been told to share with this app.
 *
 * The list is deliberately not "all your repositories" - it is only what was
 * granted on GitHub's own consent screen. That is the whole point of using a
 * GitHub App rather than a sign-in: the app cannot see the rest, so the list
 * cannot show them, and "Add repositories" has to go back to GitHub rather
 * than being something this UI could do on the user's behalf.
 */
export function GitHubRepoPicker({
  status,
  repositories,
  loading,
  disabled,
  onPick,
  onRefresh,
}: {
  status: GitHubStatus;
  repositories: GitHubRepository[];
  loading: boolean;
  disabled: boolean;
  onPick: (repository: GitHubRepository) => void;
  onRefresh: () => void;
}) {
  const [query, setQuery] = useState("");

  const filtered = useMemo(() => {
    const needle = query.trim().toLowerCase();
    if (!needle) return repositories;
    return repositories.filter(
      (r) =>
        r.full_name.toLowerCase().includes(needle) ||
        (r.description ?? "").toLowerCase().includes(needle),
    );
  }, [repositories, query]);

  if (!status.configured) {
    return (
      <p className="rounded-md border border-border bg-background px-3 py-2.5 text-sm text-zinc-500">
        GitHub is not set up on this server. Paste a repository URL instead, or add{" "}
        <code className="text-xs">GITHUB_APP_ID</code> and{" "}
        <code className="text-xs">GITHUB_APP_PRIVATE_KEY_PATH</code> to{" "}
        <code className="text-xs">backend/.env</code>.
      </p>
    );
  }

  if (status.error) {
    return (
      <p className="rounded-md border border-red-200 bg-red-50 px-3 py-2.5 text-sm text-red-700 dark:border-red-900 dark:bg-red-950 dark:text-red-400">
        {status.error}
      </p>
    );
  }

  if (loading) {
    return (
      <p className="flex items-center gap-2 rounded-md border border-border bg-background px-3 py-2.5 text-sm text-zinc-500">
        <span className="h-2 w-2 animate-pulse rounded-none bg-accent" />
        Loading your repositories…
      </p>
    );
  }

  if (repositories.length === 0) {
    return (
      <div className="space-y-3 rounded-md border border-border bg-background p-4">
        <p className="text-sm text-foreground">
          {status.installations.length === 0
            ? "No repositories have been shared with DPM yet."
            : "DPM is connected, but no repositories were selected."}
        </p>
        <p className="text-sm text-zinc-500">
          GitHub will ask which repositories to grant read access to. Nothing outside your
          selection is visible to this app.
        </p>
        {status.install_url && (
          <a
            href={status.install_url}
            className="inline-block rounded-md bg-accent px-4 py-2 text-sm font-medium text-accent-foreground shadow-sm transition-colors hover:bg-accent-hover"
          >
            Choose repositories on GitHub
          </a>
        )}
      </div>
    );
  }

  return (
    <div className="space-y-3">
      <div className="flex items-center gap-2">
        <input
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Filter repositories…"
          disabled={disabled}
          className="flex-1 rounded-md border border-border bg-background px-3 py-2 text-sm text-foreground outline-none focus:border-accent"
        />
        <button
          type="button"
          onClick={onRefresh}
          disabled={disabled}
          className="rounded-md border border-border px-3 py-2 text-sm text-zinc-500 transition-colors hover:text-foreground"
        >
          Refresh
        </button>
      </div>

      <ul className="max-h-80 space-y-1 overflow-y-auto rounded-md border border-border p-2">
        {filtered.map((repository) => (
          <li key={`${repository.installation_id}:${repository.full_name}`}>
            <button
              type="button"
              disabled={disabled}
              onClick={() => onPick(repository)}
              className="w-full rounded px-2.5 py-2 text-left transition-colors hover:bg-accent-soft/40 disabled:opacity-50"
            >
              <span className="flex flex-wrap items-center gap-2">
                <span className="font-mono text-sm text-foreground">{repository.full_name}</span>
                {repository.private && (
                  <span className="rounded bg-zinc-100 px-1.5 py-0.5 text-[10px] uppercase tracking-wide text-zinc-500 dark:bg-zinc-800">
                    private
                  </span>
                )}
                <span className="font-mono text-[11px] text-zinc-400">
                  {repository.default_branch}
                </span>
              </span>
              {repository.description && (
                <span className="mt-0.5 block truncate text-xs text-zinc-500">
                  {repository.description}
                </span>
              )}
              {repository.pushed_at && (
                <span className="mt-0.5 block text-[11px] text-zinc-400">
                  updated {relativeTime(repository.pushed_at)}
                </span>
              )}
            </button>
          </li>
        ))}
        {filtered.length === 0 && (
          <li className="px-2.5 py-2 text-sm text-zinc-500">Nothing matches “{query}”.</li>
        )}
      </ul>

      {status.install_url && (
        <a
          href={status.install_url}
          className="inline-block text-sm text-zinc-500 transition-colors hover:text-foreground"
        >
          + Grant access to more repositories
        </a>
      )}
    </div>
  );
}
