---
name: setup-and-run
description: Set up and run DPM locally on a machine that has just cloned this repo - installs dependencies, writes backend/.env, creates and migrates the dpm_dev database, seeds it, starts the backend and frontend, and verifies both. Use when someone says they just cloned/pulled this repo, asks how to run it, asks to start or restart the app, or hits a setup error (missing .env, CONNECTOR_SECRET_KEY, alembic, port already in use).
---

# Set up and run DPM locally

Goal: a working local DPM on a machine that has only just cloned this repo -
backend on `http://localhost:8000`, frontend on `http://localhost:5173`.

Work through the phases in order. Each phase says what to check before doing
anything, because most of this is idempotent and a returning user usually only
needs Phase 5.

**Never commit anything created here.** `backend/.env` and `frontend/.env` hold
secrets and are gitignored. Do not print the contents of either file back to the
user, and do not paste a generated `CONNECTOR_SECRET_KEY` into chat.

## Phase 1 - Prerequisites

Check all of these in one pass and report what is missing as a single list. Do
not install anything system-wide yourself; tell the user what to install and
stop until they have it.

| Tool | Check | If missing |
|---|---|---|
| Python 3.12 | `python --version` | Needed by `backend/.python-version`; uv can also fetch it |
| uv | `uv --version` | https://docs.astral.sh/uv/ - `winget install astral-sh.uv` / `brew install uv` |
| Node 20+ | `node --version` | https://nodejs.org |
| Postgres 16 | `psql --version` **and** that the server answers | Installer on Windows; `brew install postgresql@16 && brew services start postgresql@16` on macOS |
| Claude Code CLI | `claude --version` | Optional. RCA and the agents fall back to deterministic rules without it - the app is fully usable |

Postgres being *installed* is not the same as *running*. Confirm the server
answers before moving on: `psql -l` (Windows may need `-U postgres`).

## Phase 2 - `backend/.env`

Skip this phase entirely if `backend/.env` already exists. Never overwrite it.

1. Copy `backend/.env.example` to `backend/.env`. That file documents every
   variable - read it rather than inventing keys.
2. Set the two required values:
   - `DATABASE_URL=postgresql+psycopg://<pg-user>@localhost:5432/dpm_dev`
     Ask the user for their Postgres username and password if they have one; do
     not assume `postgres` works passwordless. The config default in
     `app/config.py` names another machine's user and will fail here.
   - `CONNECTOR_SECRET_KEY` - generate a fresh one on this machine:
     `python -c "import secrets; print(secrets.token_urlsafe(32))"`
     Write it straight into the file. Do not echo it. It is not shared between
     machines: it encrypts connector credentials at rest, so each install has
     its own and each install re-enters its own Snowflake credentials.
3. Leave everything else empty unless the user asks. Every other variable
   degrades cleanly and the app is designed to run without them:
   - `AGENT_MODEL` empty - checks, incidents and escalation all still run, the
     rules just work without judgement. This is a supported mode, not degraded.
   - `JIRA_*` empty - incidents open and close in the app with no issue attached.
   - `SNOWFLAKE_*` empty - only the seeded `snowflake-default` connector uses
     them; connectors made in the UI carry their own credentials.
   - `GITHUB_APP_*` empty - setup offers paste-a-URL instead of a repo picker.
     The private key it wants is a `.pem` that lives outside this repo; if the
     user does not have one, they do not need this.

Do **not** create `frontend/.env`. The frontend defaults to
`http://localhost:8000`, which is correct. It only exists to point at a backend
on a different port.

## Phase 3 - Database

```bash
createdb dpm_dev          # already exists -> the error is harmless, move on
```

On Windows `createdb` may not be on PATH; `psql -c "CREATE DATABASE dpm_dev;"`
does the same thing.

Then, from `backend/`:

```bash
uv sync                          # creates backend/.venv, Python 3.12
uv run alembic upgrade head      # creates every table
uv run python -m app.seed        # 2 projects, their databases, example checks
```

`app.seed` is safe to re-run and deliberately will not clobber connector
credentials entered through the UI.

**Never run `alembic downgrade`, drop or truncate a table, or delete rows
without asking the user first** - this holds even for `dpm_dev`.

## Phase 4 - Verify before starting servers

From `backend/`: `uv run pytest` - 155 tests, all should pass. A failure here is
a real problem with the checkout, not with the user's setup; report the output
rather than working around it.

## Phase 5 - Run

Two long-running processes. Start each in the background and keep the handles
so you can report their logs.

```bash
# Backend (from backend/) - also runs the APScheduler cron in-process
uv run uvicorn app.main:app --reload --port 8000

# Frontend (from frontend/, separate process)
npm install      # first time only
npm run dev
```

Then verify, and say what you saw:

- `curl http://localhost:8000/api/health` returns OK
- `http://localhost:5173` loads and the projects page lists the seeded projects

Give the user the URL. Do not claim it works without having hit both.

## Troubleshooting

**Port already in use.** Find it - `netstat -ano | findstr ":8000"` (Windows) or
`lsof -i :8000` (macOS) - and ask before killing anything. If Windows has left
orphaned sockets that a new server cannot bind past, run the backend on 8001 and
add `VITE_API_URL=http://localhost:8001` to `frontend/.env`. That is a
workaround for one machine, not a setting to commit.

**`connection refused` on Postgres.** The server is not running, not a DPM bug.

**Blank page / API errors in the browser.** The backend is down or on another
port. Check the backend log first; `frontend/.env` second.

**Frontend build errors.** `npm run build` runs `tsc -b`; a type error is an
error here, not a warning. `npm run lint` runs oxlint. Both must pass before
calling frontend work done.

**The Snowflake MCP server will not start.** Expected. `.mcp.json` points at a
path from another machine. Nothing in the app depends on it - connectors read
their credentials from Postgres.

**A repo will not ingest.** `ddl_parser` is lexical, not a SQL grammar. It
handles the Snowflake DDL shapes in `snowflake/` and `GET_DDL` output; dbt
models, Jinja or view-only definitions parse to little or nothing, and surface
as an explicit warning rather than a silent empty success.

## Where to read next

`README.md` for the stack, `STATUS.md` for architecture and current state,
`FLOW.md` for the intended user journey, `PLAN.md` for requirements, `CLAUDE.md`
for how to work in this repo before changing anything.
