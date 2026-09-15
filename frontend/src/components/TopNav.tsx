import { Link, matchPath, useLocation } from "react-router-dom";

/**
 * Two modes, because the useful actions differ by altitude.
 *
 * At workspace level the nav offers the things that span projects (Projects,
 * Connectors). Inside a project it switches to that project's own sections and
 * the primary action becomes "New check" - which only makes sense once there is
 * a project to put the check in. Previously that button sat in the global nav
 * with no context for what it would belong to.
 */
export function TopNav() {
  const location = useLocation();
  // Read from the path rather than useParams: TopNav renders outside <Routes>,
  // so route params are not in scope here.
  const match = matchPath("/projects/:slug/*", location.pathname);
  const slug = match?.params.slug;
  // "new" is the create form, not a project.
  const inProject = Boolean(slug) && slug !== "new";
  const base = inProject ? `/projects/${slug}` : "";

  const links = inProject
    ? [
        { href: base, label: "Overview", exact: true },
        { href: `${base}/checks`, label: "Checks" },
        { href: `${base}/tickets`, label: "Tickets" },
        { href: `${base}/settings`, label: "Settings" },
      ]
    : [
        { href: "/", label: "Projects", exact: true },
        { href: "/connectors", label: "Connectors" },
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

        {inProject ? (
          <Link
            to={`${base}/checks/new`}
            className="ml-auto rounded-md bg-accent px-3 py-1.5 text-sm font-medium text-accent-foreground shadow-sm transition-colors hover:bg-accent-hover"
          >
            New check
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
