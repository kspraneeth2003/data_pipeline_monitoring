"""Short queries that list the rows behind one finding.

The check's own statement is long because it proves four things in one
snapshot while the pipeline is writing (see b2s_parity.py). Nobody needs to read
that to answer "which 259 keys are missing?" - they need a query about the size
of the old hand-written parity checks, that they can paste into Snowflake and
get the rows. That is what this module builds.

Each query must list exactly what its metric counts, or it is a second,
disagreeing definition of the finding. So each one is built from the same
config and the same rules as the check:

* **Missing** - settled source keys with no target row. Latest-per-key does
  not change the key set, so plain DISTINCT is equivalent.
* **Extra** - target keys with no source row at all. The check's "not in the
  settled rows and not in the pending ones" is exactly that, since settled and
  pending together are every source row with a load time.
* **Value mismatches** - the source's latest row per key against the target,
  with the same null-safe comparison.
* **SCD2** - the same effective end for an open window as `scd2.py`.

They are read-only, capped at `ROW_CAP` rows, and built on read like the
check's statements, never stored.
"""

from dataclasses import dataclass

from app.checks.config_schemas import (
    BronzeToSilverParityConfig,
    NullRateConfig,
    Scd2IntegrityConfig,
)

ROW_CAP = 1000


@dataclass(frozen=True)
class Drilldown:
    # The key in a run's metrics that this query lists, so the page can offer
    # it only when that number is non-zero.
    metric: str
    label: str
    sql: str


# --- parity -------------------------------------------------------------------


def _source(config: BronzeToSilverParityConfig) -> str:
    if config.bronzeQuery:
        return f"(\n{config.bronzeQuery}\n) q"
    at = f" AT(TIMESTAMP => '{config.asOfTimestamp}'::TIMESTAMP_NTZ)" if config.asOfTimestamp else ""
    return f"{config.bronzeObject}{at}"


def _target(config: BronzeToSilverParityConfig) -> str:
    at = f" AT(TIMESTAMP => '{config.asOfTimestamp}'::TIMESTAMP_NTZ)" if config.asOfTimestamp else ""
    where = f" WHERE ({config.silverFilter})" if config.silverFilter else ""
    return f"{config.silverObject}{at}{where}"


def _and_filter(config: BronzeToSilverParityConfig) -> str:
    return f" AND ({config.sourceFilter})" if config.sourceFilter else ""


def _settled(config: BronzeToSilverParityConfig) -> str:
    return (
        f"{config.bronzeLoadedAtColumn} <= "
        f"DATEADD('second', -{int(config.lagMinutes * 60)}, CURRENT_TIMESTAMP())"
    )


def _keys(config: BronzeToSilverParityConfig, side: str) -> str:
    return ", ".join(f"{getattr(c, side)} AS {c.name}" for c in config.keyColumns)


def _key_match(config: BronzeToSilverParityConfig, left: str, right: str) -> str:
    return " AND ".join(f"{left}.{c.name} IS NOT DISTINCT FROM {right}.{c.name}" for c in config.keyColumns)


def _parity(config: BronzeToSilverParityConfig) -> list[Drilldown]:
    source, target = _source(config), _target(config)
    found = [
        Drilldown(
            "missingInSilver",
            "Missing in target",
            f"""-- Settled source keys with no row in the target
WITH src AS (
  SELECT DISTINCT {_keys(config, "bronze")}
  FROM {source}
  WHERE {_settled(config)}{_and_filter(config)}
),
tgt AS (SELECT {_keys(config, "silver")} FROM {target})
SELECT * FROM src
WHERE NOT EXISTS (SELECT 1 FROM tgt WHERE {_key_match(config, "tgt", "src")})
LIMIT {ROW_CAP};""",
        ),
        Drilldown(
            "extraInSilver",
            "Extra in target",
            f"""-- Target keys with no row in the source at all
WITH src AS (
  SELECT DISTINCT {_keys(config, "bronze")}
  FROM {source}
  WHERE {config.bronzeLoadedAtColumn} IS NOT NULL{_and_filter(config)}
),
tgt AS (SELECT DISTINCT {_keys(config, "silver")} FROM {target})
SELECT * FROM tgt
WHERE NOT EXISTS (SELECT 1 FROM src WHERE {_key_match(config, "src", "tgt")})
LIMIT {ROW_CAP};""",
        ),
        Drilldown(
            "silverDuplicateKeys",
            "Duplicate keys",
            f"""-- Target keys that appear more than once
SELECT {_keys(config, "silver")}, COUNT(*) AS ROW_COUNT
FROM {target}
GROUP BY ALL
HAVING COUNT(*) > 1
ORDER BY ROW_COUNT DESC
LIMIT {ROW_CAP};""",
        ),
    ]
    if config.valueColumns:
        order = f"{config.bronzeLoadedAtColumn} DESC" + (
            f", {config.bronzeSequenceColumn} DESC" if config.bronzeSequenceColumn else ""
        )
        src_values = ", ".join(f"{c.bronze} AS SOURCE_{c.name}" for c in config.valueColumns)
        tgt_values = ", ".join(f"{c.silver} AS TARGET_{c.name}" for c in config.valueColumns)
        side_by_side = ", ".join(f"src.SOURCE_{c.name}, tgt.TARGET_{c.name}" for c in config.valueColumns)
        differs = "\n   OR ".join(
            f"src.SOURCE_{c.name} IS DISTINCT FROM tgt.TARGET_{c.name}" for c in config.valueColumns
        )
        key_names = ", ".join(c.name for c in config.keyColumns)
        found.append(
            Drilldown(
                "valueMismatches",
                "Value mismatches",
                f"""-- Keys whose latest source row disagrees with the target, column by column
WITH src AS (
  SELECT {_keys(config, "bronze")}, {src_values}
  FROM {source}
  WHERE {_settled(config)}{_and_filter(config)}
  QUALIFY ROW_NUMBER() OVER (PARTITION BY {key_names} ORDER BY {order}) = 1
),
tgt AS (SELECT {_keys(config, "silver")}, {tgt_values} FROM {target})
SELECT {", ".join(f"src.{c.name}" for c in config.keyColumns)}, {side_by_side}
FROM src JOIN tgt ON {_key_match(config, "src", "tgt")}
WHERE {differs}
LIMIT {ROW_CAP};""",
            )
        )
    return found


# --- SCD2 ---------------------------------------------------------------------


def _scd2(config: Scd2IntegrityConfig) -> list[Drilldown]:
    keys = ", ".join(config.naturalKeyColumns)
    vf, vt, cur, obj = config.validFromColumn, config.validToColumn, config.currentFlagColumn, config.object
    # Same rule as scd2.py: an open NULL window ends in the far future, or
    # every comparison against it is NULL and reads as "no violation".
    end = f"COALESCE({vt}, '9999-12-31'::TIMESTAMP_NTZ)" if config.openEndedSentinel is None else vt
    successor = f"""SELECT * FROM (
  SELECT {keys}, {vf}, {vt},
         LEAD({vf}) OVER (PARTITION BY {keys} ORDER BY {vf}, {end}) AS NEXT_{vf}
  FROM {obj}
)"""
    return [
        Drilldown(
            "keysWithNoCurrent",
            "Keys with no current row",
            f"""-- Keys that have versions but no current one
SELECT {keys}, COUNT(*) AS VERSIONS
FROM {obj}
GROUP BY {keys}
HAVING COUNT_IF({cur}) = 0
LIMIT {ROW_CAP};""",
        ),
        Drilldown(
            "keysWithManyCurrent",
            "Keys with several current rows",
            f"""-- Keys with more than one current version
SELECT {keys}, COUNT_IF({cur}) AS CURRENT_ROWS, COUNT(*) AS VERSIONS
FROM {obj}
GROUP BY {keys}
HAVING COUNT_IF({cur}) > 1
LIMIT {ROW_CAP};""",
        ),
        Drilldown(
            "overlappingVersions",
            "Overlapping versions",
            f"""-- Versions that overlap the next version of the same key
{successor}
WHERE NEXT_{vf} < {end}
LIMIT {ROW_CAP};""",
        ),
        Drilldown(
            "gappedVersions",
            "Gaps between versions",
            f"""-- Versions followed by a gap before the next version of the same key
{successor}
WHERE NEXT_{vf} > {end}
LIMIT {ROW_CAP};""",
        ),
        Drilldown(
            "invalidWindows",
            "Malformed windows",
            f"""-- Versions whose window ends before it starts
SELECT {keys}, {vf}, {vt}
FROM {obj}
WHERE {vf} >= {end}
LIMIT {ROW_CAP};""",
        ),
    ]


# --- null rate ----------------------------------------------------------------


def _null_rate(config: NullRateConfig) -> list[Drilldown]:
    return [
        Drilldown(
            "nulls",
            f"Rows with no {config.column}",
            f"""-- Rows where {config.column} is null
SELECT *
FROM {config.object}
WHERE {config.column} IS NULL
LIMIT {ROW_CAP};""",
        )
    ]


def build_drilldowns(check_type: str, config: dict) -> list[Drilldown]:
    if check_type == "BRONZE_TO_SILVER_PARITY":
        return _parity(BronzeToSilverParityConfig.model_validate(config))
    if check_type == "SCD2_INTEGRITY":
        return _scd2(Scd2IntegrityConfig.model_validate(config))
    if check_type == "NULL_RATE":
        return _null_rate(NullRateConfig.model_validate(config))
    # Freshness, row count, schema drift and cross-source parity report one
    # number or a list already on the run; there are no rows to list.
    return []


def try_build_drilldowns(check_type: str, config: dict) -> list[Drilldown]:
    """For list and detail views: an unbuildable config offers no queries,
    rather than failing the page. Its statements already carry the error."""
    try:
        return build_drilldowns(check_type, config)
    except Exception:  # noqa: BLE001
        return []
