# Project Status / Handoff Notes

Read this first if you're picking this up in a new session. High-level requirements/plan/milestones live in `PLAN.md` - this file is "what's actually been built so far and how to pick it back up," updated as of **2026-09-23**.
The *target* shape - what a project is, what a test is, the sections a user navigates - is in `FLOW.md`.

## TL;DR

The app was **fully rewritten** from Next.js/Prisma/TypeScript to **Vite+React (frontend) / FastAPI+SQLAlchemy (backend, Python) / LangGraph+LangChain (RCA agent)**, at the user's explicit request. The old Next.js app has been deleted; git history still has it if ever needed (last commit before deletion: `c8c732e`, plus everything built in this session on top of the working tree).

Functional parity with the previous Next.js app is verified working end-to-end:
- **M0/M1**: Check engine + dashboard
- **M2**: RCA agent, now built as a **LangGraph** `StateGraph` (gather evidence -> try LLM synthesis -> fall back to heuristic on failure), using a custom LangChain chat model that shells out to the Claude Code CLI - still no `ANTHROPIC_API_KEY` needed
- **M3**: Ticketing, in Jira. This app has no ticket board of its own
- Account-agnostic Snowflake connectors (encrypted credentials, connect any account via the UI)
- **Repository ingestion**: a project is now started by pasting the git URL of the repo that defines
  the pipeline. The DDL is parsed, checks are derived from it, an agent adds the ones rules cannot
  derive, and the user reviews before anything is written. See "Starting a project from a repository".
- The visual design system (accent color, tokens, cards, badges) was ported over into the new frontend

There are now **three** components, and the distinction matters to anyone
picking this up:

1. **Check derivation** (`ingest/heuristic.py`) - a deterministic *workflow*,
   not an agent. Runs once at setup and writes the parity check set, plus a
   coverage report naming what it could not cover and why.
2. **The maintenance agent** (`maintenance/`) - runs when the DDL or a task
   definition changes, re-derives the affected checks, and reconciles them
   against what is there. Writes reviewable proposals, never silent edits.
3. **The reporting agent** (`monitoring/`) - runs continuously, owns the
   incident lifecycle, and decides what to say: comment, escalate, warn,
   suppress, or nothing. It says it in Jira.

**There is no ticket board in this app.** An `Incident` is the record of what
went wrong and what was said about it; the Jira issue is that incident's
external face. This app stores a reference to the issue and a cached view of
its status, refreshed once per sweep - never a copy. With Jira unconfigured
there is no tracker at all, and incidents are still opened, escalated and
cleared here.

Both agents are optional. With `AGENT_MODEL` unset each falls back to a rule
path that still does the mechanical work - incidents open, dedup, escalate
and clear; drift is detected and proposed. The agents replace the
*judgement*, not the plumbing.

**Not started**: M4 (RDS/cross-platform connector spike), M5 (hardening).

## The target flow lives in `FLOW.md`

`PLAN.md` holds requirements and `STATUS.md` holds what is built. `FLOW.md` is
new and holds the third thing: the shape the product is being moved toward -
what a project is (one repo, one warehouse connection, one source connection),
what a test is, and the sections a user navigates between. Read it before
adding a check type or changing setup, because several things in this file are
deliberately on the way to something it describes rather than finished.

Its §5 is an honest gap list against the tree, and its §6 the engineering
order. Step 1 of that order has landed and is described below.

### Every check owes its reader three things

Description, the logic behind it, and the SQL it runs. This is now a property
of every check type, not a convention:

- `checks.description` - what it asserts, at most two lines
- `checks.rationale` - **new column** (migration `c41d9be6a3f2`). The logic was
  previously computed by ingestion and then buried inside the `derived_from`
  JSON blob, so only derived checks had one and nothing ever rendered it. The
  migration lifts those buried values into the column. `derived_from` keeps its
  copy on purpose: that blob records what the proposal said at the time, and
  rewriting it to match an editable column would destroy the one question it
  answers.
- `checks/sql.py` - `build_statements(type, config)`, the SQL, built on read
  rather than stored.

The load-bearing part is that `sql.py` is not documentation. `engine.py` calls
the same builders to produce the queries it executes, so a rendered statement
and an executed one cannot drift. `test_check_sql.py` asserts this mechanically
- it greps the engine's source for re-inlined literal SQL - because a
"documentation" copy would be wrong silently, and a reviewer approving one
statement while the engine runs another is the failure worth preventing.

The unit is a labelled statement rather than a string, since some checks issue
two queries and a cross-source check issues them against different systems;
presenting those as one script would read as a join that cannot exist.

`try_build_statements` reports rather than raises, for list views: one
unbuildable check must not blank the page, and its error belongs next to it as
the finding it is - a check whose SQL cannot be built is one that will ERROR on
its first run, which is worth learning during review.

Surfaced at `GET /api/checks` and `/api/checks/{id}` (`statements`,
`statements_error`), on ingest proposals at `GET /api/ingest/{job_id}`, and
rendered by `frontend/src/components/CheckExplanation.tsx` - behind the
**Underlying SQL** subtab on the check detail page, and inline in the setup
wizard's review step, where a proposal approved without its SQL is a check
nobody read.

### Stage, and why a check is not filed under a database (`checks/stage.py`)

The project page groups checks by the pipeline hop they watch - `STG_TO_BRONZE`,
`BRONZE_TO_SILVER`, `SILVER_TO_GOLD`, `DATA_QUALITY` - because that is how a
failure is reasoned about. The database a check's row lives in was always the
wrong axis: a parity check compares two databases, so filing it under one of
them is half a lie.

`stage` is derived from the objects a check's config compares and stored on the
row. Deriving it in the frontend was rejected: every list view would re-derive
it, nothing could be filtered or counted server-side, and a bad guess could not
be corrected. `stage_locked` records that a person overruled the derivation, so
the next config edit does not silently move the check back.

Origin (`DERIVED` / `AGENT` / `HUMAN`) is a **second axis and stays a filter**, not
a fifth tab. A hand-written parity check is both derived-in-kind and custom, so
tabs on origin would either duplicate rows or hide them.

Only `DATA_QUALITY` has an add-check control. The three movement stages are
populated by derivation from the pipeline's own definition - hand-writing one
means transcribing a MERGE and then maintaining the transcription - so those
tabs are for reviewing and tuning, and say so on screen rather than just
omitting the button.

### Version history, and the stored SQL (`checks/versions.py`, `check_versions`)

A human edit used to overwrite the check in place, so "what did this assert
last Tuesday" was unanswerable. `check_revisions` looks like it covers this and
does not - it is the maintenance agent's proposal queue, and a UI edit never
went through it.

`check_versions` appends one row per change to a check's logic, from every write
path (`human`, `agent`, `ingest`, `seed`), and carries the **SQL that config
rendered to at the time**. Generation from config stays the source of truth for
what executes; the stored text is a *snapshot*, which is a record of the past
and so cannot go stale the way a live copy would. It is also what makes a
version diff readable - two configs side by side say much less than two queries.

Config -> SQL is automatic on every write. **The reverse is not implemented**:
editing SQL and having config follow needs arbitrary SQL parsed back into
structured fields, which only works for SQL the generator itself produced and
fails silently the moment someone hand-edits a predicate.

Restore is applied as a new version on top, never by rewinding - the versions in
between are what explain how the check got into the state being backed out of.

Pinning (`POST /api/checks/{id}/pin`) is workspace-wide, not per-viewer: there is
no user table to hang a personal pin off. Its endpoint is separate from `PATCH`
so pinning does not stamp `human_edited_at` and take a derived check out of the
agent's hands.

### The loyalty test pipeline, and what it is for (`snowflake/dpm_src_loyalty/`)

The original five databases are all one shape: an append-only VARIANT landing
table, a stream, and a MERGE that collapses to entity state. A parity engine
tested only against that shape is untested against most of what real pipelines
do - which was not obvious until an FCC production repo was read as reference.

`DPM_SRC_LOYALTY` / `DPM_LOYALTY_360` are new, and deliberately different:

- **MERGE-on-PK bronze** (`MEMBERS_RAW`) - one row per entity, not an event log,
  with CDC `OP` of I/U/D. Tombstones are real bronze rows, so the MERGE's
  `OP <> 'D'` has to be lifted onto the source side of any parity check
- **Composite snapshot grain** (`POINTS_SNAPSHOT_RAW`) - `(MEMBER_ID,
  SNAPSHOT_DATE)`, where keying on the member alone reads every day after the
  first as a duplicate
- **An SCD2 dimension** (`SILVER.MEMBERS`) - the thing parity structurally
  cannot check
- **Aggregate gold** (`MEMBER_ENGAGEMENT`) - where key parity is meaningless and
  reconciliation is the real check
- **Loader cursor state** (`CURSOR_STATE`) - what makes a source-to-bronze
  question answerable from inside the warehouse

**Not deployed yet.** `snowflake/README.md` has the deploy order (child task
before parent task, tables before streams before tasks) and the by-hand
validation queries. `ioi/` picks them up on the next `dump_ddl.py` run after
that; until then the dump skips them with a `!!` line.

### SCD2 integrity (`backend/app/checks/scd2.py`)

The first check in the advanced section. Four assertions, evaluated in one
statement so they describe the same snapshot, but counted separately because
they fail differently: no current row (the open step did not run), several
current rows (the close step did not, and every join through the dimension now
fans out and doubles its measures), overlapping windows (an as-of lookup is
ambiguous), and gaps (a lost batch, where the fact silently drops out).

Two things are load-bearing:

**The natural key comes from the MERGE, not the table.** A table cannot
distinguish `MEMBER_ID` from `MEMBER_KEY`, and choosing the surrogate would make
every assertion trivially pass - one row per "key", no window to overlap. So no
MERGE means no proposal at all, rather than a guess. A check that cannot fail is
more dangerous than a missing one, because the coverage report then claims
ground nothing covers.

**The open-ended convention is configured, not inferred.** A sentinel
(`9999-12-31`) and NULL are both in use and are not interchangeable; a table
mixing them is precisely the fault being looked for, so inferring the convention
from whichever appears more often would make the check agree with the corruption
it exists to find.

Parity against an SCD2 target is separately handled: `BronzeToSilverParityConfig`
gained `silverFilter`, set to `IS_CURRENT = TRUE` when the target's column shape
says type-2. Without it that check fails forever on correct data and gets muted.

### Parser fixes driven by the FCC reference repo

Each of these was a case of deriving *nothing* while looking like it had derived
everything - a short, clean proposal list is indistinguishable from a simple
pipeline. `FLOW.md` §7 has the full account.

- `IS NOT DISTINCT FROM` in an ON clause now yields keys (`ddl_parser.
  _normalize_key_equality`). It is the idiomatic join wherever keys are
  nullable, and FCC uses it throughout; we split on `=` only, so their entire
  silver layer would have produced zero parity checks
- Metadata columns are matched by suffix on an underscore boundary, so
  `ETL_LOADED_AT` is recognised as the settle column. Only compound candidates
  qualify - allowing `_ID` to match made `MEMBER_ID` register as a sequence
  column, a worse bug than the one being fixed
- A MERGE whose source equals its target is no longer a pipeline hop. That is a
  MERGE-on-PK loader writing its own landing table, and parity derived from it
  compares a table to itself and passes unconditionally
- Value columns are filtered against the target's real column list. An SCD2
  close statement selects `CHANGED_AT` only to write it into `VALID_TO`, and
  comparing against a column that does not exist is an ERROR run - which says
  the check is wrong, not that the pipeline is

Still open, and noted so it is not built the wrong way: FCC templates every
object name (`BLINKFIRE_{{ env }}.SILVER.POSTS`). That is a **binding** problem,
not a parsing one - we hold a live warehouse connection, so the environment
should be discovered from the databases the credentials reach and confirmed by
the user, rather than guessed at lexically.

## Architecture

```
backend/                  FastAPI + SQLAlchemy + Alembic (Python, managed with `uv`)
  app/
    main.py               FastAPI app, CORS, router registration, scheduler lifecycle
    config.py             Settings (pydantic-settings, reads backend/.env)
    db.py                 SQLAlchemy engine/session
    models.py             ORM models: Project (+ repo_url/ref/commit/path), Database (+ repo_paths),
                            Connector (project-owned), Check (+ provenance, + rationale), CheckRun,
                            RcaResult,
                            Incident (+ cached Jira reference), IncidentEvent, CheckRevision
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
      sql.py              build_statements(type, config) -> the labelled SQL each check runs.
                            engine.py calls the same builders, so what is rendered for review
                            and what is executed cannot drift apart
      b2s_parity.py       BRONZE_TO_SILVER_PARITY: builds the single deterministic SQL statement
                            that proves silver is a deduplicated, lossless projection of bronze.
                            See the module docstring for why it is a FULL OUTER JOIN and not EXCEPT.
      runner.py           execute_check(db, check_id): persists a run, runs RCA on failure, then
                            hands to monitoring/triage. It no longer files tickets itself -
                            that needs the incident's history, not one run
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
    rca/                  Root-cause analysis on a failed run - see below
    agents/               SHARED AGENT CORE
      model.py            The only module that names an LLM provider. "claude-cli" for the
                            local CLI, anything else through init_chat_model - so
                            "openai:gpt-5" / "anthropic:claude-opus-5" / "ollama:llama3" are
                            configuration. Returns None when unset - agents are optional
      claude_cli.py       The local, key-free path: a BaseChatModel over `claude -p` with
                            tool calling emulated over a JSON text protocol, so it can drive
                            create_agent exactly as a native tool-calling model does
      runner.py           create_agent + call/tool budgets + validated structured output
    monitoring/           THE REPORTING AGENT - incidents, comments, escalation
      incidents.py        Lifecycle primitives with no judgement in them (open/clear/reopen/suppress)
      triage.py           The deterministic floor: open on failure, comment on change, clear on two
                            consecutive passes, escalate an untouched issue, reopen a closed-but-
                            failing one. This is the whole monitor when no model is configured
      tools.py            Read-only views the agent investigates through (history, trend, RCA, siblings)
      prompts.py          The reporting agent's operating manual - its specification, kept reviewable
      agent.py            LangGraph agent loop. Decides NONE/COMMENT/ESCALATE/WARN/SUPPRESS.
                            apply_decision() enforces what it may actually do - CLEAR is refused
      sweep.py            The heartbeat: rule passes first, then the agent, each committing alone
    maintenance/          THE MAINTENANCE AGENT - keeping checks correct as the DDL moves
      detector.py         Repo commits (diffed against Database.repo_paths) + warehouse DDL history.
                            Their disagreement is itself the finding: undocumented drift
      tools.py            Current checks, freshly derived set, the diff, and path-jailed repo reads
      prompts.py          The maintenance agent's operating manual
      agent.py            Reconciles derived against existing. Writes CheckRevision rows, never
                            edits a check. _may_auto_apply is the consent boundary, in code
      service.py          detect -> re-derive -> reconcile, with a no-model rule path
    tickets/
      base.py             TicketBackend protocol: create / comment / transition /
                            fetch_state / set_priority. fetch_state is not incidental -
                            "has anyone responded" is unanswerable without asking Jira
      jira.py             Jira Cloud REST v3, the only tracker. Never raises; renders ADF
      registry.py         Returns the backend, or None when Jira is unconfigured
    scheduler.py           APScheduler: per-check cron jobs, the monitor sweep, the maintenance scan
    routers/               projects.py, checks.py, connectors.py, ingest.py,
                            monitoring.py (incidents, revisions, manual triggers)
    seed.py                Seeds 1 connector + 4 example checks (`python -m app.seed`)
  alembic/                 Migrations (env.py wired to app.db.Base.metadata + app.config.settings)

frontend/                 Vite + React + TypeScript
  src/
    lib/api.ts             Typed fetch client for the FastAPI backend (VITE_API_URL, default localhost:8000)
    lib/check-types.ts      UI metadata for check-type form fields (keep in sync with backend/app/checks/config_schemas.py)
    components/             TopNav, Breadcrumbs, HealthPill, StatusBadge, RunNowButton, EnabledToggle,
                             DeleteButton, CheckForm, SnowflakeCredentialFields
    lib/time.ts             Parses the API's naive-UTC timestamps. Use this, never bare `new Date(iso)` -
                             a bare parse reads them as local time and shifts every timestamp.
    pages/                  Projects (home), NewProject (repo-driven setup wizard),
                             NewProjectManual (the old name/connect/databases wizard), ProjectOverview,
                             ProjectIncidents, ProjectRevisions, ProjectConnections,
                             ProjectSettings, NewDatabase, DatabaseChecks, DatabaseSettings,
                             CheckDetail, NewCheck, EditCheck
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
/projects/:slug                                Checks by stage (tabs, search, pins).
                                                 ?stage=... selects a tab; with none named,
                                                 opens on the first populated one
/projects/:slug/databases                      The databases this project spans
/projects/:slug/incidents                      Incidents, with the agent's comment stream
                                                 (each links out to its Jira issue)
/projects/:slug/changes                        Proposed check changes awaiting review
/projects/:slug/settings                       Rename, connections in use, delete
/projects/:slug/connections                    Connections owned by this project
/projects/:slug/databases/new                  Add one (picked from live discovery)
/projects/:slug/databases/:dbSlug              Checks on this database
/projects/:slug/databases/:dbSlug/settings     Describe, remove from project
/projects/:slug/databases/:dbSlug/checks/...   new | :id | :id/edit
                                                 :id takes ?tab=sql|versions|runs

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
- If that raises (CLI missing, timeout, bad JSON), the conditional edge routes to `synthesize_heuristic` (`heuristic.py`) - the same deterministic fallback logic as before, so RCA never blocks incident reporting.
- `generate_rca(...)` in `graph.py` is the single entry point `runner.py` calls.

### The three components, and which is an agent

This is the thing to understand before changing anything here.

**Check derivation is a workflow, not an agent** (`ingest/heuristic.py`). It
runs once at setup, is fully deterministic, and its job is completeness: every
MERGE with a readable key gets key-and-value parity at whatever layer it sits,
filtered MERGEs included (the filter is lifted onto the source side), plus
freshness, schema drift, and a zero-tolerance null-rate check per NOT NULL
column. `assess_coverage` then names every table it could *not* cover and why,
because a short proposal list and a simple pipeline look identical otherwise -
and the short list is what a repo the parser did not understand produces.

**The maintenance agent** (`maintenance/`) runs when that definition moves.
The rules re-derive; the agent reconciles the new against the old. That
reconciliation is the judgement: the same diff can be a rename to follow or a
replacement to retire, and telling them apart means reading what the MERGE now
maps into the column - which a lexical parser cannot do and a re-run of the
rules would simply clobber.

**The reporting agent** (`monitoring/`) runs continuously over incidents.

### Incidents replaced per-run ticketing

A `Ticket` used to hang off a `CheckRun`, so there was nowhere to record a
second observation of the same problem - which is why every failed run filed a
fresh ticket and FR10 stayed open. The local database had 381 tickets across 6
checks, 209 of them for one continuously-failing inventory check.

The `tickets` table has since been dropped entirely: issues live in Jira and
nowhere else.

An `Incident` is one problem across however many runs it takes to fix.
`IncidentEvent` is its comment stream, mirrored to the incident's Jira
issue when there is one.

`WARNING` is an incident state with no matching run status, deliberately: a
check that is passing but degrading is not a failure, and teaching a
deterministic comparison to have opinions about trends would put judgement in
the one place that must stay predictable.

Two rules are worth knowing before tuning them. An incident clears after
**two** consecutive passes, because one green run of a flapping check is not a
recovery. And escalation skips any issue somebody has moved off an untouched status,
or assigned - the
signal is absence of response, not slowness.

### The agent will fabricate if you let it

Worth reading before tuning any of the agent knobs, because it is not
hypothetical and it was caused by a plausible-looking configuration.

The reporting agent was originally handed up to 25 incidents per sweep with
a loop budget affording maybe two investigations. Asked to decide on all 25,
it did not say it lacked evidence - it produced a confident escalation
citing a metric climbing "3841 -> 5089" across seven runs, a "migration
0047" and a "WMS feed merged 2026-09-15". None of it existed. The real
metric was 0 on every run, and the stored RCA it claimed to quote had a
different cause at 0.4 confidence, not 0.82.

Three changes, and the third is the one that matters:

1. `MAX_INCIDENTS_PER_SWEEP` is 4, not 25 - the batch has to fit the budget.
2. `sweep.needing_review` only offers incidents where something has changed
   since the agent last spoke. Asking about 209 unchanged incidents every
   five minutes invites it to manufacture something to say.
3. **The tools record which incidents were actually read, and any decision
   that writes - a comment, an escalation - is dropped for an incident the
   agent never opened.** A prompt instruction not to invent is unenforceable;
   this is checkable. On the next real sweep it dropped three SUPPRESS
   decisions on unread incidents and let through one comment whose numbers
   matched the database exactly.

The general lesson for anything added here: a fabricated escalation reads
exactly like a good one, so the defence has to be mechanical.

### What the agents may and may not do

Both propose; the applying code decides. This is enforced in Python, not only
in the prompts, because a prompt is guidance a model can misread.

The reporting agent cannot `CLEAR` an incident. Whether a problem is over is a
question about the data, answered by the check passing; a model reasoning its
way to "this looks resolved" would close issues on pipelines that are still
broken, and that failure is silent.

The maintenance agent cannot touch a check whose `origin` is `HUMAN` or whose
`human_edited_at` is set, and auto-applies nothing below 0.75 confidence and no
`CREATE` at all. The reason is the blind spot documented below: a check derived
from a MERGE asserts that the MERGE did what the MERGE says, so the derived
inventory check agrees with the `WAREHOUSE_ID` mis-mapping and passes, while
the hand-written one fails. Overwriting hand-written checks would remove the
only thing that catches that class of bug.

Check provenance is recorded at creation (`origin`, `derived_from`,
`derived_at_commit`) because it cannot be reconstructed later. Any edit through
the API stamps `human_edited_at`. The migration marked all pre-existing checks
`HUMAN`, which is the safe direction - it means propose, not rewrite.

### Running the agents on the local Claude Code CLI

`AGENT_MODEL=claude-cli` drives the `claude` binary already on the machine,
using whatever session it is logged in with. No API key, no provider package.

The obstacle was that an agent loop needs a model that emits structured tool
calls - `create_agent` reads `AIMessage.tool_calls` to decide what to run
next, and (via its default `ToolStrategy`) delivers the final structured
response as a tool call too. `claude -p` returns text. So
`agents/claude_cli.py` renders the tool catalogue into the prompt, asks for a
single JSON object naming a tool call, and parses the reply back into a real
`AIMessage`. LangGraph cannot tell the difference, so the agent loop, the
middleware and the structured-output strategy all work unchanged.

Two things are load-bearing and worth not "simplifying" away:

**The model is told which tool ends the run.** `build_agent` passes the
response schema's name down as `response_tool_name`, and the protocol then
omits the free-text `final` shape entirely. Without that, the model answers
in prose, the agent ends with no structured response, and the run is
discarded - measured at two failures in three before the fix.

**The CLI gets `--allowedTools ""`.** The tools in play are this
application's, executed by LangGraph. A CLI that could also read files or run
commands would be a second, ungoverned agent inside the first.

What it costs: every turn is a fresh process, so a turn is tens of seconds
and a full agent run is one to three minutes. There is no prompt caching -
each turn re-sends the whole conversation - so cost grows with the square of
the loop length. `AGENT_MAX_MODEL_CALLS_CLI` (default 6) is therefore tighter
than the hosted budget. `claude --resume` would fix the re-sending, and is
deliberately not used: the CLI's history and LangGraph's message list could
drift apart, and a silent divergence is much harder to debug than a slow loop.

Good trade for a monitor that sweeps every five minutes. Not a good trade for
anything interactive.

### Configuring the agents

`AGENT_MODEL` in `backend/.env`, and that is the whole switch:

```
AGENT_MODEL=openai:gpt-5              # uv add langchain-openai,   OPENAI_API_KEY
AGENT_MODEL=anthropic:claude-opus-5   # uv add langchain-anthropic, ANTHROPIC_API_KEY
AGENT_MODEL=ollama:llama3             # uv add langchain-ollama
```

There is also a local option that needs no key at all:

```
AGENT_MODEL=claude-cli                # the Claude Code CLI on this machine
AGENT_MODEL=claude-cli:sonnet         # ...pinned to a specific model
```

`app/agents/model.py` is the only module that names a provider. Leave
`AGENT_MODEL` empty and both agents fall back to their rule paths - a supported
way to run this, not a degraded one. `GET /api/monitor/status` reports the
model string rather than a bare "enabled", because a model that is set but
unusable looks identical to "on" from anywhere else.

Jira needs `JIRA_BASE_URL`, `JIRA_EMAIL`, `JIRA_API_TOKEN` and
`JIRA_PROJECT_KEY` together. With any missing there is no tracker: incidents
are still opened, escalated and cleared, they simply have no issue attached,
and `GET /api/monitor/status` reports `ticket_backend: null`. See
`backend/.env.example` for everything.

### Tickets live in Jira (`backend/app/tickets/`)

There is no board here. `JiraBackend` is the only implementation of
`TicketBackend`, and `ticket_backend()` returns `None` when Jira is not
configured - deliberately a null *value* rather than a null *object*, because
a backend that accepts a comment and silently drops it is indistinguishable
from one that works.

The interface has five operations, and `fetch_state` is the one worth
explaining. Escalation turns on "has anyone responded to this", which is a
fact about Jira rather than about this database. The monitor sweep refreshes
each active incident's status and assignee once, and the rest of the pass
reads that cache - which keeps a network call out of every decision and keeps
`ticket_moved_at` meaning *when the issue changed* rather than when we last
wrote to our own row.

Nothing in the Jira client raises. A tracker that is down costs the external
copy of a comment, never the incident record. Descriptions are rendered as
Atlassian Document Format; v3 rejects a plain string on `description`, which
is the most common way a first Jira integration fails with an opaque 400.

An incident whose filing failed carries no `ticket_key`, and the next failing
run retries it, so the gap closes on its own.

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

The `setup-and-run` skill (`.claude/skills/setup-and-run/SKILL.md`) walks a
fresh clone through all of this - prerequisites, `backend/.env`, database,
seed, both servers, and the failure modes each step has. Keep it in sync with
this section; it is what a teammate with Claude Code actually follows.

```bash
# Postgres
brew services start postgresql@16   # if not running
createdb dpm_dev                    # if it doesn't exist

# Backend
cd backend
uv sync                             # installs into backend/.venv (Python 3.12, managed by uv)
source .venv/bin/activate
alembic upgrade head                # creates connectors/checks/check_runs/rca_results/incidents tables
python -m app.seed                  # seeds 1 connector + 4 example checks
uvicorn app.main:app --reload --port 8000

# Frontend (separate terminal)
cd frontend
npm install
npm run dev
```

`backend/.env` (gitignored) needs: `DATABASE_URL`, `CONNECTOR_SECRET_KEY` (generate your own - see comment in the file), legacy `SNOWFLAKE_*` vars (only used by the seeded `snowflake-default` connector), `RCA_LLM_COMMAND` (defaults to `claude`), and optionally `AGENT_MODEL` plus its provider key, and the four `JIRA_*` variables. `backend/.env.example` documents every one. Optionally `GITHUB_APP_ID`/`GITHUB_APP_SLUG`/`GITHUB_APP_PRIVATE_KEY_PATH` for the GitHub picker - see "Connecting GitHub". `ANTHROPIC_API_KEY`/`JIRA_*`/`GITHUB_TOKEN` are unused/stubbed.

`frontend/.env` (optional, gitignored): `VITE_API_URL` if the backend isn't at `http://localhost:8000`.

**⚠️ Never run destructive DB commands (`alembic downgrade`, dropping tables, etc.) without checking with the user first** - even on "just a dev database." Claude Code's own safety classifier blocks some destructive CLI invocations (e.g. `prisma migrate reset`) and requires explicit re-confirmation; the equivalent Python/SQL operations aren't guaranteed to be caught by that same classifier, so exercise the same caution manually.

## Design system

`frontend/src/index.css` defines the same CSS custom properties as before (`--background`, `--surface`, `--border`, `--accent`, `--accent-hover`, `--accent-foreground`, `--accent-soft`), exposed to Tailwind via `@theme inline`. Use these tokens (not raw `zinc-*`/`white`/`black`) in any new page or component.

## Known rough edges / things to fix eventually

- **Neither agent has been run against a live model.** Everything below the
  model - prompts, tools, schemas, budgets, and every branch of the applying
  code - is written and tested, and the rule fallbacks are verified end to
  end. But no `AGENT_MODEL` has been configured in this environment, so the
  first real run may need prompt tuning. Start with
  `POST /api/monitor/sweep` and read `agent_error` in the response.
- Warehouse-side change detection (`MAINTENANCE_SCAN_WAREHOUSE=true`) is
  written but unverified - it needs a live connection per project, and the
  seeded projects have no repository to compare against. The repo side is
  covered by tests driving real commits
- The maintenance agent is only reachable for projects created by repository
  ingestion. The seeded projects were built manually and have no `repo_url`,
  so `POST /api/projects/{slug}/maintenance` correctly refuses them
- GitHub webhooks are still off, so maintenance runs on a timer rather than
  on push. Turning them on is what makes it react to a commit immediately,
  and needs a publicly reachable endpoint - the first thing here that cannot
  run purely on localhost
- ~~No ticket de-duplication/cooldown (FR10)~~ **fixed** - one Jira issue per incident, so repeated failures comment instead of re-filing
- The Jira client is written and unit-tested against a fake, but has never been pointed at a real Jira. Field mappings most likely to need adjusting on first contact: the priority names in `PRIORITY_MAP`, `JIRA_ISSUE_TYPE`, and `JIRA_DONE_STATUS`/`JIRA_REOPEN_STATUS` if the workflow renames its columns
- `CROSS_SOURCE_PARITY` checks get no RCA object-level evidence gathering yet (every other check type has it)
- No auth/RBAC on the web app yet (PLAN.md FR16) - anyone with network access can hit the API routes
- Connector secret encryption uses a single symmetric key in `.env`, not a real secrets manager/KMS
- The connector edit form (`PATCH /api/connectors/{id}`) exists as an API but has no frontend UI yet - editing credentials currently requires calling the API directly
- The frontend still has no tests. The backend now has 155 (`cd backend && uv run pytest`) covering the parser's judgement calls, the derivation rules and their coverage report, the incident lifecycle, both agents' guard rails, and change detection against real git commits. Neither agent's *model* path is exercised by tests - what is tested is the code that decides what a model is allowed to do
- Repo ingestion jobs live in memory, so `uvicorn --reload` drops an in-flight analysis; re-running is cheap because the git checkout is cached. It also assumes one process, as APScheduler already does
- `ddl_parser` is lexical, not a real SQL grammar. It handles the Snowflake DDL shapes in `snowflake/` and the `GET_DDL` output in `ioi/`; a repo using dbt models, Jinja templating or `CREATE VIEW`-only definitions will parse to little or nothing (which surfaces as an explicit warning, not a silent empty success)
- Access tokens for private repos are used for the fetch and discarded - there is no stored credential, so re-ingesting a private repo later means re-entering the token
- `.mcp.json` still points the `snowflake` MCP server at `/Users/kotam/Documents/dpm/.mcp/`, a path from a different machine. Nothing in the app depends on it (connectors read credentials from Postgres), but the MCP server will not start as configured

## What's next (per PLAN.md)

- **M4**: implement a Postgres/RDS connector against the existing `Connector` protocol, add one cross-source parity check, prove the check engine needed zero changes
- **M5**: secrets management, retries/observability on external calls, RBAC, load testing, docs
- **Agents**: configure `AGENT_MODEL`, run a sweep, and tune the prompts in `monitoring/prompts.py` and `maintenance/prompts.py` against what they actually write
