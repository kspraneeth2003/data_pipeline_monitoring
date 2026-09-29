"""Reads a repository of Snowflake DDL and works out what pipeline it describes.

This is deliberately a *lexical* parser - regex and paren-balancing over the raw
SQL text - not a real grammar. That choice is worth explaining, because it is
the kind of thing a reader would reasonably try to "fix":

A full SQL parser would have to understand every Snowflake dialect extension
(VARIANT paths, `AUTOINCREMENT START n`, stored procedures whose bodies are
quoted strings containing more SQL) before it could tell us the four facts we
actually want - which databases and schemas exist, what columns a table has,
which MERGE feeds which table, and on what key. Those four facts survive
lexical extraction fine, and when extraction fails it fails *visibly*, by
finding nothing, rather than by silently mis-binding a column.

Everything here is best-effort by design. A repo that parses to nothing is a
valid outcome: the caller falls back to the LLM, and the user still gets a
review screen to correct whatever was missed.
"""

import re
from pathlib import Path
from typing import TypedDict


class ParsedColumn(TypedDict):
    name: str
    data_type: str
    # False only when the DDL says NOT NULL. A nullable column is the default
    # in Snowflake, so absence of the constraint is not evidence of intent -
    # which is why only `nullable: False` justifies a generated null-rate check.
    nullable: bool


class ParsedTable(TypedDict):
    database: str
    schema: str
    name: str
    fqn: str
    columns: list[ParsedColumn]
    comment: str | None
    file_path: str


class ColumnMapping(TypedDict):
    name: str
    source_expr: str
    target_expr: str


class ParsedMerge(TypedDict):
    target: str
    source: str | None
    key_columns: list[ColumnMapping]
    value_columns: list[ColumnMapping]
    filtered: bool
    # The business predicate that makes `filtered` true, with source-table
    # aliases stripped so it can be re-applied to the source table directly.
    # None when the MERGE copies every row. Carrying the text - not just the
    # boolean - is what lets a filtered MERGE get a parity check at all: apply
    # the same predicate to both sides and the two are comparable again.
    filter_predicate: str | None
    file_path: str


class ParsedRepo(TypedDict):
    schemas: dict[str, str]          # "DB.SCHEMA" -> repo-relative file path
    tables: list[ParsedTable]
    merges: list[ParsedMerge]
    streams: dict[str, str]          # stream FQN -> the table it reads
    cadence: dict[str, float]        # table FQN -> minutes between task runs
    files: list[str]
    warnings: list[str]


# A Snowflake identifier: bare, or double-quoted.
IDENT = r'(?:"[^"]+"|[A-Za-z_][\w$]*)'
FQN = rf"{IDENT}(?:\.{IDENT}){{0,2}}"

_LINE_COMMENT = re.compile(r"--[^\n]*")
_BLOCK_COMMENT = re.compile(r"/\*.*?\*/", re.DOTALL)

# Column-definition trailers we stop at when reading a data type.
_TYPE_STOPWORDS = re.compile(
    r"\b(?:NOT\s+NULL|NULL|DEFAULT|AUTOINCREMENT|IDENTITY|COMMENT|COLLATE|"
    r"PRIMARY\s+KEY|UNIQUE|FOREIGN\s+KEY|REFERENCES|CONSTRAINT|TAG|MASKING|WITH|START|INCREMENT|ORDER|NOORDER)\b",
    re.IGNORECASE,
)

_TABLE_CONSTRAINT = re.compile(
    r"^\s*(?:CONSTRAINT|PRIMARY\s+KEY|UNIQUE|FOREIGN\s+KEY|CHECK)\b", re.IGNORECASE
)

# `NOT NULL` on a column definition. Deliberately not `_TYPE_STOPWORDS`, which
# also matches a bare `NULL` - the two mean opposite things.
_NOT_NULL = re.compile(r"\bNOT\s+NULL\b", re.IGNORECASE)

# What ends a WHERE clause. Without this the predicate grab runs to the end of
# the SELECT and swallows `GROUP BY ...` into the filter text.
_TRAILING_CLAUSE = re.compile(
    r"\b(?:GROUP\s+BY|HAVING|QUALIFY|WINDOW|ORDER\s+BY|LIMIT|FETCH|OFFSET)\b", re.IGNORECASE
)


def strip_comments(sql: str) -> str:
    return _LINE_COMMENT.sub("", _BLOCK_COMMENT.sub("", sql))


def unquote(identifier: str) -> str:
    identifier = identifier.strip()
    if identifier.startswith('"') and identifier.endswith('"'):
        return identifier[1:-1]
    return identifier.upper()


def normalize_fqn(raw: str) -> str:
    return ".".join(unquote(p) for p in raw.strip().split("."))


def _match_paren_block(text: str, open_index: int) -> tuple[str, int] | None:
    """Returns the contents of the (...) starting at `open_index`, and the index
    just past its closing paren. Quote-aware, so a paren inside a string literal
    does not unbalance the count."""
    if open_index >= len(text) or text[open_index] != "(":
        return None
    depth, i, quote = 0, open_index, None
    while i < len(text):
        ch = text[i]
        if quote:
            if ch == quote:
                quote = None
        elif ch in ("'", '"'):
            quote = ch
        elif ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                return text[open_index + 1 : i], i + 1
        i += 1
    return None


def split_top_level(text: str, separator: str = ",") -> list[str]:
    """Splits on `separator` only at paren depth 0 and outside string literals."""
    parts: list[str] = []
    depth, quote, current = 0, None, []
    for ch in text:
        if quote:
            current.append(ch)
            if ch == quote:
                quote = None
            continue
        if ch in ("'", '"'):
            quote = ch
        elif ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        if ch == separator and depth == 0:
            parts.append("".join(current))
            current = []
        else:
            current.append(ch)
    if current:
        parts.append("".join(current))
    return [p.strip() for p in parts if p.strip()]


def _parse_columns(body: str) -> list[ParsedColumn]:
    columns: list[ParsedColumn] = []
    for raw in split_top_level(body):
        if _TABLE_CONSTRAINT.match(raw):
            continue
        match = re.match(rf"^\s*({IDENT})\s+(.*)$", raw, re.DOTALL)
        if not match:
            continue
        name = unquote(match.group(1))
        rest = match.group(2).strip()
        stop = _TYPE_STOPWORDS.search(rest)
        data_type = (rest[: stop.start()] if stop else rest).strip().rstrip(",").strip()
        if not data_type:
            continue
        columns.append(
            {
                "name": name,
                "data_type": data_type.upper(),
                "nullable": not _NOT_NULL.search(rest),
            }
        )
    return columns


def base_type(data_type: str) -> str:
    """NUMBER(38,0) -> NUMBER. Schema-drift checks compare the type family, not
    the precision, because a widened VARCHAR is not drift worth paging for."""
    return re.sub(r"\s*\(.*$", "", data_type).strip().upper()


def parse_tables(sql: str, file_path: str) -> list[ParsedTable]:
    tables: list[ParsedTable] = []
    pattern = re.compile(
        rf"CREATE\s+(?:OR\s+REPLACE\s+)?(?:TRANSIENT\s+|TEMPORARY\s+|TEMP\s+)?TABLE\s+"
        rf"(?:IF\s+NOT\s+EXISTS\s+)?({FQN})\s*(?=\()",
        re.IGNORECASE,
    )
    for match in pattern.finditer(sql):
        block = _match_paren_block(sql, match.end())
        if not block:
            continue
        body, after = block
        fqn = normalize_fqn(match.group(1))
        parts = fqn.split(".")
        if len(parts) != 3:
            continue

        comment = None
        comment_match = re.search(
            r"COMMENT\s*=\s*'((?:[^']|'')*)'", sql[after : after + 400], re.IGNORECASE
        )
        if comment_match:
            comment = comment_match.group(1).replace("''", "'")

        tables.append(
            {
                "database": parts[0],
                "schema": parts[1],
                "name": parts[2],
                "fqn": fqn,
                "columns": [
                    {
                        "name": c["name"],
                        "data_type": base_type(c["data_type"]),
                        "nullable": c["nullable"],
                    }
                    for c in _parse_columns(body)
                ],
                "comment": comment,
                "file_path": file_path,
            }
        )
    return tables


def _normalize_key_equality(condition: str) -> str:
    r"""Rewrites `a IS NOT DISTINCT FROM b` to `a = b` so one splitter handles both.

    Null-safe equality is the idiomatic MERGE join wherever a key column can be
    null, and a pipeline written against a source that emits null keys uses it
    throughout. Treating it as unparseable costs the entire check rather than
    part of one: the ON clause yields no keys, so no parity check is derived for
    that table at all, and a repo written in this style derives almost nothing
    while looking indistinguishable from a repo with a simple pipeline.

    `IS DISTINCT FROM` - the negation - is deliberately left alone. It asserts
    the two sides *differ*, so reading it as key equality would invert the join
    and compare every row against every row it is not. The `NOT` is the whole
    difference, and matching it explicitly is what keeps the two apart.
    """
    return re.sub(r"\bIS\s+NOT\s+DISTINCT\s+FROM\b", "=", condition, flags=re.IGNORECASE)


def _strip_alias(expr: str) -> str:
    """`tgt.` / `src.` prefixes carry no meaning once we know which side we are on."""
    return re.sub(r"^\s*[A-Za-z_][\w$]*\s*\.\s*", "", expr.strip())


def _strip_leading_ctes(select_body: str) -> str:
    """Drops `WITH name AS (...), other AS (...)` so the regex below finds the
    MERGE's real projection rather than the first CTE's.

    Without this, a MERGE whose USING starts with a CTE silently yields the
    CTE's one-column select - the check still builds, but compares the wrong
    columns against the wrong source table, which is worse than not building.
    """
    text = select_body
    opening = re.match(r"\s*WITH\b", text, re.IGNORECASE)
    if not opening:
        return select_body
    i = opening.end()
    while True:
        name_match = re.match(rf"\s*{IDENT}\s+AS\s*(?=\()", text[i:], re.IGNORECASE)
        if not name_match:
            break
        block = _match_paren_block(text, i + name_match.end())
        if not block:
            break
        i = block[1]
        comma = re.match(r"\s*,", text[i:])
        if not comma:
            break
        i += comma.end()
    return text[i:]


def _parse_select_list(select_body: str) -> dict[str, str]:
    """Maps output column name -> the expression that produced it.

    This is the mapping that makes a bronze->silver parity check meaningful:
    `RAW_PAYLOAD:customer_id::NUMBER AS CUSTOMER_ID` says the untyped bronze
    path on the left is the same logical value as the typed silver column on the
    right. Without it a parity check can only compare keys, which cannot see a
    MERGE that reads the wrong payload field - the keys still line up perfectly.
    """
    body = _strip_leading_ctes(select_body)
    match = re.search(r"\bSELECT\b(.*?)\bFROM\b", body, re.IGNORECASE | re.DOTALL)
    if not match:
        return {}
    mapping: dict[str, str] = {}
    for item in split_top_level(match.group(1)):
        alias_match = re.search(rf"\s+AS\s+({IDENT})\s*$", item, re.IGNORECASE | re.DOTALL)
        if alias_match:
            expr = item[: alias_match.start()].strip()
            name = unquote(alias_match.group(1))
        else:
            expr = item.strip()
            name = unquote(item.split(".")[-1])
            if not re.fullmatch(r"[\w$]+", name):
                continue
        mapping[name] = " ".join(expr.split())
    return mapping


def _first_from_object(select_body: str) -> str | None:
    matches = re.findall(rf"\bFROM\s+({FQN})", _strip_leading_ctes(select_body), re.IGNORECASE)
    return normalize_fqn(matches[0]) if matches else None


def parse_streams(sql: str) -> dict[str, str]:
    """Maps each stream to the table it reads.

    A MERGE consumes a stream, not a table, but a parity check has to compare
    against the table: the stream is a transient changelog that empties as the
    task drains it, so counting rows in it would compare "what changed since the
    last run" against "everything", and read as catastrophic loss every time.
    """
    pattern = re.compile(
        rf"CREATE\s+(?:OR\s+REPLACE\s+)?STREAM\s+(?:IF\s+NOT\s+EXISTS\s+)?({FQN})\s+ON\s+TABLE\s+({FQN})",
        re.IGNORECASE,
    )
    streams: dict[str, str] = {}
    for match in pattern.finditer(sql):
        stream = normalize_fqn(match.group(1))
        table = normalize_fqn(match.group(2))
        # `ON TABLE CUSTOMERS_RAW` inside a fully-qualified stream name is
        # relative to the stream's own schema - GET_DDL emits it that way.
        if "." not in table:
            prefix = stream.rsplit(".", 1)[0]
            table = f"{prefix}.{table}"
        streams[stream] = table
    return streams


def _strip_source_aliases(predicate: str) -> str:
    """`src.QUANTITY_ON_HAND > 0` -> `QUANTITY_ON_HAND > 0`.

    The predicate is lifted out of a MERGE, where the source carries an alias,
    and re-applied to the source table selected directly with no alias. A
    one-level `alias.` prefix is dropped; a qualified object name is not, which
    is why the prefix must be neither preceded by a dot nor followed by one -
    that leaves `DPM_SRC_CRM.BRONZE.CUSTOMERS_RAW` intact.
    """
    return re.sub(rf"(?<![.\w]){IDENT}\s*\.\s*(?={IDENT}(?!\s*\.))", "", predicate)


def _row_filter(using_body: str) -> str | None:
    """The business predicate the MERGE applies, or None if it copies every row.

    A row-count parity check is only honest when the MERGE copies every row.
    `WHERE QUANTITY_ON_HAND > 0` means target is *supposed* to be smaller than
    source, so comparing the two unfiltered would fail forever on correct data -
    the fastest way to teach someone to ignore this tool.

    Returning the text rather than a boolean is what lets such a MERGE be
    checked at all: the same predicate applied to the source side makes the two
    comparable again, and the alternative - proposing nothing - leaves every
    filtered table silently uncovered.

    Two kinds of condition are dropped, because neither shrinks the target:

    * `METADATA$ACTION = 'INSERT'` is stream mechanics, and drops nothing that
      was ever meant to land.
    * A condition containing a subquery - `CUSTOMER_ID IN (SELECT ... FROM
      changed_ids)` - is an *incremental* restriction saying which rows this
      run touches, not which rows belong in the target. The target still
      accumulates every row over time, so full parity is the correct assertion.
      It is also unusable as a source-side predicate, since the CTE it names
      exists only inside the MERGE.

    If every condition is dropped the MERGE is unfiltered after all, which is
    the difference between a gold table getting real parity and getting nothing.
    """
    where = re.search(r"\bWHERE\b(.*)$", using_body, re.IGNORECASE | re.DOTALL)
    if not where:
        return None
    body = _TRAILING_CLAUSE.split(where.group(1))[0]
    conditions = [
        condition.strip()
        for condition in re.split(r"\bAND\b", body, flags=re.IGNORECASE)
        if condition.strip()
        and "METADATA$" not in condition.upper()
        and not re.search(r"\bSELECT\b", condition, re.IGNORECASE)
    ]
    if not conditions:
        return None
    # Each condition is re-parenthesised so an OR inside one cannot capture the
    # neighbouring terms once they are re-joined with AND.
    return _strip_source_aliases(" AND ".join(f"({c})" for c in conditions)).strip()


def parse_merges(sql: str, file_path: str) -> list[ParsedMerge]:
    """Extracts MERGE INTO <target> USING (<select>) ON <keys>.

    The MERGE *is* the pipeline contract - it states which rows should land
    where, keyed on what. Lifting the contract out of it is what lets a
    generated check assert that the contract actually held, rather than assert
    some generic invented threshold.
    """
    merges: list[ParsedMerge] = []
    pattern = re.compile(
        rf"\bMERGE\s+INTO\s+({FQN})(?:\s+(?:AS\s+)?{IDENT})?\s+USING\s*(?=\()", re.IGNORECASE
    )
    for match in pattern.finditer(sql):
        block = _match_paren_block(sql, match.end())
        if not block:
            continue
        using_body, after = block

        on_match = re.search(r"\bON\b(.*?)(?=\bWHEN\b)", sql[after:], re.IGNORECASE | re.DOTALL)
        if not on_match:
            continue

        source_map = _parse_select_list(using_body)
        key_columns: list[ColumnMapping] = []
        for condition in re.split(r"\bAND\b", on_match.group(1), flags=re.IGNORECASE):
            sides = split_top_level(_normalize_key_equality(condition), "=")
            if len(sides) != 2:
                continue
            target_col = unquote(_strip_alias(sides[0]))
            source_col = unquote(_strip_alias(sides[1]))
            source_expr = source_map.get(source_col)
            if not source_expr or not re.fullmatch(r"[\w$]+", target_col):
                continue
            key_columns.append(
                {"name": target_col, "source_expr": source_expr, "target_expr": target_col}
            )

        key_names = {k["name"] for k in key_columns}
        value_columns: list[ColumnMapping] = [
            {"name": name, "source_expr": expr, "target_expr": name}
            for name, expr in source_map.items()
            if name not in key_names
        ]

        filter_predicate = _row_filter(using_body)
        merges.append(
            {
                "target": normalize_fqn(match.group(1)),
                "source": _first_from_object(using_body),
                "key_columns": key_columns,
                "value_columns": value_columns,
                "filtered": filter_predicate is not None,
                "filter_predicate": filter_predicate,
                "file_path": file_path,
            }
        )
    return merges


def parse_task_cadence(sql: str) -> dict[str, float]:
    """Maps each table a task writes to -> how often that task runs, in minutes.

    This is what lets a generated freshness check assert something real. Without
    it the only option is an invented threshold, and an invented threshold is
    either so loose it never fires or so tight it cries wolf - either way the
    check gets muted, which is worse than having no check at all.

    Cron schedules are deliberately not decoded; they return nothing and the
    caller falls back rather than guessing at a cadence it cannot read.
    """
    cadence: dict[str, float] = {}
    task_starts = [
        m.start()
        for m in re.finditer(
            r"CREATE\s+(?:OR\s+REPLACE\s+)?TASK\b", sql, re.IGNORECASE
        )
    ]
    for index, start in enumerate(task_starts):
        end = task_starts[index + 1] if index + 1 < len(task_starts) else len(sql)
        body = sql[start:end]

        schedule = re.search(r"SCHEDULE\s*=\s*'([^']*)'", body, re.IGNORECASE)
        if not schedule:
            continue
        interval = re.fullmatch(r"\s*(\d+)\s*(MINUTE|MINUTES|M)\s*", schedule.group(1), re.IGNORECASE)
        if not interval:
            continue
        minutes = float(interval.group(1))

        for target in re.findall(rf"\bMERGE\s+INTO\s+({FQN})", body, re.IGNORECASE):
            name = normalize_fqn(target)
            cadence[name] = min(cadence.get(name, minutes), minutes)
    return cadence


def parse_schemas(
    sql: str, tables: list[ParsedTable], merges: list[ParsedMerge]
) -> dict[str, int]:
    """Finds the schemas a file touches, ranked by how strongly it owns them.

    Rank matters because RCA resolves an object to a single file and then reads
    that file's git history. A silver schema is *defined* in silver.sql but also
    *written to* by the MERGE in bronze.sql; attributing it to bronze.sql would
    point every silver root-cause at the wrong file's commits.

    2 = creates the schema, 1 = defines a table in it, 0 = only merges into it.
    """
    ranked: dict[str, int] = {}

    def claim(name: str, rank: int) -> None:
        if len(name.split(".")) == 2:
            ranked[name] = max(ranked.get(name, -1), rank)

    for raw in re.findall(
        rf"CREATE\s+(?:OR\s+REPLACE\s+)?SCHEMA\s+(?:IF\s+NOT\s+EXISTS\s+)?({FQN})",
        sql,
        re.IGNORECASE,
    ):
        claim(normalize_fqn(raw), 2)
    for table in tables:
        claim(f"{table['database']}.{table['schema']}", 1)
    for merge in merges:
        parts = merge["target"].split(".")
        if len(parts) == 3:
            claim(f"{parts[0]}.{parts[1]}", 0)
    return ranked


def parse_repo(root: Path, max_files: int = 500, max_bytes: int = 2_000_000) -> ParsedRepo:
    """Walks `root` for .sql files and merges everything found into one picture."""
    schemas: dict[str, str] = {}
    schema_ranks: dict[str, int] = {}
    tables: list[ParsedTable] = []
    merges: list[ParsedMerge] = []
    streams: dict[str, str] = {}
    cadence: dict[str, float] = {}
    files: list[str] = []
    warnings: list[str] = []

    skip_dirs = {".git", "node_modules", ".venv", "venv", "__pycache__", "dist", "build", "target"}

    candidates = sorted(
        p for p in root.rglob("*.sql") if not skip_dirs & set(p.relative_to(root).parts)
    )
    if len(candidates) > max_files:
        warnings.append(
            f"Repository has {len(candidates)} .sql files; only the first {max_files} were read."
        )
        candidates = candidates[:max_files]

    for path in candidates:
        relative = path.relative_to(root).as_posix()
        try:
            if path.stat().st_size > max_bytes:
                warnings.append(f"{relative} is larger than {max_bytes} bytes and was skipped.")
                continue
            raw = path.read_text(encoding="utf-8", errors="replace")
        except OSError as error:
            warnings.append(f"Could not read {relative}: {error}")
            continue

        sql = strip_comments(raw)
        file_tables = parse_tables(sql, relative)
        file_merges = parse_merges(sql, relative)
        file_schemas = parse_schemas(sql, file_tables, file_merges)

        if not (file_tables or file_merges or file_schemas):
            continue

        files.append(relative)
        tables.extend(file_tables)
        merges.extend(file_merges)
        streams.update(parse_streams(sql))
        for target, minutes in parse_task_cadence(sql).items():
            cadence[target] = min(cadence.get(target, minutes), minutes)
        for schema, rank in file_schemas.items():
            if rank > schema_ranks.get(schema, -1):
                schema_ranks[schema] = rank
                schemas[schema] = relative

    # A MERGE reads a stream; a check has to compare against the table behind it.
    for merge in merges:
        if merge["source"] and merge["source"] in streams:
            merge["source"] = streams[merge["source"]]

    if not files:
        warnings.append("No .sql file in this repository defined any table, schema or merge.")

    return {
        "schemas": schemas,
        "tables": tables,
        "merges": merges,
        "streams": streams,
        "cadence": cadence,
        "files": files,
        "warnings": warnings,
    }
