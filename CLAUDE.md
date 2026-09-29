# Working agreement for this repo

DPM is a data integrity/parity check engine for Snowflake. Read `README.md` for
the stack, `PLAN.md` for requirements and milestones, and `STATUS.md` for the
current state, architecture map and local setup. This file is about *how to
work* here, not what the code does.

## Version control

### Commit as you go, not at the end

Commit each time a piece of work stands on its own — a check type added, a bug
fixed, a migration written, a page rebuilt. Do not batch a session's work into
one commit at the end, and do not leave finished work sitting uncommitted
waiting to be asked. A session that ends with a large uncommitted working tree
has lost information: the reasoning that went with each step is gone.

The bar for "stands on its own" is: the tree builds, the thing you changed
works, and the commit message can name one change. If you find yourself writing
"and also" in the subject line, it is two commits.

Before committing, read your own diff (`git diff --staged`) and stage
deliberately. Never `git add -A` without looking — `.env`, `.mcp/`, and
`backend/.venv` are gitignored for a reason and a new secret-bearing file will
not be.

### Branches

`main` is the default branch and tracks `origin/main`. Work happens on a
feature branch — the current one is `rishi-raj`. Never commit directly to
`main`; if you are on it, branch first.

Name a branch after the work, not the person or the day: `b2s-parity-lag`,
`database-level-nav`. Keep a branch to one line of work so it stays reviewable.

Push and open PRs only when asked. Committing locally is the routine part;
publishing is the user's call.

### Commit messages

The existing history is the spec — match it. Look at `git log` before writing
one.

**Subject**: imperative mood, sentence case, no type prefix or scope, no
trailing period, under ~72 characters. "Apply the B2S settling lag to both
sides", not "fix(checks): fixed lag bug."

**Body**: required for anything but a trivial change. Wrap at 72 columns.
Explain *why the change was needed and what it means*, not a restatement of the
diff — the diff is already in the commit. The pattern this repo follows:

1. What was wrong or missing, concretely.
2. What the change does, and the reasoning behind the design choice —
   especially any alternative that was rejected and why.
3. Evidence it works: what you ran, what the numbers were, what still fails on
   purpose.

Say the consequence out loud. "Filtering one side only trades false 'missing'
alerts for false 'extra' ones, and a check that cries wolf gets muted" is worth
more to the next reader than any description of the SQL.

Use a bullet list in the body when a change genuinely has several independent
parts. Call out migrations, dropped columns, changed URLs, and anything a
teammate must do after pulling.

End every commit message with:

```
Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
```

Never use `--no-verify`, never skip signing, and prefer a new commit over
amending one that already exists.

## Keeping the written record current

`STATUS.md` is the handoff document — it is what a new session reads first.
When you change the architecture, add a route, change setup steps, or close out
a known rough edge, update `STATUS.md` in the same commit as the change. A
stale handoff file is worse than none, because it is trusted.

`PLAN.md` holds requirements and milestones; update it when scope moves, not
when code moves.

## Safety rules that override convenience

- **Never run destructive database commands without asking** — `alembic
  downgrade`, dropping or truncating tables, deleting rows. This holds even for
  `dpm_dev`, even when "it's just local dev data." Claude Code's own classifier
  catches some destructive CLI calls but is not guaranteed to catch the
  equivalent SQL or Python, so apply the caution manually.
- **Never write against Snowflake.** The connectors are read-only in spirit;
  the `snowflake/` DDL is the only place pipeline structure changes, and those
  are reviewed as code.
- **Secrets never reach the repo or a response.** Connector credentials go
  through `connectors/security.py` (`encrypt_config_secrets` /
  `redact_config_secrets`) on every write and read. Do not print a decrypted
  config, do not paste `.env` contents into a commit, a comment, or chat.
- `CONNECTOR_SECRET_KEY` in `backend/.env` is a single symmetric key, not a
  secrets manager. Treat anything encrypted with it as dev-grade.

## Before you call something done

- **Frontend**: `npm run build` (runs `tsc -b`) and `npm run lint` (oxlint) must
  both pass. A type error is not a warning here.
- **Backend**: there is no test suite yet, so verification is manual and must be
  *stated*, not assumed — run the check, hit the endpoint with curl, show the
  numbers. If you add a behaviour worth protecting, adding a test is better than
  describing how you tested it by hand.
- **Migrations**: any `models.py` change needs an Alembic revision, and the
  revision must be run (`uv run alembic upgrade head`) before you claim it
  works. Prefer a migration that *derives* data from what is already there over
  one that guesses defaults.
- Report honestly. If something still fails, say so with the output. "Verified:
  CRM now passes (372 keys, 1:1, 0 ahead), inventory still fails on the real
  WAREHOUSE_ID mis-mapping" is the standard.

## Code conventions

- **Python**: Python 3.12, managed by `uv`. Run everything through `uv run`.
  Type-annotate function signatures; snake_case throughout, including DB
  columns.
- **TypeScript/React**: functional components, typed props, no `any`. All API
  access goes through `src/lib/api.ts` — do not scatter `fetch` calls.
- **Timestamps**: the API returns naive UTC. Always parse with `src/lib/time.ts`;
  a bare `new Date(iso)` reads them as local time and silently shifts every
  timestamp in the UI.
- **Styling**: use the design tokens in `src/index.css` (`--background`,
  `--surface`, `--border`, `--accent`, `--accent-hover`, `--accent-foreground`,
  `--accent-soft`), never raw `zinc-*`/`white`/`black`.
- **Check types**: `backend/app/checks/config_schemas.py` and
  `frontend/src/lib/check-types.ts` are two halves of one contract. Change them
  together, in the same commit.
- **New data sources**: add them to `connectors/registry.py` behind the existing
  `Connector` protocol. If the check engine needs changing to support a new
  source, the abstraction is wrong — fix that instead.
- **Non-obvious logic gets a module docstring explaining the *why*.**
  `b2s_parity.py` explains why it is a FULL OUTER JOIN and not `EXCEPT`. That is
  the standard for anything a reader could reasonably "simplify" into a bug.

## Health and hierarchy invariants

Do not break these without an explicit decision:

- The tree is **project → database → check**. A project is a data product, not a
  database. A check is anchored to exactly one database and reaches its project
  through that database — there is no direct check → project link, because two
  paths to the same fact can disagree.
- Health takes the **worst** state at every level, never an average. One failing
  check makes its database and its project read as failing.
- RCA must never block ticket creation. The LangGraph graph falls back from LLM
  synthesis to the deterministic heuristic on any failure; keep that edge intact.
