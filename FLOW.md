# The flow

What a project *is*, what a test *is*, and the one workflow that turns the
first into the second. `PLAN.md` holds requirements, `STATUS.md` holds what is
built; this file holds the shape everything else has to agree with.

## 1. A project is one pipeline

A project is **one repository, one Snowflake connection, one external source
connection**. Not "up to N of each" — one, because every comparison this tool
makes is between two of those three things, and a project that holds two
Snowflake accounts has no answer to "parity against which one".

```
Project
  repo            git URL + ref + commit it was derived from
  warehouse       the Snowflake connection (all layers live here)
  source          the external system bronze is landed from (optional)
  databases[]     the Snowflake databases the repo defines, reached through
                  the one warehouse connection
```

The external source connection is optional and its absence is not a
degradation: a project without it simply has no SOURCE→BRONZE stage, and the
setup flow says so rather than leaving an empty section that looks broken.

Databases stay as an organising layer beneath the project. One connection
reaches many databases, so "one connection" and "several databases" are not in
tension — `DPM_SRC_CRM`, `DPM_SRC_BILLING` and `DPM_CUSTOMER_360` are three
databases on one account serving one data product.

## 2. A test is three things, always

Every check in this system carries, without exception:

1. **Description** — at most two lines, plain language, what it asserts.
   "Every billing invoice landed in bronze reaches silver exactly once, keyed
   on INVOICE_ID."
2. **Logic** — the reasoning. Which invariant of the pipeline this relies on,
   where that invariant came from (this MERGE, this NOT NULL, this SCD2
   pattern), and what a failure would mean. This is what makes a failing check
   actionable instead of a red square.
3. **SQL** — the exact statement the engine runs, readable *before* the first
   run. A check whose SQL cannot be seen cannot be trusted or debugged, and
   "run it and see" is not review.

This is a hard rule for derived, agent-authored and hand-written checks alike.
A check missing any of the three is not a check, it is a guess with a schedule.

## 3. Two families of test, four sections

### 3.1 Pipeline parity — *did the data move losslessly*

Stage-tagged, because "which hop broke" is the first question asked of a
failure and answering it by reading table names is guesswork.

| Stage | Compares | Connection(s) |
|---|---|---|
| `SOURCE_TO_BRONZE` | the external system against the Snowflake landing table | source + warehouse |
| `BRONZE_TO_SILVER` | untyped landed VARIANT against the typed, deduplicated table | warehouse |
| `SILVER_TO_GOLD` | the conformed table against the serving/aggregate table | warehouse |

All three ask the same four questions and differ only in where the two sides
come from and how their columns are expressed:

- **Key parity** — every source key present exactly once on the target
- **No extras** — no target key absent from the source
- **No duplicates** — the target's declared key really is unique
- **Value parity** — mapped columns agree, which is the only one that catches
  a MERGE reading the wrong field (see the blind spot in `STATUS.md`)

SILVER→GOLD adds one shape the other two do not have: gold is frequently an
*aggregate*, not a row-for-row projection. Key parity is meaningless there, so
that case is reconciliation — `SUM(amount)` by grain on each side — and is a
different config, not a fudged parity check.

### 3.2 Data quality — *what is the data actually like*

These are not pass/fail first. They produce a **profile** — a number that is
interesting on its own — and a threshold turns that number into a verdict.
Null percentage is the canonical one: "`CUSTOMER_EMAIL` is 23% null" is an
insight about the data whether or not anyone set a tolerance on it.

Per column, on any layer:

- **Null rate** — `% NULL`, the headline metric
- **Blank rate** — empty string / whitespace-only, which is a null wearing a
  disguise and passes every NOT NULL constraint
- **Distinct count / uniqueness** — `COUNT(DISTINCT c) / COUNT(*)`, which
  catches a key that has quietly stopped being one
- **Domain / accepted values** — a status column that has grown a seventh value
- **Range** — negative amounts, timestamps in the future
- **Length / format** — emails without `@`, fixed-width codes that drifted

Per table:

- **Row count volume** — against history, not a fixed floor, so a table that
  normally lands 40k rows and lands 900 is caught
- **Freshness** — max timestamp against the writing task's own cadence

The profile is stored per run, so the dashboard can show null% as a line over
time. A threshold breach opens an incident; the number is recorded either way.

### 3.3 Advanced / business logic — *is the modelling itself sound*

Where the pipeline is technically lossless and still wrong.

- **SCD2 integrity** — four separate assertions on a dimension, each of which
  fails differently and so is worth naming separately:
  - exactly one current row per natural key (`IS_CURRENT = TRUE`)
  - no overlapping validity windows for a key
  - no gaps between a row's `VALID_TO` and the next row's `VALID_FROM`
  - `VALID_FROM < VALID_TO` on every row, and the current row's `VALID_TO` is
    the sentinel (`9999-12-31` or NULL — consistently, one or the other)
- **Referential integrity** — every fact's foreign key resolves to a dimension
  row, and where the dimension is SCD2, resolves *as of the fact's date*
- **Aggregate reconciliation** — gold totals equal silver totals at a stated
  grain, within a stated tolerance
- **Custom business rule** — an arbitrary SQL predicate that must return zero
  rows, with the description and logic fields carrying the rule in words.
  This is the escape hatch, and the two-line description is what stops it
  becoming a write-only pile of SQL

### 3.4 Schema contract

Schema drift, per table, as today. It sits outside the three sections because
it asserts structure rather than data, and it is the check that explains why
six others started failing at once.

## 4. The workflow: repo + connection → tests

One pass, with review before anything is written.

```
1. Point at it       repo URL (or GitHub picker) + Snowflake credentials
                     + optionally the external source credentials
2. Analyse           clone, parse the DDL, discover live objects, profile
3. Propose           derive the test set — all four sections
4. Review            every proposal shows description, logic and SQL
5. Confirm           project, connections, databases and checks written together
```

Step 3 is the substance, and each section is derived from a different thing:

| Section | Derived from |
|---|---|
| BRONZE→SILVER, SILVER→GOLD parity | the MERGE: source, target, ON clause → keys, SELECT aliases → value mapping |
| SOURCE→BRONZE parity | the landing table's payload keys matched against the source system's schema — only possible once the source connection is given |
| Freshness | a timestamp column + the writing task's `SCHEDULE` (x6 slack, min 15 min) |
| Schema contract | the `CREATE TABLE` column contract |
| Null rate | `NOT NULL` gives a zero-tolerance check; every *other* column gets a threshold proposed from its **live profile**, because the DDL says nothing about what null rate is normal and a guessed threshold is noise |
| Uniqueness, domain, range | live profile — a column that is 100% distinct today is a key; one with six values is an enum |
| SCD2 | the table's shape: a natural key plus a validity-window column pair plus a current flag is an SCD2 dimension, and the four assertions follow |
| Business rules | the agent, from the repo's own comments and the modelling it can read — always proposed, never auto-applied |

Two things about this that are load-bearing:

**Profiling requires the connection, so derivation is no longer repo-only.**
Today ingestion runs entirely off the checkout and credentials arrive last.
Data-quality thresholds cannot be derived that way — there is nothing in a
`CREATE TABLE` that says what null rate is normal. So the flow gains an
optional profiling pass after the connection is given: without it, the DQ
section still proposes checks, but with tolerances marked "needs a threshold"
rather than invented ones.

**The heuristic still runs first and always.** An absent or failing model costs
proposal quality, never the flow — the same rule ingestion and RCA already
follow. The agent's job in each section is the part rules cannot reach:
business rules, which columns actually matter, and whether a detected SCD2
shape is really one.

## 5. What this asks of the current code

Honest gap list, against the tree as of 2026-09-23.

| Gap | Where |
|---|---|
| A project can hold N connectors; nothing names one as the warehouse or one as the source | `models.py:Project`, `routers/projects.py` |
| No stage concept — `BRONZE_TO_SILVER_PARITY` is layer-agnostic but a silver→gold check is indistinguishable from a bronze→silver one | `models.py:CheckType`, `checks/config_schemas.py` |
| `CROSS_SOURCE_PARITY` compares two scalars from two queries; it cannot do key-level SOURCE→BRONZE parity | `checks/config_schemas.py:CrossSourceParityConfig`, `checks/engine.py` |
| `rationale` is computed during ingest and dropped at confirm — the Check row has nowhere to put it | `ingest/heuristic.py:CheckProposal` vs `models.py:Check` |
| SQL is assembled inside the engine at run time and never surfaced; only `BRONZE_TO_SILVER_PARITY` has a standalone builder | `checks/engine.py`, `checks/b2s_parity.py` |
| No profiling anywhere — no metric exists until a check is defined and run | — |
| ~~No SCD2~~ **done** - `SCD2_INTEGRITY` exists, derived from the table's column shape with the natural key taken from the MERGE | `checks/scd2.py` |
| No referential-integrity, reconciliation, uniqueness, domain, range or blank-rate check types | `checks/` |
| Checks are listed flat per database; there is no section grouping for the UI to render | `frontend/src/pages/DatabaseChecks.tsx` |

## 6. What the user sees

The sections in §3 are not phases of a rollout and not a sequence anyone walks
through. They are **the places inside a project**, all present from the moment
setup finishes, each independently reachable and independently useful. A user
arrives with a question — "did last night's load lose rows", "how bad is the
null rate on that column", "is the customer dimension's history intact" — and
goes straight to the section that answers it.

```
/projects/:slug                              overview: health per section
/projects/:slug/parity                       the three stages, grouped
/projects/:slug/parity?stage=SOURCE_TO_BRONZE
/projects/:slug/parity?stage=BRONZE_TO_SILVER
/projects/:slug/parity?stage=SILVER_TO_GOLD
/projects/:slug/quality                      profile + column checks
/projects/:slug/advanced                     SCD2, RI, reconciliation, rules
/projects/:slug/schema                       the column contracts
/projects/:slug/incidents                    what is currently wrong
/projects/:slug/changes                      proposed check changes
```

Every section shows the same thing for every test in it: description, logic,
SQL, current status, history. The sections differ in what they assert, not in
how they are presented or reviewed.

Two consequences worth stating, because they are easy to get wrong:

**A section is never empty without saying why.** A project with no source
connection shows SOURCE→BRONZE as *unavailable, connect a source* — not as an
empty list, which reads as "nothing to check here" and is a lie. Same for a
repo whose DDL the parser did not understand: the section names what it could
not cover, as `assess_coverage` already does for parity.

**Adding a test is done from inside its section,** with that section's form —
an SCD2 check is configured by naming the key, the window columns and the
current flag, not by picking "SCD2_INTEGRITY" out of a flat dropdown of
fifteen types. The type list is an implementation detail; the section is the
user's mental model.

### Engineering sequence

Separate question from the above, and only about the order code lands in. The
sections all ship; this is which one is built first.

1. **Three fields on every check** — `rationale` column, and a `build_sql`
   per check type. Nothing new is measured; everything existing becomes
   explicable. Every section below depends on this to render its tests.
2. **Stage on parity checks** — tag the three hops, group by stage, derive
   silver→gold from the gold MERGEs the parser already reads.
3. **The data-quality section** — profiling pass, the profile stored per run,
   and the column-level check types on top of it.
4. **The advanced section** — SCD2 first (it is the one with a detectable
   shape), then referential integrity and reconciliation, then the custom
   rule escape hatch.
5. **One project, two connections** — named warehouse/source roles, and
   SOURCE→BRONZE parity, which is the only thing the source connection is for.
   Last deliberately: it is the only migration that touches existing projects.

## 7. What the reference repos taught us

Two repositories were read while writing this: `ioi/`, the mirror of our own
test pipeline, and an FCC production repo used strictly as reference for the
kind of project this platform has to serve. The FCC one changed the design, and
the changes are worth recording because each was a case of the tool deriving
*nothing* while looking like it had derived everything.

**A pipeline's whole silver layer can be invisible to us.** FCC joins its
MERGEs with `IS NOT DISTINCT FROM` rather than `=`, because their keys are
nullable. Our ON-clause parser split on `=` only, so every such MERGE yielded
zero keys and therefore zero parity checks - and a repo written entirely in that
idiom would ingest to a short, clean-looking proposal list indistinguishable
from a simple pipeline. Fixed in `ddl_parser._normalize_key_equality`, which
rewrites the null-safe form and deliberately leaves `IS DISTINCT FROM` alone,
since reading *that* as key equality would invert the join.

**A house naming convention can cost every check.** FCC prefixes its metadata
columns: `ETL_LOADED_AT`, not `LOADED_AT`. The settle column is what separates a
row still in flight from a row that was lost, so a table whose timestamp column
is not recognised gets no parity check at all. `heuristic._find_column` now
falls back to a suffix match on an underscore boundary - but only for compound
candidates, because allowing a generic fragment like `_ID` to match made
`MEMBER_ID` register as a sequence column, which is a worse bug than the one
being fixed.

**Bronze is not always append-only.** FCC's own `docs/bronze-write-patterns.md`
documents two deliberate strategies - append-only, where silver dedups, and
MERGE-on-PK, where bronze already holds one row per entity - and states that the
parity test which works differs between them. Our engine assumed the first. The
loyalty source in `snowflake/dpm_src_loyalty/` now exercises the second so the
difference is testable rather than theoretical.

**A loader's MERGE is not a pipeline hop.** A MERGE-on-PK bronze writer merges a
table into itself, and so do maintenance statements inside stored procedures.
Deriving parity from one produces a check that compares a table to itself and
passes unconditionally - which is worse than no check, because it occupies a
slot in the coverage count and makes the report claim ground nothing covers.

**A MERGE's projection is not its insert list.** An SCD2 close statement selects
`CHANGED_AT` only to write it into `VALID_TO`. Taking every alias as a target
column produced a comparison against a column that does not exist - an ERROR
run, which says the check is wrong, rather than a FAILED one, which says the
pipeline is wrong. Shipping checks that are wrong on arrival is how people learn
to ignore the dashboard.

**Templated object names are a binding problem, not a parsing one.** FCC names
every object `BLINKFIRE_{{ env }}.SILVER.POSTS`. The instinct is to teach the
parser about Jinja; the better answer is that we hold a live warehouse
connection, so the environment should be *discovered and bound* - match the
template against the databases the credentials actually reach and let the user
confirm which one. Not yet built, and noted here so it is not built the wrong
way later.
