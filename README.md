# DPM - Data Integrity Check Engine

An agentic data integrity/parity check engine for Snowflake: scheduled checks, LLM-backed root cause analysis, and a simulated Jira ticket board.

Work is organized **project -> database -> check**. A project is a data product (Customer 360) spanning the databases that serve it; checks live inside a database. The home page ranks projects by health so whatever is broken is the first thing you see.

You start a project by naming the **repository that defines your pipeline** - picked from GitHub, or pasted as a URL. The DDL is parsed for databases, schemas and MERGE statements; checks are derived from what it says (a MERGE states the key and column mapping, a task's `SCHEDULE` states the freshness threshold, a `CREATE TABLE` states the column contract); an agent adds the ones rules cannot derive. You review the proposal, then supply Snowflake credentials, and nothing is written until you do. Setup without a repository is still available at `/projects/new/manual`.

See [PLAN.md](./PLAN.md) for requirements/milestones and [STATUS.md](./STATUS.md) for current state and setup instructions.

## Stack

- **Frontend**: Vite + React + TypeScript (`frontend/`)
- **Backend**: FastAPI + SQLAlchemy + Alembic (`backend/`)
- **Agentic framework**: LangGraph + LangChain - two graphs, both using a custom LangChain chat model that shells out to the Claude Code CLI (no Anthropic API key required):
  - `backend/app/rca/` - root-cause analysis on a failed check
  - `backend/app/ingest/` - repository -> reviewable project. Both fall back to a deterministic path if the LLM is unavailable, so a broken CLI costs quality, never the feature
- **Database**: Postgres
- **Data source**: Snowflake (bronze/silver/gold test pipeline tracked in `snowflake/`)

## Quick start

See [STATUS.md](./STATUS.md#local-setup-if-starting-fresh) for full setup. Short version:

```bash
# Backend
cd backend
uv sync
uv run alembic upgrade head
uv run python -m app.seed
uv run uvicorn app.main:app --reload --port 8000

# Frontend (separate terminal)
cd frontend
npm install
npm run dev
```

Open http://localhost:5173.
