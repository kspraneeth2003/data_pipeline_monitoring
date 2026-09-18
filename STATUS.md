# Project Status / Handoff Notes

Read this first if you're picking this up in a new session. High-level requirements/plan/milestones live in `PLAN.md` - this file is "what's actually been built so far and how to pick it back up," updated as of **2026-09-17**.

## TL;DR

The app was **fully rewritten** from Next.js/Prisma/TypeScript to **Vite+React (frontend) / FastAPI+SQLAlchemy (backend, Python) / LangGraph+LangChain (RCA agent)**, at the user's explicit request. The old Next.js app has been deleted; git history still has it if ever needed (last commit before deletion: `c8c732e`, plus everything built in this session on top of the working tree).

Functional parity with the previous Next.js app is verified working end-to-end:
- **M0/M1**: Check engine + dashboard
- **M2**: RCA agent, now built as a **LangGraph** `StateGraph` (gather evidence -> try LLM synthesis -> fall back to heuristic on failure), using a custom LangChain chat model that shells out to the Claude Code CLI - still no `ANTHROPIC_API_KEY` needed
- **M3**: Ticketing, simulated as an in-app Kanban board
- Account-agnostic Snowflake connectors (encrypted credentials, connect any account via the UI)
- **Repository ingestion**: a project is now started by pasting the git URL of the repo that defines
  the pipeline. The DDL is parsed, checks are derived from it, an agent adds the ones rules cannot
  derive, and the user reviews before anything is written. See "Starting a project from a repository".
- The visual design system (accent color, tokens, cards, badges) was ported over into the new frontend

**Not started**: M4 (RDS/cross-platform connector spike), M5 (hardening).

## Architecture

```
backend/                  FastAPI + SQLAlchemy + Alembic (Python, managed with `uv`)
  app/
    main.py               FastAPI app, CORS, router registration, scheduler lifecycle
    config.py             Settings (pydantic-settings, reads backend/.env)
    db.py                 SQLAlchemy engine/session
    models.py             ORM models: Project (+ repo_url/ref/commit/path), Database (+ repo_paths),
                            Connector (project-owned), Check, CheckRun, RcaResult, Ticket
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
    ingest/               THE OTHER AGENTIC PART - repository -> project (see below)
      repo.py             Clones/fetches a repo URL. Allowlists http(s), rejects `-`-leading URLs,
                            passes an access token for exactly one command then rewrites the remote
                            so it never lands in .git/config
      ddl_parser.py       Lexical (not grammar-based) parse of .sql: schemas, tables, columns,
                            MERGE source/target/key/value mappings, stream->table, task cadence
      heuristic.py        Rule-derived check proposals; also flags payload-key/column-name
                            mismatches, which is where DDL-derived parity checks have a blind spot
      graph.py            LangGraph: fetch -> parse -> heuristic -> llm_enrich. The LLM only ever
                            improves a result the heuristic already produced
      jobs.py             In-memory background job registry (analysis takes ~a minute)
    rca/                  THE AGENTIC PART - see below
    tickets/
      mock_ticket.py       Files a mock ticket (simulated Jira) from an RCA result
    scheduler.py           APScheduler background scheduler, resyncs cron jobs from the DB every 30s
    routers/               projects.py, checks.py, connectors.py, tickets.py, ingest.py - FastAPI routers
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
    pages/                  Projects (home), NewProject (repo-driven setup wizard),
                             NewProjectManual (the old name/connect/databases wizard), ProjectOverview,
                             ProjectTickets, ProjectConnections, ProjectSettings, NewDatabase,
                             DatabaseChecks, DatabaseSettings, CheckDetail, NewCheck, EditCheck
    App.tsx                 React Router routes
    index.css               Design tokens (same accent/surface/border scheme as before)

snowflake/                Tracked DDL for the bronze/silver/gold test pipeline. This is what the RCA
                          agent's git-history gatherer reads for the seeded projects, and it doubles
                          as the reference input for the repo parser
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

### Starting a project from a repository (`backend/app/ingest/`)

The default setup flow is **repository -> review -> connect** (`frontend/src/pages/NewProject.tsx`).
The old manual wizard still exists at `/projects/new/manual` for warehouses whose pipeline is not
defined as code anywhere.

    POST /api/ingest                   start an analysis -> job id
    GET  /api/ingest/{job_id}          poll for progress and the proposal
    POST /api/ingest/{job_id}/project  confirm it into a real project

Credentials arrive only at the third call. A repository describes the pipeline's structure, which is
knowable without touching Snowflake, so asking for a password before the user can see what was found
would be a gate with nothing behind it. That call is also the only one that writes, and it tests the
connection first, so a project never exists half-configured.

The graph is `fetch -> parse -> heuristic -> llm_enrich`. The ordering is the design: the heuristic
runs first and always, so a missing/slow/garbled LLM costs proposal *quality*, never the flow. Every
LLM-proposed check is validated against the same pydantic schema the API enforces and dropped with a
warning if it fails - accepting it would mean a check that errors on first run, which reads as a
broken pipeline rather than a bad proposal.

What gets derived, and from what:

| Proposal | Derived from |
|---|---|
| `BRONZE_TO_SILVER_PARITY` | the MERGE: source, target, ON clause -> keys, SELECT aliases -> value mapping |
| `FRESHNESS` | a timestamp column + the writing task's `SCHEDULE` (x6 slack, min 15 min) |
| `SCHEMA_DRIFT` | the CREATE TABLE column contract (skipped for VARIANT landing tables) |
| `ROW_COUNT` | an *unfiltered* MERGE only - a filtered one is meant to shrink, so parity would fail forever |
| `NULL_RATE` | nothing in the DDL justifies one, so these come from the agent |

**The known blind spot, worth understanding before trusting output.** A check derived from a MERGE
asserts *that the MERGE did what the MERGE says* - not that silver matches the source data. The
inventory MERGE reads `RAW_PAYLOAD:warehouse` into `WAREHOUSE_ID` when the landed key is
`warehouse_id`; the generated check encodes the same mistake and **passes** (verified against live
Snowflake: 1730 keys 1:1, "5 value column(s) agree"). The hand-written seed check on the same tables
fails, because a human wrote the intent rather than the implementation.

Deriving from the column name instead would be worse - a legitimate rename is indistinguishable from
a bug, so every renamed column would fail forever. So `heuristic._mapping_concerns` *flags* the
name mismatch on the check and in the analysis warnings, and leaves the judgement to the reviewer.

### Connecting GitHub (optional)

Setup offers two ways to name a repository: pick one from GitHub, or paste a URL.
The second always works (GitLab, Bitbucket, self-hosted). The first needs a GitHub App
registered once, and everything degrades cleanly without it - `/api/github/status` returns
`configured: false` and the UI falls back to paste-a-URL on its own.

A **GitHub App**, not "Sign in with GitHub", and the difference is the point: signing in
grants access to everything the user can see, whereas an App is installed *onto selected
repositories*. GitHub asks "grant read access to these?" and then enforces it - the app
cannot reach a repository it was not given.

To register it (free; no Marketplace listing needed):

1. GitHub -> Settings -> Developer settings -> GitHub Apps -> **New GitHub App**
2. **Homepage URL** `http://localhost:5173`, **Callback URL**
   `http://localhost:8000/api/github/callback`
3. **Uncheck Webhook -> Active.** Webhooks are only needed for push-driven re-analysis,
   which is not built; leaving it on means GitHub tries to reach a URL that does not exist.
4. Permissions: **Repository -> Contents -> Read-only**. Nothing else.
5. Create, note the **App ID**, then **Generate a private key** and save the `.pem`.
6. In `backend/.env`:

   ```
   GITHUB_APP_ID=123456
   GITHUB_APP_SLUG=your-app-name-as-in-its-url
   GITHUB_APP_PRIVATE_KEY_PATH=C:/path/to/your-app.private-key.pem
   ```

   `GITHUB_APP_SLUG` is the last path segment of the app's public page, which is what
   builds the install link. The `.pem` is a secret on the same footing as
   `CONNECTOR_SECRET_KEY` - never commit it.
7. Restart the backend. "From GitHub" appears in setup; the first click goes to GitHub to
   choose repositories.

Implementation notes that matter if you change this: the installation token is minted in
`graph._fetch` at clone time and never returned to the browser; installation state is
queried live rather than stored, so uninstalling on GitHub takes effect immediately; and
`/status` is written never to raise, because a broken GitHub must narrow the options
rather than break the setup screen.

### The object -> repo file map is per-project

`rca/object_repo_map.py` used to be a hardcoded dict of this repo's own `snowflake/` paths, so RCA
could only attribute failures in the pipeline the app ships with. It is now data:

- `Database.repo_paths` - `{"BRONZE": "snowflake/dpm_src_crm/bronze.sql"}`, keyed by schema (not by
  table, which would go stale the first time someone adds a table without re-ingesting)
- `Project.repo_url` / `repo_ref` / `repo_commit` / `repo_path`

`RepoContext` is built from the whole project, not the check's own database, because a check can
reference sibling databases. A project with no repo gets an empty context and RCA skips git evidence
rather than guessing at a checkout - the wrong repo's history is worse evidence than none.

Migration `7ea816a49699` backfills `repo_paths` from the old hardcoded map, so pre-existing projects
keep their attribution. `projects.repo_path` is left null on purpose (a checkout path baked into a
migration is wrong on every other machine); `git_context` falls back to the app's own root when null.

## What's running right now

- Local Postgres (Homebrew, `postgresql@16`) - database `dpm_dev`, same one used all along. **Schema was reset** when switching from Prisma to SQLAlchemy (table structure/column naming changed from camelCase to snake_case) - this destroyed the old app's data (a handful of test checks/runs), which is fine since it's all disposable local dev data and re-seedable.
- Backend: `cd backend && uv run uvicorn app.main:app --reload --port 8000` (also runs the APScheduler cron scheduler in-process - no separate scheduler process needed anymore, unlike the old Node setup)
- Frontend: `cd frontend && npm run dev` (Vite, port 5173)

Check `ps aux | grep -E "uvicorn|vite"` and restart if needed (on Windows: `netstat -ano | findstr ":8000 :5173"`).

Ingested repositories are checked out under `backend/.repo-cache/` (gitignored, re-clonable).

## Git status

Work happens on the `rishi-raj` branch; `main` is never committed to directly. The Python/Vite stack and the project -> database -> check hierarchy are committed. Repository ingestion landed in six commits on top of `f0687e3`, ending at "Make repository ingestion the default way to start a project".

Nothing has been pushed - publishing is the user's call per `CLAUDE.md`.

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

`backend/.env` (gitignored) needs: `DATABASE_URL`, `CONNECTOR_SECRET_KEY` (generate your own - see comment in the file), legacy `SNOWFLAKE_*` vars (only used by the seeded `snowflake-default` connector), `RCA_LLM_COMMAND` (defaults to `claude`). Optionally `GITHUB_APP_ID`/`GITHUB_APP_SLUG`/`GITHUB_APP_PRIVATE_KEY_PATH` for the GitHub picker - see "Connecting GitHub". `ANTHROPIC_API_KEY`/`JIRA_*`/`GITHUB_TOKEN` are unused/stubbed.

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
- Repo ingestion jobs live in memory, so `uvicorn --reload` drops an in-flight analysis; re-running is cheap because the git checkout is cached. It also assumes one process, as APScheduler already does
- `ddl_parser` is lexical, not a real SQL grammar. It handles the Snowflake DDL shapes in `snowflake/` and the `GET_DDL` output in `ioi/`; a repo using dbt models, Jinja templating or `CREATE VIEW`-only definitions will parse to little or nothing (which surfaces as an explicit warning, not a silent empty success)
- Access tokens for private repos are used for the fetch and discarded - there is no stored credential, so re-ingesting a private repo later means re-entering the token
- `.mcp.json` still points the `snowflake` MCP server at `/Users/kotam/Documents/dpm/.mcp/`, a path from a different machine. Nothing in the app depends on it (connectors read credentials from Postgres), but the MCP server will not start as configured

## What's next (per PLAN.md)

- **M4**: implement a Postgres/RDS connector against the existing `Connector` protocol, add one cross-source parity check, prove the check engine needed zero changes
- **M5**: secrets management, retries/observability on external calls, RBAC, load testing, docs
