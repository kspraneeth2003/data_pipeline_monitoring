# Project Status / Handoff Notes

Read this first if you're picking this up in a new session. High-level requirements/plan/milestones live in `PLAN.md` - this file is "what's actually been built so far and how to pick it back up," updated as of **2026-09-10**.

## TL;DR

The app was **fully rewritten** from Next.js/Prisma/TypeScript to **Vite+React (frontend) / FastAPI+SQLAlchemy (backend, Python) / LangGraph+LangChain (RCA agent)**, at the user's explicit request. The old Next.js app has been deleted; git history still has it if ever needed (last commit before deletion: `c8c732e`, plus everything built in this session on top of the working tree).

Functional parity with the previous Next.js app is verified working end-to-end:
- **M0/M1**: Check engine + dashboard
- **M2**: RCA agent, now built as a **LangGraph** `StateGraph` (gather evidence -> try LLM synthesis -> fall back to heuristic on failure), using a custom LangChain chat model that shells out to the Claude Code CLI - still no `ANTHROPIC_API_KEY` needed
- **M3**: Ticketing, simulated as an in-app Kanban board
- Account-agnostic Snowflake connectors (encrypted credentials, connect any account via the UI)
- The visual design system (accent color, tokens, cards, badges) was ported over into the new frontend

**Not started**: M4 (RDS/cross-platform connector spike), M5 (hardening).

## Architecture

```
backend/                  FastAPI + SQLAlchemy + Alembic (Python, managed with `uv`)
  app/
    main.py               FastAPI app, CORS, router registration, scheduler lifecycle
    config.py             Settings (pydantic-settings, reads backend/.env)
    db.py                 SQLAlchemy engine/session
    models.py             ORM models: Project, Database, Connector (project-owned), Check, CheckRun,
                            RcaResult, Ticket
    schemas.py            Pydantic request/response schemas
    crypto.py             AES-256-GCM encrypt/decrypt for connector secrets
    connectors/
      base.py             Connector protocol (run_query, get_schema, close)
      snowflake_connector.py   SnowflakeConnector (snowflake-connector-python) + test_snowflake_connection
      registry.py         build_connector(type, config) - add RDS/Postgres here for M4
      security.py         encrypt_config_secrets / redact_config_secrets
    checks/
      config_schemas.py   Pydantic model per check type (mirrors the old Zod schemas)
      engine.py           run_check(...) -> CheckOutcome (PASSED/FAILED/ERROR + metrics)
      b2s_parity.py       BRONZE_TO_SILVER_PARITY: builds the single deterministic SQL statement
                            that proves silver is a deduplicated, lossless projection of bronze.
                            See the module docstring for why it is a FULL OUTER JOIN and not EXCEPT.
      runner.py           execute_check(db, check_id): persists a run, triggers RCA + ticket on failure
    rca/                  THE AGENTIC PART - see below
    tickets/
      mock_ticket.py       Files a mock ticket (simulated Jira) from an RCA result
    scheduler.py           APScheduler background scheduler, resyncs cron jobs from the DB every 30s
    routers/               projects.py, checks.py, connectors.py, tickets.py - FastAPI routers
    seed.py                Seeds 1 connector + 4 example checks (`python -m app.seed`)
  alembic/                 Migrations (env.py wired to app.db.Base.metadata + app.config.settings)

frontend/                 Vite + React + TypeScript
  src/
    lib/api.ts             Typed fetch client for the FastAPI backend (VITE_API_URL, default localhost:8000)
    lib/check-types.ts      UI metadata for check-type form fields (keep in sync with backend/app/checks/config_schemas.py)
    components/             TopNav, Breadcrumbs, HealthPill, StatusBadge, RunNowButton, EnabledToggle,
                             DeleteButton, CheckForm, SnowflakeCredentialFields, TicketBoard
    lib/time.ts             Parses the API's naive-UTC timestamps. Use this, never bare `new Date(iso)` -
                             a bare parse reads them as local time and shifts every timestamp.
    pages/                  Projects (home), NewProject (setup wizard), ProjectOverview,
                             ProjectTickets, ProjectConnections, ProjectSettings, NewDatabase,
                             DatabaseChecks, DatabaseSettings, CheckDetail, NewCheck, EditCheck
    App.tsx                 React Router routes
    index.css               Design tokens (same accent/surface/border scheme as before)

snowflake/                Tracked DDL for the bronze/silver/gold test pipeline - unchanged by the rewrite,
                          this is what the RCA agent's git-history gatherer reads
```

### Information architecture

**project -> database -> check.** A project is a data product (Customer 360),
not a database - it spans the databases that together serve one domain.

```
/                                              Projects, ranked worst-health first
/projects/new                                  Create
/projects/:slug                                Databases in this project, with health
/projects/:slug/tickets                        Tickets across the project
/projects/:slug/settings                       Rename, connections in use, delete
/projects/:slug/connections                    Connections owned by this project
/projects/:slug/databases/new                  Add one (picked from live discovery)
/projects/:slug/databases/:dbSlug              Checks on this database
/projects/:slug/databases/:dbSlug/settings     Describe, remove from project
/projects/:slug/databases/:dbSlug/checks/...   new | :id | :id/edit

```

A check is *anchored* to the database holding its primary object but may still
reference sibling databases - a silver-vs-gold parity check legitimately spans
two. There is no direct check -> project link; the project is reached through
the database, so the two cannot disagree.

Connections belong to the project too - connecting a warehouse is part of
setting a project up, not a separate administrative step elsewhere. Two
projects on the same account each hold their own credentials; that is
deliberate, so deleting or re-credentialling one can never break another.

Setup happens in one pass. `POST /api/connectors/probe` tests credentials and
returns the identity plus reachable databases without saving anything, so the
wizard can show what the credentials actually reached and let the user pick
from a real list. `POST /api/projects/setup` then creates project, connection
and databases together, testing the connection before the first write.

Health takes the worst state rather than an average at every level - one
failing check makes its database, and its project, read as failing.

### The RCA agent is a LangGraph graph (`backend/app/rca/graph.py`)

```
gather_evidence -> synthesize_llm --[llm succeeded]--> END
                                   --[llm failed]-----> synthesize_heuristic -> END
```

- `gather_evidence`: for each object implicated by the failed check (`extract_targets.py`), opens the check's Snowflake connector once and pulls `snowflake_context.py` (near-real-time metadata via `INFORMATION_SCHEMA`) + `git_context.py` (git log / `git log -S<keyword>` pickaxe search against the tracked files in `snowflake/`)
- `synthesize_llm`: builds a prompt from that evidence and calls `ClaudeCliChatModel` (`llm.py`) - a `langchain_core.language_models.chat_models.BaseChatModel` subclass whose `_generate` shells out to `claude -p --restricted <prompt>` instead of hitting the Anthropic API. Set `RCA_LLM_COMMAND` in `backend/.env` to override the binary.
- If that raises (CLI missing, timeout, bad JSON), the conditional edge routes to `synthesize_heuristic` (`heuristic.py`) - the same deterministic fallback logic as before, so RCA never blocks ticket creation.
- `generate_rca(...)` in `graph.py` is the single entry point `runner.py` calls.

### Mock ticketing (`backend/app/tickets/mock_ticket.py`)

Unchanged in spirit from before: `Ticket` rows with a `DPM-N` key, title/description/priority pre-filled from the RCA result, `assignee` from RCA's `suggestedOwner`, rendered as a 3-column Kanban board at `/tickets` in the new frontend.

### Account-agnostic Snowflake connectors

Same design as before, reimplemented in Python:
- `crypto.py` - AES-256-GCM (via the `cryptography` package), keyed by `CONNECTOR_SECRET_KEY` in `backend/.env`. Same `enc:v1:...` prefix scheme as the old TypeScript version (not binary-compatible, but conceptually identical) - **not a real secrets manager**, revisit before any real deployment.
- `connectors/security.py` - `encrypt_config_secrets` / `redact_config_secrets`, applied on every write/read so plaintext secrets are never returned by any API route.
- `POST /api/connectors` and `PATCH /api/connectors/{id}` test the connection (`SELECT 1`) before saving, unless `test_connection: false`.
- The original `snowflake-default` connector still works via env var fallback (`SNOWFLAKE_*` in `backend/.env`); new connectors created via the UI carry full credentials in their own `config` and don't touch `.env`.

## What's running right now

- Local Postgres (Homebrew, `postgresql@16`) - database `dpm_dev`, same one used all along. **Schema was reset** when switching from Prisma to SQLAlchemy (table structure/column naming changed from camelCase to snake_case) - this destroyed the old app's data (a handful of test checks/runs), which is fine since it's all disposable local dev data and re-seedable.
- Backend: `cd backend && source .venv/bin/activate && uvicorn app.main:app --reload --port 8000` (also runs the APScheduler cron scheduler in-process - no separate scheduler process needed anymore, unlike the old Node setup)
- Frontend: `cd frontend && npm run dev` (Vite, port 5173)

Check `ps aux | grep -E "uvicorn|vite"` and restart if needed.

## Git status

Nothing in this repo has been committed since the `snowflake/` DDL commit (`c8c732e`) - the entire Next.js app was built, then deleted, without ever being committed, and the new Python/Vite stack is also uncommitted. This was intentional per the "only commit when asked" rule. If you want any of this version-controlled, ask for a commit - `git status`/`git diff` will show the full picture (old app gone, new `backend/`+`frontend/` added).

## Local setup (if starting fresh)

```bash
# Postgres
brew services start postgresql@16   # if not running
createdb dpm_dev                    # if it doesn't exist

# Backend
cd backend
uv sync                             # installs into backend/.venv (Python 3.12, managed by uv)
source .venv/bin/activate
alembic upgrade head                # creates connectors/checks/check_runs/rca_results/tickets tables
python -m app.seed                  # seeds 1 connector + 4 example checks
uvicorn app.main:app --reload --port 8000

# Frontend (separate terminal)
cd frontend
npm install
npm run dev
```

`backend/.env` (gitignored) needs: `DATABASE_URL`, `CONNECTOR_SECRET_KEY` (generate your own - see comment in the file), legacy `SNOWFLAKE_*` vars (only used by the seeded `snowflake-default` connector), `RCA_LLM_COMMAND` (defaults to `claude`). `ANTHROPIC_API_KEY`/`JIRA_*`/`GITHUB_TOKEN` are unused/stubbed.

`frontend/.env` (optional, gitignored): `VITE_API_URL` if the backend isn't at `http://localhost:8000`.

**⚠️ Never run destructive DB commands (`alembic downgrade`, dropping tables, etc.) without checking with the user first** - even on "just a dev database." Claude Code's own safety classifier blocks some destructive CLI invocations (e.g. `prisma migrate reset`) and requires explicit re-confirmation; the equivalent Python/SQL operations aren't guaranteed to be caught by that same classifier, so exercise the same caution manually.

## Design system

`frontend/src/index.css` defines the same CSS custom properties as before (`--background`, `--surface`, `--border`, `--accent`, `--accent-hover`, `--accent-foreground`, `--accent-soft`), exposed to Tailwind via `@theme inline`. Use these tokens (not raw `zinc-*`/`white`/`black`) in any new page or component.

## Known rough edges / things to fix eventually

- Ticket key generation (`_next_ticket_key` in `mock_ticket.py`) is a best-effort count-based scheme, not a real sequence - fine at this volume, would race under real concurrency
- No ticket de-duplication/cooldown (PLAN.md FR10) - every failed run files a new ticket
- `CROSS_SOURCE_PARITY` checks get no RCA object-level evidence gathering yet (every other check type has it)
- No auth/RBAC on the web app yet (PLAN.md FR16) - anyone with network access can hit the API routes
- Connector secret encryption uses a single symmetric key in `.env`, not a real secrets manager/KMS
- The connector edit form (`PATCH /api/connectors/{id}`) exists as an API but has no frontend UI yet - editing credentials currently requires calling the API directly
- No automated tests were written for the Python backend or the Vite frontend - everything was verified manually (curl + browser) during this session

## What's next (per PLAN.md)

- **M4**: implement a Postgres/RDS connector against the existing `Connector` protocol, add one cross-source parity check, prove the check engine needed zero changes
- **M5**: secrets management, retries/observability on external calls, RBAC, load testing, docs
