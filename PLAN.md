# Agentic Data Parity & RCA Platform — Requirements & Plan

## 1. Problem Statement

Build a web application that autonomously:

1. Runs scheduled data integrity / parity checks (starting with intra-Snowflake, e.g. table-to-table, source-to-mart, row-count/checksum/schema drift checks).
2. On failure, performs LLM-driven root cause analysis (RCA) by correlating Snowflake metadata (query history, schema/DDL changes, task/pipeline logs) with git history (recent commits/PRs touching the relevant dbt models, ETL scripts, or schema definitions).
3. Auto-creates a Jira ticket pre-filled with the RCA summary, evidence, and an assignee derived from `git blame` on the most-likely-responsible change.
4. Is architected so the check engine and connector layer generalize to cross-platform parity (Snowflake ↔ RDS ↔ other sources), not just intra-Snowflake — the initial build should not require rearchitecting to add a second data source.

## 2. Goals / Non-Goals

**Goals**
- Reliable, observable scheduled execution of parity checks with historical results.
- Actionable RCA (not generic LLM guesses) grounded in real metadata + diffs.
- Tickets that reduce triage time: correct owner, clear evidence, reproducible query.
- Pluggable connector/check architecture (new source = new adapter, not new engine).

**Non-Goals (v1)**
- Auto-remediation (fixing the data or the code) — v1 only detects, diagnoses, and files tickets.
- Full data catalog / lineage product — we consume lineage signals, not build a catalog.
- Supporting every possible data source on day one — only Snowflake (source) is required for v1; the RDS/cross-platform path is a design constraint, validated with one second connector as a spike, not a full delivery.

## 3. Requirements

### 3.1 Functional Requirements

**Check Engine**
- FR1: Define checks declaratively (YAML/JSON or DSL) — type (row count, checksum, null-rate, schema drift, referential integrity, freshness), source(s), schedule, threshold/tolerance.
- FR2: Execute checks on a schedule per-check (cron expression) via a scheduler service.
- FR3: Persist every run's result (pass/fail, metrics, timestamp, duration) for trend/history views.
- FR4: Support both single-source checks (intra-Snowflake) and multi-source parity checks (source A vs source B) via a common comparison abstraction.

**RCA Agent**
- FR5: On check failure, gather context automatically:
  - Snowflake: `QUERY_HISTORY`, `ACCESS_HISTORY`, `OBJECT_DEPENDENCIES`, DDL/table metadata, recent `ALTER`/schema changes on implicated objects.
  - Git: recent commits/PRs touching dbt models, SQL, or ETL code that map to the failing object(s) (via a config-driven mapping of table → repo path(s)).
- FR6: Feed gathered context to an LLM with a structured prompt to produce: probable root cause, confidence, supporting evidence excerpts, suggested next steps.
- FR7: Cache/store RCA output tied to the run for audit and reuse.

**Ticketing**
- FR8: On failure (or on RCA completion), auto-create a Jira ticket via Jira REST API with: title, description (RCA summary + evidence + check metadata + links to Snowflake query history / commit), severity/priority mapped from check tolerance, labels/component.
- FR9: Derive assignee from `git blame` on the most recently changed lines/files most likely responsible (per RCA's identified root-cause artifact); map git author email → Jira account ID (via Jira user lookup); fallback to a configured default owner/team if no confident mapping.
- FR10: Avoid duplicate tickets for repeated failures of the same check within a cooldown window (link to existing ticket / comment instead). **Done** - one Jira issue per incident; repeated failures comment on it.

**Extensibility**
- FR11: Connector interface abstracts source access (query metadata, run comparison query, fetch schema) so Snowflake and RDS (Postgres/MySQL) implement the same interface.
- FR12: Check engine operates against connector interfaces only — no source-specific logic outside connectors.

**Web Application**
- FR13: Dashboard: check list, status, history/trend, drill into a failed run's RCA and linked Jira ticket.
- FR14: CRUD UI (or config-as-code + UI viewer) for check definitions and source/connector configuration.
- FR15: Manual "run now" and "re-run RCA" actions.
- FR16: Auth (SSO/OAuth) and role-based access (viewer vs admin who can edit checks/connectors).

### 3.2 Non-Functional Requirements
- NFR1: Scheduler must survive restarts (persisted job state, not in-memory only).
- NFR2: Secrets (Snowflake creds, Jira API token, git/PAT, LLM API key) stored in a secrets manager, never in code/config repo.
- NFR3: LLM calls are cost/latency bounded — cap context size, cache results, avoid re-running RCA on unchanged failures.
- NFR4: All external calls (Snowflake, Jira, git provider, LLM) have retries/backoff and are individually observable (logs/metrics).
- NFR5: System is idempotent — re-running a check or RCA does not create duplicate side effects (esp. Jira tickets).
- NFR6: Multi-tenant-ready data model (org/workspace scoping) even if v1 ships single-tenant, to avoid rework.

## 4. Architecture Overview

```
┌─────────────────────────────────────────────────────────────────┐
│                         Web App (UI + API)                       │
│  Next.js/React frontend  ·  API layer (REST/tRPC)  ·  Auth       │
└───────────────┬───────────────────────────────────┬─────────────┘
                │                                   │
        ┌───────▼────────┐                  ┌───────▼────────┐
        │  Scheduler /     │                  │  App DB         │
        │  Job Orchestrator│◄────results──────┤ (Postgres):     │
        │ (cron + queue)   │                  │ checks, runs,   │
        └───────┬──────────┘                  │ RCA, tickets    │
                │                              └────────────────┘
        ┌───────▼──────────────────────────────────────────────┐
        │                  Check Engine                         │
        │  - Check registry / DSL evaluator                     │
        │  - Comparator (single-source & cross-source parity)   │
        └───────┬───────────────────────────────┬───────────────┘
                │                               │
      ┌─────────▼─────────┐           ┌─────────▼─────────┐
      │ Connector: Snowflake│           │ Connector: RDS/... │  (interface: query(), schema(), metadata())
      └─────────┬──────────┘           └─────────┬─────────┘
                │ (on failure)
        ┌───────▼───────────────────────────────────────────┐
        │                  RCA Agent                          │
        │  Context gatherers:                                 │
        │   - Snowflake metadata (query/access/ddl history)   │
        │   - Git history (commits/PRs via GitHub/GitLab API) │
        │  LLM orchestration (prompt build, call, parse)      │
        └───────┬─────────────────────────────────────────────┘
                │
        ┌───────▼───────────────────────────────────────────┐
        │              Ticketing Service                      │
        │  - Jira client (create issue, search, comment)       │
        │  - Owner resolver (git blame → git email → Jira user)│
        │  - Dedup/cooldown logic                              │
        └──────────────────────────────────────────────────────┘
```

**Key design decisions**
- **Connector interface** (`Connector`): `runQuery()`, `getSchema(object)`, `getRecentChanges(object, window)`, `compare(sourceA, sourceB, spec)`. Snowflake and RDS connectors implement this; the check engine and RCA agent never import a source SDK directly.
- **Check DSL** stored as versioned config (DB-backed, editable via UI) so checks are data, not code.
- **RCA as a pipeline**, not a single LLM call: gather → rank/filter evidence → prompt → structured-output parse (JSON schema) → store. This keeps it debuggable and lets the LLM be swapped.
- **Git integration**: a config table maps {Snowflake object / dbt model} → {repo, path glob} so the RCA agent knows where to look for related commits; git blame runs against the file(s) most recently touched that map to the failing object.
- **Async job execution**: scheduler enqueues check runs onto a worker queue (e.g. BullMQ/Celery) so long-running checks/RCA don't block the scheduler or API.

## 5. Suggested Tech Stack

- Frontend/API: Next.js (React) + tRPC or REST, TypeScript throughout for shared types across connector interfaces.
- Scheduler/Queue: node-cron or BullMQ (Redis-backed) for scheduling + job execution; Postgres for durable state.
- App DB: Postgres (checks, runs, RCA records, tickets, connector configs).
- Connectors: Snowflake via Snowflake SDK/driver (or the existing `mcp__snowflake` MCP server pattern already configured in this project); RDS via `pg`/`mysql2`.
- Git integration: GitHub/GitLab REST API (or local clone + `git blame`/`git log` if repos are accessible on disk/CI runner).
- LLM: Claude (Sonnet) via Anthropic API, structured/tool-use output for RCA JSON.
- Ticketing: Jira REST API (v3) with OAuth 2.0 (3LO) or API token auth.
- Secrets: cloud secrets manager (AWS Secrets Manager / GCP Secret Manager) or Doppler/Vault depending on deploy target.
- Deployment: containerized (Docker) services — web app, worker, scheduler — behind standard CI/CD.

## 6. Milestones

### M0 — Foundations (Week 1)
- Repo scaffold, CI, app DB schema (checks, runs, connectors, rca_results, tickets tables).
- Connector interface defined; Snowflake connector implemented (query + schema + change-history methods) using existing Snowflake MCP/creds in this environment.
- Basic auth + skeleton web app (empty dashboard).

### M1 — Check Engine (Intra-Snowflake) (Weeks 2–3)
- Check DSL + registry (row count, checksum, null-rate, schema-drift, freshness checks).
- Scheduler + worker queue running checks on cron; results persisted.
- Dashboard: check list, run history, pass/fail status.
- **Milestone demo**: a scheduled check runs daily against two Snowflake tables and reports drift.

### M2 — RCA Agent (Weeks 4–5)
- Snowflake metadata gatherer (query history, DDL/ALTER history, object dependencies for the failing object).
- Git gatherer: table/model → repo mapping config; fetch recent commits/PRs touching mapped paths.
- LLM prompt pipeline producing structured RCA (root cause, confidence, evidence, next steps); stored and shown in UI.
- **Milestone demo**: trigger a synthetic failure (e.g. manual schema change) → RCA correctly cites the change and the commit that made it.

### M3 — Auto-Ticketing (Week 6)
- Jira client integration (create/search/comment issues).
- Owner resolution: git blame on implicated file(s) → author email → Jira account lookup → assignee; fallback logic.
- Dedup/cooldown to avoid duplicate tickets.
- **Milestone demo**: failing check → RCA → Jira ticket auto-created, correctly assigned, with RCA summary in the description.

### M4 — Cross-Platform Parity Spike (Weeks 7–8)
- Implement RDS (Postgres) connector against the same `Connector` interface.
- Add one cross-source parity check (e.g. row counts / checksums between an RDS table and its Snowflake replica).
- Validate the check engine and RCA agent required **zero** changes beyond adding the new connector + check config — this is the architectural proof point for FR11/FR12.
- **Milestone demo**: a Snowflake-vs-RDS parity check runs on schedule and produces RCA/ticket the same way an intra-Snowflake check does.

### M5 — Hardening & Productionization (Weeks 9–10)
- Secrets management wiring, retries/backoff on all external calls, observability (logs/metrics/alerts on the platform itself — "who checks the checker").
- RBAC, multi-tenant scoping if needed, config UI for checks/connectors/mappings.
- Load/soak test scheduler with realistic check volume; tune LLM context size and caching for cost.
- Docs + runbook for adding a new connector/check/source.

## 7. Open Questions (for user/stakeholder input)
- Which Jira project(s)/workflow should tickets land in, and what priority mapping (check severity → Jira priority)?
- Is git history accessible via API (GitHub/GitLab) or does the RCA agent need local repo clones / CI artifact access?
- What's the authoritative mapping source from Snowflake object → owning repo/team (manual config, dbt `meta` tags, existing catalog)?
- Multi-tenant requirement now or later — affects DB schema design in M0.
- Target RDS engine for the M4 spike (Postgres vs MySQL) and how replication/CDC between RDS and Snowflake currently works (affects what "parity" means for that pair).
