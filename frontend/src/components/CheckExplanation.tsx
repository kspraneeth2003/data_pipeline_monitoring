import type { CheckStatement } from "../lib/api";

/**
 * The two explanatory halves every check owes its reader: the logic behind it,
 * and the SQL it runs. (The third, the description, sits under the check's name
 * wherever it is shown.)
 *
 * One component rather than two renderings because review and detail must show
 * the same thing - a proposal approved on one presentation and later read on
 * another is exactly how a check nobody understands gets scheduled.
 */
export function CheckExplanation({
  rationale,
  statements,
  statementsError,
}: {
  rationale: string | null;
  statements: CheckStatement[];
  statementsError: string | null;
}) {
  return (
    <>
      <CheckLogic rationale={rationale} />
      <CheckSql statements={statements} statementsError={statementsError} />
    </>
  );
}

/**
 * Why the check is worth asserting. Split out from `CheckExplanation` so the
 * check detail page can put the SQL behind its own subtab while keeping the
 * logic in view; the ingestion review still shows both together, because a
 * proposal approved without its SQL is a check nobody read.
 */
export function CheckLogic({ rationale }: { rationale: string | null }) {
  return (
    <>
      <section className="mb-4 rounded-xl border border-border bg-surface p-4 shadow-sm">
        <h2 className="mb-2 text-sm font-semibold text-zinc-700 dark:text-zinc-300">Logic</h2>
        {rationale ? (
          <p className="whitespace-pre-line text-sm leading-relaxed text-zinc-600 dark:text-zinc-300">
            {rationale}
          </p>
        ) : (
          // Said out loud rather than rendered as an empty box. A check with no
          // stated reasoning is a gap in the record, and a blank space reads as
          // "nothing to say here" instead.
          <p className="text-sm italic text-zinc-500 dark:text-zinc-400">
            No logic recorded. Edit this check to say which invariant it relies on and what a
            failure would mean.
          </p>
        )}
      </section>
    </>
  );
}

/** The statements the check issues, exactly as the engine would run them. */
export function CheckSql({
  statements,
  statementsError,
}: {
  statements: CheckStatement[];
  statementsError: string | null;
}) {
  return (
    <>
      <section className="mb-4 rounded-xl border border-border bg-surface p-4 shadow-sm">
        <h2 className="mb-2 text-sm font-semibold text-zinc-700 dark:text-zinc-300">SQL</h2>
        {statementsError ? (
          <p className="text-sm text-red-600 dark:text-red-400">
            This check's configuration cannot produce a statement, so it would error on its next
            run: {statementsError}
          </p>
        ) : (
          <div className="space-y-3">
            {statements.map((statement, index) => (
              <div key={index}>
                <div className="mb-1 flex items-baseline gap-2">
                  <span className="text-xs text-zinc-500 dark:text-zinc-400">{statement.label}</span>
                  {statement.connection === "secondary" && (
                    <span className="rounded bg-zinc-100 px-1.5 py-0.5 text-[10px] uppercase tracking-wide text-zinc-600 dark:bg-white/10 dark:text-zinc-300">
                      secondary connection
                    </span>
                  )}
                </div>
                <pre className="overflow-x-auto rounded-lg bg-zinc-50 p-3 text-xs leading-relaxed text-zinc-700 dark:bg-black/30 dark:text-zinc-300">
                  {statement.sql}
                </pre>
              </div>
            ))}
          </div>
        )}
      </section>
    </>
  );
}
