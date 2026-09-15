# DPM - Data Integrity Check Engine

An agentic data integrity/parity check engine for Snowflake: scheduled checks, LLM-backed root cause analysis, and a simulated Jira ticket board.

Checks are organized into **projects** - one per source system or pipeline. The home page ranks projects by health so whatever is broken is the first thing you see; everything else (checks, tickets, settings) lives inside a project.

See [PLAN.md](./PLAN.md) for requirements/milestones and [STATUS.md](./STATUS.md) for current state and setup instructions.

## Stack

- **Frontend**: Vite + React + TypeScript (`frontend/`)
- **Backend**: FastAPI + SQLAlchemy + Alembic (`backend/`)
- **Agentic framework**: LangGraph + LangChain (`backend/app/rca/`) - the RCA agent is a LangGraph graph, using a custom LangChain chat model that shells out to the Claude Code CLI (no Anthropic API key required)
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
