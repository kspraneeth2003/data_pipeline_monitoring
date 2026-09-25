"""Which pipeline hop a check watches, derived from what it compares.

The project page groups checks by the movement they assert - staging into
bronze, bronze into silver, silver into gold - because that is how a failure is
reasoned about ("the silver load broke"), not by the database the check row
happens to be stored under. A parity check spans two databases, so filing it
under one of them was always half a lie.

Stage is stored on the row rather than computed in the frontend. Inferring it
at render time would mean every list view re-deriving it, no way to filter or
count server-side, and no way for a person to correct a bad guess. Derivation
happens once, on write, and `stage_locked` records that a human has overruled
it so a later config edit does not silently move the check to another tab.
"""

import re

from app.models import CheckStage

# The layer a schema name denotes. Keys are matched against the schema part of
# a qualified object name (DB.SCHEMA.TABLE), upper-cased.
_LAYER_BY_SCHEMA = {
    "STG": "STG",
    "STAGE": "STG",
    "STAGING": "STG",
    "RAW": "STG",
    "LANDING": "STG",
    "BRONZE": "BRONZE",
    "SILVER": "SILVER",
    "GOLD": "GOLD",
    "MART": "GOLD",
    "MARTS": "GOLD",
}

# The hop each ordered layer pair names. Pairs that skip a layer (stg -> silver,
# bronze -> gold) are deliberately absent: a check comparing those is not one of
# the three hops, and guessing which tab it belongs to would be worse than
# leaving it in data quality where its single-table siblings are.
_STAGE_BY_HOP = {
    ("STG", "BRONZE"): CheckStage.STG_TO_BRONZE,
    ("BRONZE", "SILVER"): CheckStage.BRONZE_TO_SILVER,
    ("SILVER", "GOLD"): CheckStage.SILVER_TO_GOLD,
}

# Which config keys hold the source and target of a comparison, per check type.
# A type absent from here is single-object by construction and cannot describe a
# hop at all.
_OBJECT_PAIR_BY_TYPE = {
    "BRONZE_TO_SILVER_PARITY": ("bronzeObject", "silverObject"),
    "ROW_COUNT": ("object", "comparisonObject"),
}

# `CROSS_SOURCE_PARITY` stores two whole queries rather than two object names,
# so its layers have to be read out of the SQL. A three-part qualified name is
# specific enough not to match anything else in a SELECT.
_QUALIFIED_NAME = re.compile(r"\b([A-Za-z_][\w$]*)\.([A-Za-z_][\w$]*)\.([A-Za-z_][\w$]*)\b")


def layer_of(object_name: str | None) -> str | None:
    """The pipeline layer a qualified object sits in, or None if unreadable.

    Reads the schema, not the table: `DPM_SRC_CRM.BRONZE.CUSTOMERS_RAW` is
    bronze because of `BRONZE`, and the `_RAW` suffix on the table is a naming
    habit that would say the opposite if it were trusted.
    """
    if not object_name:
        return None
    parts = [p.strip().strip('"') for p in str(object_name).split(".")]
    if len(parts) < 2:
        return None
    return _LAYER_BY_SCHEMA.get(parts[-2].upper())


def _layer_in_query(query: str | None) -> str | None:
    """The single layer a query reads from, or None if it reads several.

    A query touching both silver and gold names no one hop, and picking the
    first match would make the tab it lands in depend on join order.
    """
    if not query:
        return None
    layers = {
        layer
        for match in _QUALIFIED_NAME.finditer(str(query))
        if (layer := _LAYER_BY_SCHEMA.get(match.group(2).upper()))
    }
    return layers.pop() if len(layers) == 1 else None


def derive_stage(check_type: str, config: dict | None) -> str:
    """The stage a check belongs to, falling back to data quality.

    Data quality is the fallback rather than an "unknown" bucket because it is
    where a check with no hop honestly belongs - a null rate on one table is a
    quality assertion whichever layer that table is in. The three hop tabs stay
    exactly what their names promise: checks that compare two layers.
    """
    config = config or {}

    if check_type == "CROSS_SOURCE_PARITY":
        source = _layer_in_query(config.get("primaryQuery"))
        target = _layer_in_query(config.get("secondaryQuery"))
    else:
        pair = _OBJECT_PAIR_BY_TYPE.get(check_type)
        if not pair:
            return CheckStage.DATA_QUALITY.value
        source, target = layer_of(config.get(pair[0])), layer_of(config.get(pair[1]))

    if source is None or target is None:
        return CheckStage.DATA_QUALITY.value

    stage = _STAGE_BY_HOP.get((source, target)) or _STAGE_BY_HOP.get((target, source))
    return (stage or CheckStage.DATA_QUALITY).value


def stage_for(check_type: str, config: dict | None, current: str | None, locked: bool) -> str:
    """The stage to store, honouring a human override.

    A person who moved a check to another tab meant it. Re-deriving on the next
    config edit would undo that silently, which is the kind of change nobody
    can find afterwards.
    """
    if locked and current:
        return current
    return derive_stage(check_type, config)
