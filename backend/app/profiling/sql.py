"""The SQL a profile runs, built from the table's own column list.

Two statements per run. The first reads the column list from
`INFORMATION_SCHEMA.COLUMNS`, so a profile follows the table as it changes
rather than a list someone typed once. The second computes every aggregate for
every column in **one** statement, for the same reason the parity check does:
Snowflake evaluates a statement against one snapshot, so the null count of
column A and the row count it is divided by describe the same instant. Twenty
round-trips would describe twenty.

Text columns are profiled by *length*, never by value. `MIN(email)` would put
a real customer's address into this app's database, and the profile history
would become a copy of the data it exists to describe.

Object names are interpolated into SQL, so they are validated first: only
plain unquoted identifiers are accepted. A table name that needs quoting is
refused with a clear error rather than half-supported.
"""

import re
from dataclasses import dataclass

_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_$]*$")

NUMERIC = "NUMERIC"
TEXT = "TEXT"
TEMPORAL = "TEMPORAL"
BOOLEAN = "BOOLEAN"
OTHER = "OTHER"

_NUMERIC_TYPES = {"NUMBER", "DECIMAL", "NUMERIC", "INT", "INTEGER", "BIGINT", "SMALLINT",
                  "TINYINT", "BYTEINT", "FLOAT", "FLOAT4", "FLOAT8", "DOUBLE", "DOUBLE PRECISION", "REAL"}
_TEXT_TYPES = {"TEXT", "VARCHAR", "STRING", "CHAR", "CHARACTER", "NCHAR", "NVARCHAR"}
# TIME is left out: it has no epoch, and a time-of-day range is rarely what
# anyone means by drift.
_TEMPORAL_TYPES = {"DATE", "DATETIME", "TIMESTAMP", "TIMESTAMP_NTZ", "TIMESTAMP_LTZ", "TIMESTAMP_TZ"}


def type_family(data_type: str) -> str:
    """Which aggregates make sense for a column, from its declared type."""
    base = data_type.upper().split("(")[0].strip()
    if base in _NUMERIC_TYPES:
        return NUMERIC
    if base in _TEXT_TYPES:
        return TEXT
    if base in _TEMPORAL_TYPES:
        return TEMPORAL
    if base == "BOOLEAN":
        return BOOLEAN
    # VARIANT, OBJECT, ARRAY, BINARY, GEOGRAPHY, VECTOR, TIME: nulls only.
    return OTHER


@dataclass(frozen=True)
class TableRef:
    database: str
    schema: str
    table: str

    @property
    def qualified(self) -> str:
        return f"{self.database}.{self.schema}.{self.table}"


def parse_object(name: str) -> TableRef:
    """Split and validate DB.SCHEMA.TABLE. Raises ValueError on anything else."""
    parts = [p.strip() for p in (name or "").split(".")]
    if len(parts) != 3:
        raise ValueError(f"Expected DATABASE.SCHEMA.TABLE, got {name!r}")
    for part in parts:
        if not _IDENTIFIER.match(part):
            raise ValueError(
                f"{part!r} is not a plain identifier. Quoted or mixed-case names are not supported"
            )
    return TableRef(*(p.upper() for p in parts))


def columns_sql(ref: TableRef) -> str:
    return (
        f"SELECT COLUMN_NAME, DATA_TYPE, ORDINAL_POSITION "
        f"FROM {ref.database}.INFORMATION_SCHEMA.COLUMNS "
        f"WHERE TABLE_SCHEMA = '{ref.schema}' AND TABLE_NAME = '{ref.table}' "
        f"ORDER BY ORDINAL_POSITION"
    )


def tables_sql(database: str) -> str:
    """Every base table in a database, for "profile all of it"."""
    if not _IDENTIFIER.match(database):
        raise ValueError(f"{database!r} is not a plain identifier")
    return (
        f"SELECT TABLE_SCHEMA, TABLE_NAME FROM {database.upper()}.INFORMATION_SCHEMA.TABLES "
        f"WHERE TABLE_TYPE = 'BASE TABLE' AND TABLE_SCHEMA <> 'INFORMATION_SCHEMA' "
        f"ORDER BY TABLE_SCHEMA, TABLE_NAME"
    )


@dataclass(frozen=True)
class ColumnSpec:
    name: str
    data_type: str
    ordinal: int

    @property
    def family(self) -> str:
        return type_family(self.data_type)


def _quote(column: str) -> str:
    # INFORMATION_SCHEMA returns the stored name, so a column created as
    # "order date" comes back with its space. Quoting it verbatim is correct
    # for every name, plain or not.
    return '"' + column.replace('"', '""') + '"'


def aggregate_aliases(index: int) -> dict[str, str]:
    """The result-column names for one profiled column, by metric."""
    return {
        "nulls": f"C{index}_NULLS",
        "distinct": f"C{index}_DISTINCT",
        "blanks": f"C{index}_BLANKS",
        "min": f"C{index}_MIN",
        "max": f"C{index}_MAX",
        "mean": f"C{index}_MEAN",
        "min_display": f"C{index}_MIN_DISPLAY",
        "max_display": f"C{index}_MAX_DISPLAY",
    }


def profile_sql(ref: TableRef, columns: list[ColumnSpec]) -> str:
    """One statement computing the row count and every column's aggregates.

    Distinct counts use `APPROX_COUNT_DISTINCT` (HyperLogLog, ~1.6% error).
    An exact `COUNT(DISTINCT ...)` per column on a wide table is the most
    expensive thing a profile could do, and the question a distinct count
    answers here - did cardinality move - survives a percent of noise.
    """
    parts = ["COUNT(*) AS ROW_COUNT"]
    for i, col in enumerate(columns):
        q = _quote(col.name)
        a = aggregate_aliases(i)
        parts.append(f"COUNT_IF({q} IS NULL) AS {a['nulls']}")
        family = col.family
        if family != OTHER:
            parts.append(f"APPROX_COUNT_DISTINCT({q}) AS {a['distinct']}")
        if family == NUMERIC:
            parts += [
                f"MIN({q})::FLOAT AS {a['min']}",
                f"MAX({q})::FLOAT AS {a['max']}",
                f"AVG({q})::FLOAT AS {a['mean']}",
            ]
        elif family == TEXT:
            parts += [
                f"COUNT_IF(TRIM({q}) = '') AS {a['blanks']}",
                f"MIN(LENGTH({q}))::FLOAT AS {a['min']}",
                f"MAX(LENGTH({q}))::FLOAT AS {a['max']}",
                f"AVG(LENGTH({q}))::FLOAT AS {a['mean']}",
            ]
        elif family == TEMPORAL:
            parts += [
                f"DATE_PART(EPOCH_SECOND, MIN({q})::TIMESTAMP_NTZ)::FLOAT AS {a['min']}",
                f"DATE_PART(EPOCH_SECOND, MAX({q})::TIMESTAMP_NTZ)::FLOAT AS {a['max']}",
                f"TO_VARCHAR(MIN({q})) AS {a['min_display']}",
                f"TO_VARCHAR(MAX({q})) AS {a['max_display']}",
            ]
        elif family == BOOLEAN:
            parts.append(f"(COUNT_IF({q}) / NULLIF(COUNT({q}), 0))::FLOAT AS {a['mean']}")
    select = ",\n  ".join(parts)
    return f"SELECT\n  {select}\nFROM {ref.qualified}"
