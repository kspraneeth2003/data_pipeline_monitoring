from typing import TypedDict

from app.checks.engine import CheckOutcome


class RcaTarget(TypedDict):
    object: str
    keyword: str | None


def extract_rca_targets(check_type: str, config: dict, outcome: CheckOutcome) -> list[RcaTarget]:
    """Figures out which object(s) an RCA should gather evidence for, and
    (when relevant) which column/keyword narrows the git search."""
    if check_type == "ROW_COUNT":
        targets: list[RcaTarget] = []
        if isinstance(config.get("object"), str):
            targets.append({"object": config["object"], "keyword": None})
        if isinstance(config.get("comparisonObject"), str):
            targets.append({"object": config["comparisonObject"], "keyword": None})
        return targets

    if check_type == "FRESHNESS" and isinstance(config.get("object"), str):
        return [{"object": config["object"], "keyword": config.get("timestampColumn") or None}]

    if check_type == "NULL_RATE" and isinstance(config.get("object"), str):
        return [{"object": config["object"], "keyword": config.get("column") or None}]

    if check_type == "SCHEMA_DRIFT" and isinstance(config.get("object"), str):
        missing = outcome.metrics.get("missing") or []
        type_mismatches = outcome.metrics.get("typeMismatches") or []
        keyword = missing[0] if missing else (type_mismatches[0].split(" ")[0] if type_mismatches else None)
        return [{"object": config["object"], "keyword": keyword}]

    if check_type == "BRONZE_TO_SILVER_PARITY":
        targets = []
        # The MERGE that populates silver is written in the *bronze* DDL file, so
        # searching bronze first puts the git pickaxe on the file most likely to
        # hold the offending change.
        mismatch_by_column = outcome.metrics.get("mismatchByColumn") or {}
        # A single disagreeing column is the strongest possible search term - it
        # is almost always a mis-mapped payload field in the MERGE's SELECT.
        keyword = next(iter(mismatch_by_column), None) if mismatch_by_column else None

        if isinstance(config.get("bronzeObject"), str):
            targets.append({"object": config["bronzeObject"], "keyword": keyword})
        if isinstance(config.get("silverObject"), str):
            targets.append({"object": config["silverObject"], "keyword": keyword})
        return targets

    return []
