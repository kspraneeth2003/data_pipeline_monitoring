import { Link, matchPath, useLocation } from "react-router-dom";

/**
 * Three modes, because the useful actions differ by altitude.
 *
 * Workspace level lists projects. Inside a project the first section is its
 * checks, grouped by pipeline stage - databases are a section further along,
 * not the way in, because a check is found by the hop it watches. Inside a
 * database it becomes that database's checks, and the primary action is
 * "New check", which only makes sense once there is a database to run it
 * against.
 */
export function TopNav() {
  const location = useLocation();
  // Read from the path rather than useParams: TopNav renders outside <Routes>,
  // so route params are not in scope here.
  const match = matchPath("/projects/:slug/*", location.pathname);
  const slug = match?.params.slug;
  // "new" is the create form, not a project.
  const inProject = Boolean(slug) && slug !== "new";

  const dbMatch = matchPath("/projects/:slug/databases/:dbSlug/*", location.pathname);
  const dbSlug = dbMatch?.params.dbSlug;
  const inDatabase = inProject && Boolean(dbSlug) && dbSlug !== "new";
  const base = inProject ? `/projects/${slug}` : "";

  const dbBase = `${base}/databases/${dbSlug}`;
  const inProjectDatabases = inProject && location.pathname === `${base}/databases`;

  const links = inDatabase
    ? [
        { href: base, label: "← Project", exact: true },
        { href: dbBase, label: "Checks", exact: true },
        { href: `${dbBase}/settings`, label: "Settings" },
      ]
    : inProject
      ? [
          { href: base, label: "Checks", exact: true },
          { href: `${base}/incidents`, label: "Incidents" },
          { href: `${base}/changes`, label: "Changes" },
          { href: `${base}/connections`, label: "Connections" },
          { href: `${base}/databases`, label: "Databases", exact: true },
          { href: `${base}/settings`, label: "Settings" },
        ]
      : [
          { href: "/", label: "Projects", exact: true },
        ];

  return (
    <nav className="sticky top-0 z-10 border-b border-border bg-background/85 backdrop-blur">
      <div className="mx-auto flex max-w-5xl items-center gap-1 px-6 py-3 sm:px-10">
        <Link to="/" className="mr-6 flex items-center gap-2.5">
          <span className="flex h-7 w-7 items-center justify-center bg-accent font-mono text-sm font-bold text-accent-foreground">
            D
          </span>
          <span className="font-mono text-sm font-semibold uppercase tracking-[0.22em] text-foreground">DPM</span>
        </Link>

        {links.map((link) => {
          const isActive = link.exact
            ? location.pathname === link.href || location.pathname === `${link.href}/`
            : location.pathname.startsWith(link.href);
          return (
            <Link
              key={link.href}
              to={link.href}
              className={`border-t-2 px-3 py-1.5 font-mono text-xs uppercase tracking-[0.14em] transition-colors ${
                isActive
                  ? "border-accent bg-accent-soft text-accent"
                  : "border-transparent text-zinc-500 hover:border-zinc-700 hover:text-foreground"
              }`}
            >
              {link.label}
            </Link>
          );
        })}

        {inDatabase ? (
          <Link
            to={`${dbBase}/checks/new`}
            className="ml-auto bg-accent px-4 py-1.5 font-mono text-xs font-semibold uppercase tracking-[0.14em] text-accent-foreground transition-colors hover:bg-accent-hover"
          >
            New check
          </Link>
        ) : inProjectDatabases ? (
          // Only offered on the databases page. On a page about checks, "Add
          // database" is an answer to a question nobody there is asking, and
          // it was the loudest thing on the screen.
          <Link
            to={`${base}/databases/new`}
            className="ml-auto bg-accent px-4 py-1.5 font-mono text-xs font-semibold uppercase tracking-[0.14em] text-accent-foreground transition-colors hover:bg-accent-hover"
          >
            Add database
          </Link>
        ) : inProject ? null : (
          <Link
            to="/projects/new"
            className="ml-auto bg-accent px-4 py-1.5 font-mono text-xs font-semibold uppercase tracking-[0.14em] text-accent-foreground transition-colors hover:bg-accent-hover"
          >
            New project
          </Link>
        )}
      </div>
    </nav>
  );
}
