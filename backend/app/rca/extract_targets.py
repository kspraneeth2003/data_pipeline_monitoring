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

    return []
