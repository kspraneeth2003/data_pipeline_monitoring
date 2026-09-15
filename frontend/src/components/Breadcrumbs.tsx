import { Link } from "react-router-dom";

export type Crumb = { label: string; to?: string };

/**
 * Trail back to the home page. Once a user is three levels deep - project ->
 * checks -> a single run - a lone "back" link leaves them guessing where they
 * are; the full trail keeps the hierarchy visible and every ancestor reachable
 * in one click.
 */
export function Breadcrumbs({ items }: { items: Crumb[] }) {
  return (
    <nav aria-label="Breadcrumb" className="mb-4">
      <ol className="flex flex-wrap items-center gap-1.5 text-sm text-zinc-500">
        {items.map((item, i) => {
          const isLast = i === items.length - 1;
          return (
            <li key={`${item.label}-${i}`} className="flex items-center gap-1.5">
              {item.to && !isLast ? (
                <Link to={item.to} className="transition-colors hover:text-accent">
                  {item.label}
                </Link>
              ) : (
                <span className={isLast ? "font-medium text-foreground" : undefined} aria-current={isLast ? "page" : undefined}>
                  {item.label}
                </span>
              )}
              {!isLast && <span className="text-zinc-300 dark:text-zinc-700">/</span>}
            </li>
          );
        })}
      </ol>
    </nav>
  );
}
