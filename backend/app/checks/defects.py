"""Telling a broken check apart from a broken pipeline.

A run that errors can mean two very different things, with different owners:

* **The pipeline moved** - a column was dropped, a table renamed. That is news
  about the user's data and belongs in health, incidents and Jira.
* **The check is wrong** - SQL this application generated does not compile.
  The user did not write it and cannot fix it. Reporting it as a pipeline
  error sends them to investigate a problem that does not exist, and the RCA
  agent will confidently blame their most recent commit for it. Every false
  red square also teaches them to discount the next real one.

The product rule is that the user only ever sees problems in their data, so the
second kind is caught here, repaired where possible, and otherwise reported as
"not monitored" - never as a failure. Hiding it entirely would be wrong in the
other direction: a table whose only check is broken is unmonitored, and a page
that shows it green claims coverage nothing provides.

The most common defect, and the one this module exists for first: a MERGE's
USING clause refers to its tables by alias (`s.MEMBER_ID`, `x.INDIVIDUAL_ID`)
and derivation copied those expressions into a check that reads the source
table without that alias. `localize_parity_columns` is the single place that
rewrites MERGE expressions into ones valid against the source table alone; the
derivation rules call it so new checks are right on arrival, and the runtime
repair calls it so existing ones are fixed in place.
"""

import re
from dataclasses import dataclass, field

# `alias.column` where alias is a bare identifier. The lookbehind stops this
# matching the middle of a dotted name (DB.SCHEMA.TABLE) or a VARIANT path
# (RAW_PAYLOAD:a.b), and keeps `ts.` from reading as `s.`.
_QUALIFIED = re.compile(r'(?<![\w$.:"])([A-Za-z_][\w$]*)\.([A-Za-z_][\w$]*|"[^"]+")')
# A Snowflake Scripting bind variable, e.g. `:next_seq`. It only has a value
# inside the procedure that declared it. `::TYPE` casts and `RAW:path` lookups
# are excluded by requiring neither a word character nor a colon before it.
_BIND_VARIABLE = re.compile(r"(?<![:\w]):[A-Za-z_]\w*")
_BARE_IDENTIFIER = re.compile(r"(?<![\w$.:'\"])([A-Za-z_][\w$]*)(?![\w$(])")

# Words a bare-identifier scan would otherwise report as columns.
_SQL_WORDS = {
    "AND", "OR", "NOT", "NULL", "IS", "IN", "AS", "CASE", "WHEN", "THEN", "ELSE", "END",
    "TRUE", "FALSE", "DISTINCT", "STRING", "NUMBER", "VARCHAR", "INT", "INTEGER", "FLOAT",
    "BOOLEAN", "DATE", "TIMESTAMP", "TIMESTAMP_NTZ", "TIMESTAMP_TZ", "TIMESTAMP_LTZ",
    "VARIANT", "OBJECT", "ARRAY", "DECIMAL", "DOUBLE", "TIME", "LIKE", "ILIKE", "BETWEEN",
}


def _unquote(name: str) -> str:
    return name[1:-1] if name.startswith('"') and name.endswith('"') else name.upper()


def qualifiers(expression: str) -> dict[str, set[str]]:
    """Alias -> the columns it is used to reach, for every `alias.column` here."""
    found: dict[str, set[str]] = {}
    for alias, column in _QUALIFIED.findall(expression):
        found.setdefault(alias.upper(), set()).add(_unquote(column))
    return found


def root_columns(expression: str) -> set[str]:
    """The table columns an expression reads, with any qualifier removed.

    `s.RAW_PAYLOAD:tier::STRING` reads RAW_PAYLOAD; `COALESCE(AMOUNT, 0)` reads
    AMOUNT. Function names are excluded by the no-parenthesis lookahead, and
    VARIANT path segments by the no-colon lookbehind.
    """
    stripped = _QUALIFIED.sub(lambda m: m.group(2), expression)
    stripped = re.sub(r"'[^']*'", "''", stripped)  # string literals are not columns
    stripped = _BIND_VARIABLE.sub("", stripped)
    return {
        _unquote(name)
        for name in _BARE_IDENTIFIER.findall(stripped)
        if name.upper() not in _SQL_WORDS
    }


@dataclass
class LocalizedColumns:
    key_columns: list[dict]
    value_columns: list[dict]
    # The alias recognised as the source table and stripped, if any.
    driving_alias: str | None = None
    # Value columns removed, with why - they read a table the check cannot see.
    dropped: list[str] = field(default_factory=list)
    # Set when a *key* reads another table: no two-table parity check can be
    # built from this MERGE, so none should exist.
    blocking_reason: str | None = None

    @property
    def changed(self) -> bool:
        return bool(self.driving_alias or self.dropped or self.blocking_reason)


def _choose_driving_alias(
    columns: list[dict], source_columns: set[str] | None
) -> str | None:
    """The alias that stands for the source table, inferred from its columns.

    Used only when the MERGE's own FROM alias is not known (repairing a check
    whose derivation predates recording it). The alias whose every reference
    resolves on the source table, and which is used most, is the source; one
    that reaches columns the source lacks is a joined table or a CTE.
    """
    if not source_columns:
        return None
    usage: dict[str, set[str]] = {}
    for column in columns:
        for alias, cols in qualifiers(column["bronze"]).items():
            usage.setdefault(alias, set()).update(cols)
    candidates = [
        (len(cols), alias) for alias, cols in usage.items() if cols <= source_columns
    ]
    return max(candidates)[1] if candidates else None


def localize_parity_columns(
    key_columns: list[dict],
    value_columns: list[dict],
    *,
    driving_alias: str | None = None,
    source_columns: set[str] | None = None,
) -> LocalizedColumns:
    """Rewrite MERGE source expressions so they are valid against the source table.

    Each column is `{name, bronze, silver}`, `bronze` being the MERGE's source
    expression. The source table's own alias is stripped. Anything reaching
    another alias - a joined lookup, a CTE of rollups - or a procedure's bind
    variable cannot be evaluated against the source table at all: such a value
    column is dropped and named, and such a key means no check.

    `driving_alias` is the MERGE's FROM alias when the parser knows it;
    otherwise it is inferred from `source_columns`.
    """
    all_columns = key_columns + value_columns
    alias = (driving_alias or "").upper() or _choose_driving_alias(all_columns, source_columns)

    def foreign(expression: str) -> str | None:
        """Why an expression cannot be read from the source table, or None."""
        if _BIND_VARIABLE.search(expression):
            return "a procedure variable"
        others = sorted(a for a in qualifiers(expression) if a != alias)
        if others:
            return f"table/CTE alias {', '.join(others).lower()}"
        return None

    def strip(expression: str) -> str:
        if not alias:
            return expression
        return _QUALIFIED.sub(
            lambda m: m.group(2) if m.group(1).upper() == alias else m.group(0), expression
        )

    result = LocalizedColumns(key_columns=[], value_columns=[], driving_alias=None)
    stripped_any = False
    for column in key_columns:
        reason = foreign(column["bronze"])
        if reason:
            result.blocking_reason = (
                f"The key {column['name']} comes from {reason} (`{column['bronze']}`), "
                "not from the source table, so the two sides cannot be joined on it."
            )
            return result
        new = strip(column["bronze"])
        stripped_any |= new != column["bronze"]
        result.key_columns.append({**column, "bronze": new})

    for column in value_columns:
        reason = foreign(column["bronze"])
        if reason:
            result.dropped.append(f"{column['name']} (reads {reason})")
            continue
        new = strip(column["bronze"])
        stripped_any |= new != column["bronze"]
        result.value_columns.append({**column, "bronze": new})

    result.driving_alias = alias.lower() if (alias and stripped_any) else None
    return result


def referenced_columns(check_type: str, config: dict) -> dict[str, set[str]]:
    """Table -> the columns this check needs to exist, as its config names them.

    Used to decide whether a compile error is a pipeline change (a column the
    check needs is gone) or a check defect (every column it needs is there, so
    the SQL around them is what is wrong).
    """
    def add(table: object, *cols: object) -> None:
        if not isinstance(table, str) or not table:
            return
        bucket = needed.setdefault(table.upper(), set())
        for col in cols:
            if isinstance(col, str) and col:
                bucket.update(root_columns(col))

    needed: dict[str, set[str]] = {}
    if check_type == "FRESHNESS":
        add(config.get("object"), config.get("timestampColumn"))
    elif check_type == "NULL_RATE":
        add(config.get("object"), config.get("column"))
    elif check_type in ("ROW_COUNT", "SCHEMA_DRIFT"):
        add(config.get("object"))
        add(config.get("comparisonObject"))
    elif check_type == "SCD2_INTEGRITY":
        add(
            config.get("object"),
            *(config.get("naturalKeyColumns") or []),
            config.get("validFromColumn"),
            config.get("validToColumn"),
            config.get("currentFlagColumn"),
        )
    elif check_type == "BRONZE_TO_SILVER_PARITY":
        columns = (config.get("keyColumns") or []) + (config.get("valueColumns") or [])
        add(
            config.get("bronzeObject"),
            *(c.get("bronze") for c in columns),
            config.get("bronzeLoadedAtColumn"),
            config.get("bronzeSequenceColumn"),
        )
        add(config.get("silverObject"), *(c.get("silver") for c in columns))
    return needed


def is_compile_error(message: str | None) -> bool:
    """Snowflake rejected the statement before reading any data."""
    text = (message or "").lower()
    return "sql compilation error" in text or "syntax error" in text or "bind variable" in text


def is_missing_object(message: str | None) -> bool:
    """A table or view the check reads does not exist (or is not visible)."""
    text = (message or "").lower()
    return "does not exist or not authorized" in text


@dataclass
class Assessment:
    """What an errored run actually was."""

    # "defect": the check's SQL is wrong. "pipeline": report it as today.
    kind: str
    # For the user, when it is a defect: one plain sentence, no SQL.
    summary: str = ""
    # For whoever maintains DPM: the raw error and what was decided from it.
    diagnostics: dict = field(default_factory=dict)
    # A corrected config, when one could be built. Still to be compiled.
    repaired_config: dict | None = None
    repair_note: str | None = None


def assess_error(
    check_type: str,
    config: dict,
    message: str | None,
    live_columns: dict[str, set[str] | None],
) -> Assessment:
    """Classify an errored run. `live_columns` is table -> its columns right now
    (None for a table that could not be described).

    Order matters. A config that contains MERGE aliases is wrong regardless of
    the error text, so that is checked first - and it is the only case with a
    repair. Only then does a missing column mean the pipeline changed.
    """
    diagnostics = {"error": (message or "")[:2000]}

    if not is_compile_error(message) or is_missing_object(message):
        return Assessment(kind="pipeline", diagnostics=diagnostics)

    if check_type == "BRONZE_TO_SILVER_PARITY":
        source = str(config.get("bronzeObject") or "").upper()
        localized = localize_parity_columns(
            config.get("keyColumns") or [],
            config.get("valueColumns") or [],
            source_columns=live_columns.get(source) or None,
        )
        if localized.changed:
            diagnostics["localized"] = {
                "driving_alias": localized.driving_alias,
                "dropped": localized.dropped,
                "blocking_reason": localized.blocking_reason,
            }
            if localized.blocking_reason:
                return Assessment(
                    kind="defect",
                    summary=(
                        "Not monitored: this table's MERGE takes its key from a joined table, "
                        "which a two-table parity check cannot compare. It needs a different "
                        "kind of check."
                    ),
                    diagnostics=diagnostics,
                )
            note_parts = []
            if localized.driving_alias:
                note_parts.append(
                    f"rewrote the MERGE's `{localized.driving_alias}.` references against "
                    f"{source} itself"
                )
            if localized.dropped:
                note_parts.append(
                    "dropped value columns the source table does not hold: "
                    + "; ".join(localized.dropped)
                )
            return Assessment(
                kind="defect",
                summary="Not monitored: the check's SQL was invalid and is being repaired.",
                diagnostics=diagnostics,
                repaired_config={
                    **config,
                    "keyColumns": localized.key_columns,
                    "valueColumns": localized.value_columns,
                },
                repair_note="Automatic repair: " + ", and ".join(note_parts) + ".",
            )

    # No alias problem. Now a compile error means one of two things: a column
    # the check needs has gone (the pipeline moved - real news), or all of them
    # are there and the statement around them is wrong (ours).
    missing: list[str] = []
    for table, needed in referenced_columns(check_type, config).items():
        present = live_columns.get(table)
        if present is None:
            # Could not describe it. Not enough to call this our fault.
            return Assessment(kind="pipeline", diagnostics=diagnostics)
        missing.extend(f"{table}.{col}" for col in sorted(needed - present))
    if missing:
        diagnostics["missing_columns"] = missing
        return Assessment(kind="pipeline", diagnostics=diagnostics)

    return Assessment(
        kind="defect",
        summary=(
            "Not monitored: every column this check reads exists, but its SQL does not "
            "compile. This is a fault in the check, not in your pipeline."
        ),
        diagnostics=diagnostics,
    )
