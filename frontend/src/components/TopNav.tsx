import { Link, useLocation } from "react-router-dom";

const LINKS = [
  { href: "/", label: "Checks" },
  { href: "/connectors", label: "Connectors" },
  { href: "/tickets", label: "Tickets" },
];

export function TopNav() {
  const location = useLocation();

  return (
    <nav className="sticky top-0 z-10 border-b border-border bg-surface/80 backdrop-blur">
      <div className="mx-auto flex max-w-5xl items-center gap-1 px-6 py-3 sm:px-10">
        <Link to="/" className="mr-4 flex items-center gap-2">
          <span className="flex h-7 w-7 items-center justify-center rounded-md bg-accent text-sm font-bold text-accent-foreground">
            D
          </span>
          <span className="font-semibold text-foreground">DPM</span>
        </Link>

        {LINKS.map((link) => {
          const isActive = link.href === "/" ? location.pathname === "/" : location.pathname.startsWith(link.href);
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

        <Link
          to="/checks/new"
          className="ml-auto rounded-md bg-accent px-3 py-1.5 text-sm font-medium text-accent-foreground shadow-sm transition-colors hover:bg-accent-hover"
        >
          New check
        </Link>
      </div>
    </nav>
  );
}
