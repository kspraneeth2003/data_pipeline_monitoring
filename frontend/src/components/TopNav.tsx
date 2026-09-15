import { Link, matchPath, useLocation } from "react-router-dom";

/**
 * Three modes, because the useful actions differ by altitude.
 *
 * Workspace level lists projects. Inside a
 * project the sections become its databases and tickets, and the primary action
 * is "Add database". Inside a database it becomes that database's checks, and
 * the primary action is "New check" - which only makes sense once there is a
 * database to run it against.
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

  const links = inDatabase
    ? [
        { href: base, label: "← Project", exact: true },
        { href: dbBase, label: "Checks", exact: true },
        { href: `${dbBase}/settings`, label: "Settings" },
      ]
    : inProject
      ? [
          { href: base, label: "Databases", exact: true },
          { href: `${base}/tickets`, label: "Tickets" },
          { href: `${base}/connections`, label: "Connections" },
          { href: `${base}/settings`, label: "Settings" },
        ]
      : [
          { href: "/", label: "Projects", exact: true },
        ];

  return (
    <nav className="sticky top-0 z-10 border-b border-border bg-surface/80 backdrop-blur">
      <div className="mx-auto flex max-w-5xl items-center gap-1 px-6 py-3 sm:px-10">
        <Link to="/" className="mr-4 flex items-center gap-2">
          <span className="flex h-7 w-7 items-center justify-center rounded-md bg-accent text-sm font-bold text-accent-foreground">
            D
          </span>
          <span className="font-semibold text-foreground">DPM</span>
        </Link>

        {links.map((link) => {
          const isActive = link.exact
            ? location.pathname === link.href || location.pathname === `${link.href}/`
            : location.pathname.startsWith(link.href);
          return (
            <Link
              key={link.href}
              to={link.href}
              className={`rounded-md px-3 py-1.5 text-sm font-medium transition-colors ${
                isActive
                  ? "bg-accent-soft text-accent"
                  : "text-zinc-600 hover:bg-zinc-100 hover:text-zinc-900 dark:text-zinc-400 dark:hover:bg-zinc-900 dark:hover:text-zinc-100"
              }`}
            >
              {link.label}
            </Link>
          );
        })}

        {inDatabase ? (
          <Link
            to={`${dbBase}/checks/new`}
            className="ml-auto rounded-md bg-accent px-3 py-1.5 text-sm font-medium text-accent-foreground shadow-sm transition-colors hover:bg-accent-hover"
          >
            New check
          </Link>
        ) : inProject ? (
          <Link
            to={`${base}/databases/new`}
            className="ml-auto rounded-md bg-accent px-3 py-1.5 text-sm font-medium text-accent-foreground shadow-sm transition-colors hover:bg-accent-hover"
          >
            Add database
          </Link>
        ) : (
          <Link
            to="/projects/new"
            className="ml-auto rounded-md bg-accent px-3 py-1.5 text-sm font-medium text-accent-foreground shadow-sm transition-colors hover:bg-accent-hover"
          >
            New project
          </Link>
        )}
      </div>
    </nav>
  );
}
