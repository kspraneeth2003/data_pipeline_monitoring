"""Turns a parsed repository into concrete check proposals, by rule.

This is the deterministic half of repo ingestion. It runs first and always, and
its output is what the LLM is asked to improve on - so a failed, slow or absent
LLM costs quality, never the feature. That mirrors how RCA already degrades:
`graph.py` falls back from LLM synthesis to a heuristic, and ingestion should
not introduce a second, harder failure mode.

The guiding rule is that every proposal must be traceable to something the DDL
actually says. A MERGE states which rows should land where and keyed on what; a
task's SCHEDULE states how often; a CREATE TABLE states the column contract and,
where it says NOT NULL, the null contract too. Where the DDL says nothing - what
*non-zero* null rate is acceptable, which columns matter most - this module
proposes nothing, and leaves that to the LLM and the user.

The second rule is that silence is not coverage. `assess_coverage` reports the
tables this module could *not* cover and why, because a thin proposal list and a
clean pipeline look identical otherwise, and the thin list is the one that shows
up when the parser met a repo shape it does not understand.
"""

import re
from typing import TypedDict

from app.ingest.ddl_parser import ParsedColumn, ParsedMerge, ParsedRepo, ParsedTable

# Schema and table naming that marks a raw landing layer. A bronze->silver
# parity check is only meaningful when the source really is untyped landed
# data; run against two typed tables it just restates a row-count check.
LANDING_SCHEMAS = {"BRONZE", "RAW", "LANDING", "STAGE", "STAGING", "SRC", "SOURCE"}
LANDING_TABLE_SUFFIXES = ("_RAW", "_LANDING", "_STAGE")

# Column names that conventionally carry the load/update time.
LOADED_AT_COLUMNS = ("LOADED_AT", "INGESTED_AT", "LANDED_AT", "_LOADED_AT", "LOAD_TS")
UPDATED_AT_COLUMNS = ("UPDATED_AT", "MODIFIED_AT", "LAST_UPDATED", "REFRESHED_AT")
# Monotonic per-row ordering columns. `SEQ_NO`/`CHANGE_SEQ` are the CDC
# spellings: a source that emits change events numbers them, and that number
# is a better dedup ordering than any load timestamp, which records when we
# fetched rather than when the row changed and ties whenever a page lands
# several changes at once.
SEQUENCE_COLUMNS = ("RECORD_ID", "SEQ_NO", "SEQ_ID", "CHANGE_SEQ", "INGEST_ID", "_ID")

TIMESTAMP_TYPES = ("TIMESTAMP", "TIMESTAMP_NTZ", "TIMESTAMP_LTZ", "TIMESTAMP_TZ", "DATETIME")

# How far past a task's own cadence a table may fall before freshness fails.
# Generous on purpose: a check that fires on one slow run gets muted, and a
# muted check protects nothing.
FRESHNESS_SLACK = 6
MIN_FRESHNESS_MINUTES = 15

# A bronze row is allowed this long to reach silver before its absence counts
# as loss rather than ordinary pipeline latency.
DEFAULT_LAG_MINUTES = 5


class CheckProposal(TypedDict):
    key: str
    name: str
    description: str
    rationale: str
    type: str
    schedule: str
    database: str
    config: dict
    source: str
    concerns: list[str]


def _database_of(fqn: str) -> str:
    return fqn.split(".")[0]


def _schema_of(fqn: str) -> str:
    parts = fqn.split(".")
    return parts[1] if len(parts) > 2 else ""


def _table_of(fqn: str) -> str:
    return fqn.split(".")[-1]


def is_landing_object(fqn: str) -> bool:
    return _schema_of(fqn).upper() in LANDING_SCHEMAS or _table_of(fqn).upper().endswith(
        LANDING_TABLE_SUFFIXES
    )


def _find_column(table: ParsedTable | None, candidates: tuple[str, ...]) -> str | None:
    """The first of `candidates` this table has, exact match preferred.

    The fallback is a suffix match on an underscore boundary, so a house style
    that prefixes its metadata columns - `ETL_LOADED_AT`, `DW_UPDATED_AT`,
    `_ETL_LOADED_AT` - is recognised as the column it plainly is. This is not
    cosmetic: the settle column is what separates a row still in flight from a
    row that was lost, so a table whose timestamp column is not recognised gets
    no parity check at all. A repo that prefixes consistently, which is the
    common convention, would otherwise derive nothing and look like a repo with
    a simple pipeline rather than one the parser did not understand.

    Exact still wins, so a table carrying both `LOADED_AT` and `ETL_LOADED_AT`
    resolves to the unprefixed one rather than to whichever is found first.
    """
    if not table:
        return None
    names = {c["name"].upper() for c in table["columns"]}
    for candidate in candidates:
        if candidate in names:
            return candidate
    for candidate in candidates:
        # Only compound names are matched by suffix. A generic fragment like
        # `_ID` would otherwise swallow every key column in the table -
        # `MEMBER_ID` is not a sequence column, and picking it as one produces a
        # parity check that orders bronze rows by their key and calls the result
        # a sequence. Requiring an underscore inside the candidate keeps the
        # fallback to names that are already specific.
        stem = candidate.lstrip("_")
        if "_" not in stem:
            continue
        # Guarding on the boundary is what stops `UNLOADED_AT` matching
        # `LOADED_AT`.
        suffix = f"_{stem}"
        matches = sorted(name for name in names if name.endswith(suffix))
        if matches:
            return matches[0]
    return None


def _find_timestamp_column(table: ParsedTable | None) -> str | None:
    """Prefers a conventionally-named column, then any timestamp column."""
    named = _find_column(table, UPDATED_AT_COLUMNS) or _find_column(table, LOADED_AT_COLUMNS)
    if named or not table:
        return named
    for column in table["columns"]:
        if column["data_type"].upper().startswith(TIMESTAMP_TYPES):
            return column["name"]
    return None


def _freshness_minutes(cadence_minutes: float | None) -> float:
    if not cadence_minutes:
        return 90.0
    return max(MIN_FRESHNESS_MINUTES, round(cadence_minutes * FRESHNESS_SLACK))


def _tables_by_fqn(parsed: ParsedRepo) -> dict[str, ParsedTable]:
    return {t["fqn"]: t for t in parsed["tables"]}


def _payload_key(expression: str) -> str | None:
    """`RAW_PAYLOAD:warehouse::STRING` -> `warehouse`. None if not a VARIANT path."""
    match = re.search(r":([A-Za-z_][\w$]*)\s*(?:::|$)", expression)
    return match.group(1) if match else None


def _mapping_concerns(merge: ParsedMerge) -> list[str]:
    """Flags value columns whose payload key does not match the column name.

    This is the limit of deriving a check from a MERGE, and it is worth stating
    plainly: the generated check asserts that the MERGE did what the MERGE says,
    not that silver matches the source data. A MERGE reading the wrong payload
    field produces a check that agrees with it and passes.

    A name mismatch is the visible symptom of exactly that. It is not proof of a
    bug - a genuine rename looks identical - so this flags rather than fails,
    and leaves the judgement to the person reviewing the proposal.
    """
    concerns: list[str] = []
    for column in merge["value_columns"]:
        key = _payload_key(column["source_expr"])
        if key and key.upper() != column["name"].upper():
            concerns.append(
                f"{column['name']} is populated from payload key `{key}`. The names differ, so this "
                f"check will agree with the MERGE even if the MERGE reads the wrong field - confirm "
                f"`{key}` is really what the source sends."
            )
    return concerns


def _settle_column(table: ParsedTable | None, landing: bool) -> str | None:
    """The column saying when a source row became available to the MERGE.

    Parity needs this to tell a row that is *late* from a row that was *lost*:
    without it every in-flight row reads as loss and the check cries wolf on a
    healthy pipeline.

    Which column that is depends on the layer. Append-only landing tables stamp
    a load time; a typed table that is itself maintained by a MERGE stamps an
    update time instead, and using the wrong one silently shifts the settling
    window onto a column that does not advance.
    """
    if landing:
        return _find_column(table, LOADED_AT_COLUMNS) or _find_column(table, UPDATED_AT_COLUMNS)
    return _find_column(table, UPDATED_AT_COLUMNS) or _find_column(table, LOADED_AT_COLUMNS)


# The column shapes that together mark a type-2 slowly-changing dimension. A
# table needs one name from each group to qualify: a validity window and a way
# to say which version is live. Either alone is not enough - a VALID_FROM with
# no end is an event date, and an IS_CURRENT with no window is a status flag.
SCD2_VALID_FROM_COLUMNS = ("VALID_FROM", "EFFECTIVE_FROM", "START_DATE", "DBT_VALID_FROM")
SCD2_VALID_TO_COLUMNS = ("VALID_TO", "EFFECTIVE_TO", "END_DATE", "DBT_VALID_TO")
SCD2_CURRENT_FLAG_COLUMNS = ("IS_CURRENT", "IS_ACTIVE", "CURRENT_FLAG", "IS_LATEST")


class Scd2Shape(TypedDict):
    valid_from: str
    valid_to: str
    current_flag: str


def detect_scd2(table: ParsedTable | None) -> Scd2Shape | None:
    """The SCD2 column triple, if this table has one.

    Detected from column shape rather than from the MERGE, because the MERGE
    that maintains an SCD2 dimension is normally two statements - close the old
    version, open the new one - and neither on its own looks like anything in
    particular. The table, on the other hand, says plainly what it is: a
    validity window plus a current flag is not a shape that occurs by accident.

    Shape detection is a proposal, not a conclusion. It is why the checks this
    produces are offered for review rather than applied - a table can carry
    these three columns and be maintained as something else entirely, and only
    a person reading the pipeline can say so.
    """
    if not table:
        return None
    valid_from = _find_column(table, SCD2_VALID_FROM_COLUMNS)
    valid_to = _find_column(table, SCD2_VALID_TO_COLUMNS)
    current_flag = _find_column(table, SCD2_CURRENT_FLAG_COLUMNS)
    if not (valid_from and valid_to and current_flag):
        return None
    return {"valid_from": valid_from, "valid_to": valid_to, "current_flag": current_flag}


def _is_target_column(name: str, target_table: ParsedTable | None) -> bool:
    """Whether the target really has this column.

    A MERGE's USING projection is not the same list as its INSERT column list.
    Helper aliases are routine - an SCD2 close statement selects `CHANGED_AT`
    only to write it into `VALID_TO`, and a staging select carries working
    columns that are never inserted anywhere. Taking every alias as a target
    column produces a comparison against a column that does not exist, which
    fails at execution as an ERROR run rather than as a FAILED one.

    That distinction is why this filters instead of flagging. A FAILED check
    says the pipeline is wrong; an ERROR check says the check is wrong, and
    shipping checks that are wrong on arrival teaches people to ignore the
    colour of the dashboard.

    Unknown target table means no basis to exclude anything, so nothing is
    excluded - the parser not having found the CREATE TABLE is not evidence
    that a column is absent.
    """
    if target_table is None:
        return True
    return name.upper() in {c["name"].upper() for c in target_table["columns"]}


def _parity_proposal(merge: ParsedMerge, tables: dict[str, ParsedTable]) -> CheckProposal | None:
    """Key-and-value parity for one MERGE, at whatever layer it sits.

    This used to fire only when the source was a landing table, on the grounds
    that comparing two typed tables "just restates a row-count check". That was
    wrong, and it left every gold table checked by row count alone. A MERGE
    states its key in the ON clause and its column mapping in the SELECT at any
    layer; silver -> gold can lose a row or write the wrong value exactly the
    way bronze -> silver can, and a row count sees neither.

    What is genuinely bronze-specific is only that one side may be untyped
    VARIANT - and `ParityColumn` already carries an explicit expression per
    side to handle that, so it costs nothing when both sides are typed.
    """
    source, target = merge["source"], merge["target"]
    if not source or not merge["key_columns"]:
        return None

    if source == target:
        # A MERGE from a table into itself is not a pipeline hop. It is how a
        # loader upserts its own landing table - the MERGE-on-PK bronze write
        # strategy - and how maintenance statements inside stored procedures are
        # written. Deriving parity from one produces a check that compares a
        # table to itself, which passes unconditionally.
        #
        # An always-passing check is worse than an absent one: it occupies a
        # slot in the coverage count, so the report reads as "this table is
        # covered" when nothing about it is being tested.
        return None

    landing = is_landing_object(source)
    source_table = tables.get(source)
    settle_column = _settle_column(source_table, landing)
    if not settle_column:
        # No timestamp on the source means no settling window, and without one
        # every row the MERGE has not reached yet counts as loss.
        return None

    layer_phrase = "bronze -> silver" if landing else (
        f"{_schema_of(source).lower()} -> {_schema_of(target).lower()}"
    )
    concerns = _mapping_concerns(merge)

    # An SCD2 target holds one row per version of a key, so an unrestricted
    # comparison counts a key's history as duplicates of it. Restricting to the
    # current row is what makes "exactly once" true again - and the check then
    # asserts the thing worth asserting, that every source key has a live row.
    # The history's own integrity is a separate question, and gets its own
    # checks rather than being folded into this one.
    target_table = tables.get(target)
    scd2 = detect_scd2(target_table)
    target_filter = f"{scd2['current_flag']} = TRUE" if scd2 else None
    scd2_note = ""
    if scd2:
        scd2_note = (
            f" {_table_of(target)} looks like a type-2 dimension ({scd2['valid_from']}/"
            f"{scd2['valid_to']}/{scd2['current_flag']}), so only its current rows take part."
        )
        concerns.append(
            f"{_table_of(target)} was detected as an SCD2 dimension from its column shape, and "
            f"the comparison is restricted to `{scd2['current_flag']} = TRUE`. If it is really "
            f"maintained as something else, that filter is wrong and this check is measuring a "
            f"subset. The history's own integrity is checked separately."
        )

    filter_note = ""
    if merge["filter_predicate"]:
        filter_note = (
            f" The MERGE only copies rows matching `{merge['filter_predicate']}`, so the same "
            "condition is applied to the source side here."
        )
        concerns.append(
            f"The source side is filtered by `{merge['filter_predicate']}`, lifted from the "
            f"MERGE. If that condition ever stops matching what the target actually holds - "
            f"say the filter is changed in one place and not the other - this check will "
            f"report loss that is not real. Confirm it reads correctly."
        )

    return {
        "key": f"parity:{source}->{target}",
        "name": f"{_table_of(target).title().replace('_', ' ')} {layer_phrase}: dedup + parity",
        "description": (
            f"Every settled row in {source} should appear exactly once in {target}, "
            "with values intact."
        ),
        "rationale": (
            f"Derived from the MERGE in {merge['file_path']}. The key and the column "
            f"mapping are the contract that MERGE states; this check asserts it held."
            f"{filter_note}{scd2_note}"
        ),
        "type": "BRONZE_TO_SILVER_PARITY",
        "schedule": "*/10 * * * *",
        "database": _database_of(target),
        "config": {
            "bronzeObject": source,
            "silverObject": target,
            "bronzeLoadedAtColumn": settle_column,
            "bronzeSequenceColumn": _find_column(source_table, SEQUENCE_COLUMNS),
            "sourceFilter": merge["filter_predicate"],
            "silverFilter": target_filter,
            "lagMinutes": DEFAULT_LAG_MINUTES,
            "keyColumns": [
                {"name": k["name"], "bronze": k["source_expr"], "silver": k["target_expr"]}
                for k in merge["key_columns"]
            ],
            "valueColumns": [
                {"name": v["name"], "bronze": v["source_expr"], "silver": v["target_expr"]}
                for v in merge["value_columns"]
                if _is_target_column(v["target_expr"], target_table)
            ],
        },
        "source": "heuristic",
        "concerns": concerns,
    }


def _row_count_proposal(merge: ParsedMerge) -> CheckProposal | None:
    source, target = merge["source"], merge["target"]
    if not source or merge["filtered"]:
        # A filtered MERGE is *supposed* to produce fewer target rows than
        # source rows. Proposing parity there would fail forever on correct
        # data, which is the fastest way to teach someone to ignore this tool.
        return None

    return {
        "key": f"rowcount:{source}->{target}",
        "name": f"{_table_of(source).title().replace('_', ' ')}: {_schema_of(source).lower()} vs {_schema_of(target).lower()} row count",
        "description": f"Every row in {source} should show up in {target}.",
        "rationale": (
            f"The MERGE in {merge['file_path']} copies {source} into {target} with no "
            "row filter, so the two should stay in step."
        ),
        "type": "ROW_COUNT",
        "schedule": "*/5 * * * *",
        "database": _database_of(source),
        "config": {"object": source, "comparisonObject": target, "toleranceAbs": 0},
        "source": "heuristic",
        "concerns": [],
    }


def _freshness_proposal(
    target: str, tables: dict[str, ParsedTable], cadence: dict[str, float]
) -> CheckProposal | None:
    table = tables.get(target)
    timestamp_column = _find_timestamp_column(table)
    if not timestamp_column:
        return None

    cadence_minutes = cadence.get(target)
    max_age = _freshness_minutes(cadence_minutes)
    basis = (
        f"the task writing it runs every {cadence_minutes:g} minute(s), so {max_age:g} "
        f"minutes allows {FRESHNESS_SLACK} missed runs before alerting"
        if cadence_minutes
        else "no task cadence was found in the DDL, so this threshold is a default worth reviewing"
    )

    return {
        "key": f"freshness:{target}",
        "name": f"{_table_of(target).title().replace('_', ' ')} freshness",
        "description": f"{target} should be refreshed at least every {max_age:g} minutes.",
        "rationale": f"Derived from {timestamp_column} on the table; {basis}.",
        "type": "FRESHNESS",
        "schedule": "*/10 * * * *",
        "database": _database_of(target),
        "config": {
            "object": target,
            "timestampColumn": timestamp_column,
            "maxAgeMinutes": max_age,
        },
        "source": "heuristic",
        "concerns": [],
    }


def _schema_drift_proposal(table: ParsedTable) -> CheckProposal | None:
    if not table["columns"] or is_landing_object(table["fqn"]):
        # Landing tables are a VARIANT blob by design - there is no column
        # contract there to drift away from.
        return None

    return {
        "key": f"drift:{table['fqn']}",
        "name": f"{_table_of(table['fqn']).title().replace('_', ' ')}: schema drift",
        "description": f"Guards against unexpected schema changes to {table['fqn']}.",
        "rationale": (
            f"Column contract lifted from the CREATE TABLE in {table['file_path']} "
            f"({len(table['columns'])} columns)."
        ),
        "type": "SCHEMA_DRIFT",
        "schedule": "*/30 * * * *",
        "database": table["database"],
        "config": {
            "object": table["fqn"],
            "expectedColumns": [
                {"name": c["name"], "dataType": c["data_type"]} for c in table["columns"]
            ],
        },
        "source": "heuristic",
        "concerns": [],
    }


def _null_rate_proposal(table: ParsedTable, column: ParsedColumn) -> CheckProposal:
    """A NOT NULL column is a null contract the DDL states outright.

    Every other null-rate threshold is a judgement call - is 2% null on this
    column normal? - and this module deliberately does not guess at those. Zero
    is different: the DDL already said zero, so the check restates the contract
    rather than inventing one.

    Worth having even though the warehouse enforces the constraint on write:
    the constraint binds what the pipeline *can* insert, and this observes what
    the table actually holds. They come apart whenever the column is added
    later, backfilled, or the constraint is dropped in an ALTER nobody noticed
    - which is exactly the change this platform exists to catch.
    """
    return {
        "key": f"nullrate:{table['fqn']}.{column['name']}",
        "name": f"{_table_of(table['fqn']).title().replace('_', ' ')}: {column['name']} not null",
        "description": f"{column['name']} in {table['fqn']} should never be null.",
        "rationale": (
            f"The CREATE TABLE in {table['file_path']} declares {column['name']} NOT NULL, "
            "so any null is a contract violation rather than a threshold judgement."
        ),
        "type": "NULL_RATE",
        "schedule": "*/30 * * * *",
        "database": table["database"],
        "config": {"object": table["fqn"], "column": column["name"], "maxNullRatio": 0},
        "source": "heuristic",
        "concerns": [],
    }


def _scd2_proposal(
    table: ParsedTable, merges: list[ParsedMerge]
) -> CheckProposal | None:
    """The four history assertions, for a table whose shape says SCD2.

    The natural key is taken from the MERGE that maintains the dimension rather
    than from the table, because the table cannot distinguish it from the
    surrogate key - both are just columns, and picking the surrogate would make
    every assertion trivially pass. One row per "key", no window to overlap, no
    history to contradict itself: a check that always passes and says nothing.

    So no MERGE means no proposal. Guessing the natural key from naming would
    produce exactly that silently-vacuous check, and a check that cannot fail is
    more dangerous than a missing one - it makes the coverage report claim
    ground it never covered.
    """
    scd2 = detect_scd2(table)
    if not scd2:
        return None

    key_columns: list[str] = []
    for merge in merges:
        if merge["target"] == table["fqn"] and merge["key_columns"]:
            key_columns = [k["name"] for k in merge["key_columns"]]
            break
    if not key_columns:
        return None

    surrogate_note = ""
    column_names = {c["name"].upper() for c in table["columns"]}
    if any(n.endswith("_KEY") for n in column_names):
        surrogate_note = (
            " The table also has a surrogate key column; this check deliberately uses the "
            "natural key from the MERGE, since the surrogate is unique per row and would make "
            "every assertion pass without testing anything."
        )

    keys = ", ".join(key_columns)
    return {
        "key": f"scd2:{table['fqn']}",
        "name": f"{_table_of(table['fqn']).title().replace('_', ' ')}: SCD2 history integrity",
        "description": (
            f"{_table_of(table['fqn'])} keeps one row per version of {keys}. Its versions should "
            "form a clean history: one current row each, no overlaps, no gaps."
        ),
        "rationale": (
            f"{table['fqn']} has the type-2 shape - {scd2['valid_from']}/{scd2['valid_to']}/"
            f"{scd2['current_flag']} - so its correctness is a statement about versions, not "
            f"rows, and no parity check can see it. A key with two current rows makes every "
            f"join through this dimension fan out and double its measures, and nothing else "
            f"reports that.{surrogate_note}"
        ),
        "type": "SCD2_INTEGRITY",
        "schedule": "*/15 * * * *",
        "database": _database_of(table["fqn"]),
        "config": {
            "object": table["fqn"],
            "naturalKeyColumns": key_columns,
            "validFromColumn": scd2["valid_from"],
            "validToColumn": scd2["valid_to"],
            "currentFlagColumn": scd2["current_flag"],
        },
        "source": "heuristic",
        "concerns": [
            f"The open-ended marker is assumed to be the sentinel 9999-12-31. If "
            f"{scd2['valid_to']} uses NULL instead, clear `openEndedSentinel` - the convention "
            f"cannot be read off the DDL, and a table that mixes both is the fault this check "
            f"is looking for."
        ],
    }


def propose_checks(parsed: ParsedRepo) -> list[CheckProposal]:
    """Generates every check the DDL justifies, in the order a reviewer reads them."""
    tables = _tables_by_fqn(parsed)
    proposals: list[CheckProposal] = []

    for merge in parsed["merges"]:
        # Parity is attempted at every layer, not only from a landing table.
        # A row count is the fallback for a MERGE parity cannot describe -
        # no key in the ON clause, or no timestamp to settle against.
        proposal = _parity_proposal(merge, tables) or _row_count_proposal(merge)
        if proposal:
            proposals.append(proposal)

    for target in dict.fromkeys(m["target"] for m in parsed["merges"]):
        proposal = _freshness_proposal(target, tables, parsed["cadence"])
        if proposal:
            proposals.append(proposal)

    for table in parsed["tables"]:
        proposal = _scd2_proposal(table, parsed["merges"])
        if proposal:
            proposals.append(proposal)

    for table in parsed["tables"]:
        proposal = _schema_drift_proposal(table)
        if proposal:
            proposals.append(proposal)

    for table in parsed["tables"]:
        if is_landing_object(table["fqn"]):
            continue
        for column in table["columns"]:
            if not column["nullable"]:
                proposals.append(_null_rate_proposal(table, column))

    # A repo can define the same object in more than one file; the first
    # proposal for a key is the one whose file the parser already preferred.
    seen: set[str] = set()
    unique: list[CheckProposal] = []
    for proposal in proposals:
        if proposal["key"] in seen:
            continue
        seen.add(proposal["key"])
        unique.append(proposal)
    return unique


class TableCoverage(TypedDict):
    table: str
    parity: bool
    freshness: bool
    schema_drift: bool
    # Why this table has no parity check, in a sentence a reviewer can act on.
    # Empty when it has one.
    gaps: list[str]


class CoverageReport(TypedDict):
    tables_total: int
    # Tables a parity check is *expected* for: everything but the landing
    # layer, which is append-only with no upstream in this repo. Reporting
    # against all tables instead would make a fully covered pipeline read as
    # partially covered forever.
    tables_expecting_parity: int
    tables_with_parity: int
    # Only the tables missing something. A reviewer reads this to decide
    # whether the proposal set is thin because the pipeline is simple or
    # because the parser did not understand the repo.
    uncovered: list[TableCoverage]
    summary: str


def _parity_gap_reason(
    table: ParsedTable, merges_by_target: dict[str, ParsedMerge], tables: dict[str, ParsedTable]
) -> str:
    """Why parity could not be derived for one table - specific, not generic.

    "3 tables have no parity check" tells a reviewer nothing they can act on.
    "PRODUCT_STOCK_SUMMARY is written by a MERGE with no ON clause the parser
    could read" tells them whether to fix the repo, fix the parser, or write
    the check by hand.
    """
    merge = merges_by_target.get(table["fqn"])
    if merge is None:
        return (
            "No MERGE in the repository writes this table. It may be loaded by a COPY, a "
            "view, or a tool outside this repo - parity needs a stated source to compare "
            "against, so this one has to be written by hand."
        )
    if not merge["source"]:
        return (
            f"The MERGE in {merge['file_path']} does not name a single source table the "
            "parser could resolve (a join or a subquery, most likely), so there is no one "
            "object to compare against."
        )
    if not merge["key_columns"]:
        return (
            f"The MERGE in {merge['file_path']} has no ON condition the parser could read as "
            "a key. Parity is keyed comparison, so without a key it falls back to row count."
        )
    source_table = tables.get(merge["source"])
    if source_table is None:
        return (
            f"The MERGE reads {merge['source']}, which is not defined by any CREATE TABLE in "
            "this repository - so its columns, and any load timestamp, are unknown here."
        )
    return (
        f"{merge['source']} has no load or update timestamp column, so there is no way to "
        "tell a row still in flight from a row that was lost. Every in-flight row would "
        "read as loss."
    )


def assess_coverage(parsed: ParsedRepo, proposals: list[CheckProposal]) -> CoverageReport:
    """What the rules covered, and what they could not.

    Ingestion's failure mode is quiet: a repo the parser does not understand
    yields a short proposal list, and a short list is indistinguishable from a
    simple pipeline. This makes the difference explicit, so "12 checks" is
    read alongside "and 4 tables no rule could cover, for these reasons."
    """
    tables = _tables_by_fqn(parsed)
    merges_by_target = {m["target"]: m for m in parsed["merges"]}

    covered: dict[str, set[str]] = {}
    for proposal in proposals:
        config = proposal["config"]
        # A parity check covers its target; every other type covers the single
        # object it names.
        objects = (
            [config.get("silverObject")]
            if proposal["type"] == "BRONZE_TO_SILVER_PARITY"
            else [config.get("object"), config.get("comparisonObject")]
        )
        for fqn in objects:
            if isinstance(fqn, str):
                covered.setdefault(fqn, set()).add(proposal["type"])

    uncovered: list[TableCoverage] = []
    with_parity = 0
    expecting = 0
    for table in parsed["tables"]:
        # A landing table is append-only with no upstream in this repo, so it
        # is not expected to have parity and is not counted as a gap.
        if is_landing_object(table["fqn"]):
            continue
        expecting += 1

        types = covered.get(table["fqn"], set())
        if "BRONZE_TO_SILVER_PARITY" in types:
            with_parity += 1
            continue

        uncovered.append(
            {
                "table": table["fqn"],
                "parity": False,
                "freshness": "FRESHNESS" in types,
                "schema_drift": "SCHEMA_DRIFT" in types,
                "gaps": [_parity_gap_reason(table, merges_by_target, tables)],
            }
        )

    total = len(parsed["tables"])
    landing = total - expecting
    if not expecting:
        summary = (
            f"No table in this repository is expected to have a parity check "
            f"({total} parsed, all of them landing tables). That usually means the "
            "repo defines the raw layer only, or the parser did not recognise its shape."
        )
    elif not uncovered:
        summary = (
            f"Every table that should have a parity check has one "
            f"({with_parity} of {expecting}; {landing} landing table(s) exempt)."
        )
    else:
        summary = (
            f"{with_parity} of {expecting} tables have a parity check. "
            f"{len(uncovered)} could not be covered by rule and need a decision - "
            "see the reason on each."
        )

    return {
        "tables_total": total,
        "tables_expecting_parity": expecting,
        "tables_with_parity": with_parity,
        "uncovered": uncovered,
        "summary": summary,
    }


def propose_databases(parsed: ParsedRepo) -> list[dict]:
    """The databases the repo describes, each with the schemas it defines and
    the file that defines them - the per-project replacement for the hardcoded
    map in `rca/object_repo_map.py`."""
    databases: dict[str, dict] = {}
    for schema_fqn, file_path in sorted(parsed["schemas"].items()):
        database_name, schema_name = schema_fqn.split(".")
        entry = databases.setdefault(
            database_name, {"name": database_name, "repo_paths": {}, "tables": []}
        )
        entry["repo_paths"][schema_name] = file_path

    for table in parsed["tables"]:
        entry = databases.get(table["database"])
        if entry is not None:
            entry["tables"].append(table["fqn"])

    for entry in databases.values():
        layers = sorted({s.lower() for s in entry["repo_paths"]})
        entry["description"] = (
            f"{', '.join(layers)} layer{'s' if len(layers) != 1 else ''} "
            f"({len(entry['tables'])} table{'s' if len(entry['tables']) != 1 else ''}), "
            "derived from the repository."
        )
    return sorted(databases.values(), key=lambda d: d["name"])
