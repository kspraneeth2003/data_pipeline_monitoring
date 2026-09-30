"""Deciding what, in a profile, is worth a person's attention.

Pure functions over plain data: no database, no warehouse. Everything a
detector decides is a function of the snapshot in front of it and the
snapshots before it, which is what makes it testable and what makes the same
data always produce the same verdict.

**Why median and MAD, not mean and standard deviation.** The baseline is
built from the column's own past, and the past contains the incidents. One
run where a load half-failed drags a mean a long way and inflates a standard
deviation enough to hide the next failure. The median ignores it and the
median absolute deviation (MAD) barely notices it, so a single bad run cannot
teach the detector that bad is normal.

**Why every band has a floor.** A column that has been exactly 0% null for
thirty runs has a MAD of zero, and a zero-width band flags 0.01% as an
anomaly. Each metric has an absolute floor (and some a relative one) below
which movement is not worth reporting, because an alert on noise costs the
reader's trust in every alert after it.

**Why row volume is judged on the change, not the count.** An append-only
table grows every run, so its count is never near its own median. The change
between runs is what is stable - "it usually lands about 500 rows" - and a
change of -3,000 on a table that has only ever grown is the loud one.

**Nothing is judged until there is a baseline.** Under `MIN_HISTORY` prior
runs, history anomalies are not computed at all. Findings - the checks that
need no history - still run from the first pass.
"""

from dataclasses import dataclass, field
from statistics import median

from app.profiling.models import AnomalyKind, AnomalySeverity
from app.profiling.sql import BOOLEAN, NUMERIC, OTHER, TEMPORAL, TEXT

MIN_HISTORY = 5
HISTORY_WINDOW = 30
# Robust z threshold. 1.4826 * MAD estimates one standard deviation for
# normally distributed data, so K=4 is roughly "four sigma" without the
# assumption that any of this is normally distributed.
K = 4.0
MAD_SCALE = 1.4826
# Distinct ratios on a handful of rows swing wildly on one value.
MIN_ROWS_FOR_RATIOS = 20
FUTURE_SLACK_SECONDS = 86_400


@dataclass
class ColumnMetrics:
    name: str
    data_type: str
    family: str
    row_count: int
    null_count: int
    distinct_count: int | None = None
    blank_count: int | None = None
    min_numeric: float | None = None
    max_numeric: float | None = None
    mean_numeric: float | None = None

    @property
    def non_null(self) -> int:
        return self.row_count - self.null_count

    @property
    def null_ratio(self) -> float | None:
        return self.null_count / self.row_count if self.row_count else None

    @property
    def blank_ratio(self) -> float | None:
        if self.blank_count is None or not self.non_null:
            return None
        return self.blank_count / self.non_null

    @property
    def distinct_ratio(self) -> float | None:
        if self.distinct_count is None or self.non_null < MIN_ROWS_FOR_RATIOS:
            return None
        # APPROX_COUNT_DISTINCT can overshoot the true count slightly.
        return min(self.distinct_count / self.non_null, 1.0)


@dataclass
class Snapshot:
    row_count: int
    columns: dict[str, ColumnMetrics] = field(default_factory=dict)


@dataclass
class Draft:
    kind: str
    metric: str
    severity: str
    message: str
    column_name: str | None = None
    observed: float | None = None
    expected: float | None = None
    lower: float | None = None
    upper: float | None = None


def _mad(values: list[float], center: float) -> float:
    return median(abs(v - center) for v in values)


def _band(values: list[float], abs_floor: float, rel_floor: float = 0.0) -> tuple[float, float]:
    """(median, tolerance) for a series, with the tolerance never below its floors."""
    center = median(values)
    tolerance = max(K * MAD_SCALE * _mad(values, center), abs_floor, rel_floor * abs(center))
    return center, tolerance


def _pct(ratio: float) -> str:
    return f"{ratio * 100:.1f}%"


def _num(value: float) -> str:
    if abs(value) >= 1000 or value == int(value):
        return f"{value:,.0f}"
    return f"{value:,.2f}"


def _severity(deviation: float, tolerance: float) -> str:
    return AnomalySeverity.HIGH.value if abs(deviation) > 2 * tolerance else AnomalySeverity.MEDIUM.value


def _banded(
    *,
    column: str | None,
    metric: str,
    observed: float,
    history: list[float],
    abs_floor: float,
    rel_floor: float = 0.0,
    describe,
) -> Draft | None:
    center, tolerance = _band(history, abs_floor, rel_floor)
    deviation = observed - center
    if abs(deviation) <= tolerance:
        return None
    return Draft(
        kind=AnomalyKind.ANOMALY.value,
        metric=metric,
        severity=_severity(deviation, tolerance),
        column_name=column,
        observed=observed,
        expected=center,
        lower=center - tolerance,
        upper=center + tolerance,
        message=describe(observed, center),
    )


def _findings(current: Snapshot, history: list[Snapshot], now_epoch: float) -> list[Draft]:
    drafts: list[Draft] = []
    for col in current.columns.values():
        if current.row_count and col.null_count == current.row_count:
            prior = [
                h.columns[col.name].null_ratio
                for h in history
                if col.name in h.columns and h.columns[col.name].null_ratio is not None
            ]
            if prior and median(prior) < 0.5:
                # It used to hold values. That is a change, not a property.
                drafts.append(Draft(
                    kind=AnomalyKind.ANOMALY.value,
                    metric="null_ratio",
                    severity=AnomalySeverity.HIGH.value,
                    column_name=col.name,
                    observed=1.0,
                    expected=median(prior),
                    message=(
                        f"{col.name} is now NULL on every row ({current.row_count:,}); "
                        f"it is usually {_pct(median(prior))} null."
                    ),
                ))
            else:
                drafts.append(Draft(
                    kind=AnomalyKind.FINDING.value,
                    metric="all_null",
                    severity=AnomalySeverity.MEDIUM.value,
                    column_name=col.name,
                    observed=1.0,
                    message=(
                        f"{col.name} is NULL on all {current.row_count:,} rows. A column that is "
                        "never populated is usually a mapping bug (a MERGE reading a field that "
                        "does not exist) or a column nothing writes. Acknowledge it if that is intended."
                    ),
                ))
        if (
            col.family == TEMPORAL
            and col.max_numeric is not None
            and col.max_numeric > now_epoch + FUTURE_SLACK_SECONDS
        ):
            drafts.append(Draft(
                kind=AnomalyKind.FINDING.value,
                metric="future_timestamp",
                severity=AnomalySeverity.MEDIUM.value,
                column_name=col.name,
                observed=col.max_numeric,
                message=(
                    f"{col.name} holds a value more than a day in the future. Usually a timezone "
                    "or unit error, or a placeholder date."
                ),
            ))
    return drafts


def _schema_changes(current: Snapshot, previous: Snapshot) -> list[Draft]:
    drafts: list[Draft] = []
    for name in current.columns.keys() - previous.columns.keys():
        drafts.append(Draft(
            kind=AnomalyKind.SCHEMA.value, metric="column_added", severity=AnomalySeverity.LOW.value,
            column_name=name, message=f"Column {name} ({current.columns[name].data_type}) appeared since the last run.",
        ))
    for name in previous.columns.keys() - current.columns.keys():
        drafts.append(Draft(
            kind=AnomalyKind.SCHEMA.value, metric="column_removed", severity=AnomalySeverity.HIGH.value,
            column_name=name,
            message=f"Column {name} disappeared since the last run. Anything reading it will now fail.",
        ))
    for name in current.columns.keys() & previous.columns.keys():
        before, after = previous.columns[name].data_type, current.columns[name].data_type
        if before.upper() != after.upper():
            drafts.append(Draft(
                kind=AnomalyKind.SCHEMA.value, metric="type_changed", severity=AnomalySeverity.MEDIUM.value,
                column_name=name, message=f"Column {name} changed type from {before} to {after}.",
            ))
    return drafts


def _row_volume(current: Snapshot, history: list[Snapshot]) -> list[Draft]:
    counts = [h.row_count for h in history]
    previous = counts[-1]

    if current.row_count == 0 and previous > 0:
        return [Draft(
            kind=AnomalyKind.ANOMALY.value, metric="row_count", severity=AnomalySeverity.HIGH.value,
            observed=0, expected=previous,
            message=f"The table is empty. It held {previous:,} rows on the previous run.",
        )]

    deltas = [b - a for a, b in zip(counts, counts[1:])]
    if len(deltas) < MIN_HISTORY - 1:
        return []
    delta = current.row_count - previous
    center, tolerance = _band(deltas, abs_floor=max(10.0, 0.05 * previous))
    deviation = delta - center
    if abs(deviation) <= tolerance:
        return []
    only_grew = all(d >= 0 for d in deltas)
    severity = (
        AnomalySeverity.HIGH.value if (delta < 0 and only_grew) else _severity(deviation, tolerance)
    )
    lost = " Rows were removed from a table that has only ever grown." if delta < 0 and only_grew else ""
    return [Draft(
        kind=AnomalyKind.ANOMALY.value, metric="row_volume", severity=severity,
        observed=delta, expected=center, lower=center - tolerance, upper=center + tolerance,
        message=(
            f"Row count changed by {delta:+,} since the last run (now {current.row_count:,}); "
            f"a typical run changes it by {center:+,.0f}.{lost}"
        ),
    )]


def _column_anomalies(col: ColumnMetrics, past: list[ColumnMetrics], skip_null_ratio: bool) -> list[Draft]:
    drafts: list[Draft] = []
    name = col.name

    def series(attr: str) -> list[float]:
        return [v for p in past if (v := getattr(p, attr)) is not None]

    def add(draft: Draft | None) -> None:
        if draft:
            drafts.append(draft)

    if not skip_null_ratio and col.null_ratio is not None and len(s := series("null_ratio")) >= MIN_HISTORY:
        add(_banded(
            column=name, metric="null_ratio", observed=col.null_ratio, history=s, abs_floor=0.02,
            describe=lambda o, c: f"{name} is {_pct(o)} null; it is usually {_pct(c)}.",
        ))

    if col.family == TEXT and col.blank_ratio is not None and len(s := series("blank_ratio")) >= MIN_HISTORY:
        add(_banded(
            column=name, metric="blank_ratio", observed=col.blank_ratio, history=s, abs_floor=0.02,
            describe=lambda o, c: (
                f"{_pct(o)} of {name} values are empty strings; usually {_pct(c)}. "
                "Blank strings pass every NOT NULL constraint."
            ),
        ))

    if col.family != OTHER and col.distinct_ratio is not None and len(s := series("distinct_ratio")) >= MIN_HISTORY:
        center = median(s)
        add(_banded(
            column=name, metric="distinct_ratio", observed=col.distinct_ratio, history=s, abs_floor=0.05,
            describe=lambda o, c: (
                f"{name} has stopped being unique: {_pct(o)} of values are distinct, against {_pct(c)} before."
                if center >= 0.99 and o < c
                else f"{name} cardinality moved: {_pct(o)} of values are distinct, usually {_pct(c)}."
            ),
        ))

    if col.family == NUMERIC:
        mins, maxes = series("min_numeric"), series("max_numeric")
        if col.min_numeric is not None and len(mins) >= MIN_HISTORY and col.min_numeric < 0 <= min(mins):
            drafts.append(Draft(
                kind=AnomalyKind.ANOMALY.value, metric="min", severity=AnomalySeverity.HIGH.value,
                column_name=name, observed=col.min_numeric, expected=min(mins),
                message=f"{name} has a negative value ({_num(col.min_numeric)}) for the first time.",
            ))
        elif len(mins) >= MIN_HISTORY and len(maxes) >= MIN_HISTORY:
            low, high = min(mins), max(maxes)
            span = (high - low) or abs(high) or 1.0
            if col.max_numeric is not None and col.max_numeric > high + 0.5 * span:
                drafts.append(Draft(
                    kind=AnomalyKind.ANOMALY.value, metric="max", severity=AnomalySeverity.MEDIUM.value,
                    column_name=name, observed=col.max_numeric, expected=high, upper=high + 0.5 * span,
                    message=f"{name} reached {_num(col.max_numeric)}, far beyond anything seen before ({_num(high)}).",
                ))
            if col.min_numeric is not None and col.min_numeric < low - 0.5 * span:
                drafts.append(Draft(
                    kind=AnomalyKind.ANOMALY.value, metric="min", severity=AnomalySeverity.MEDIUM.value,
                    column_name=name, observed=col.min_numeric, expected=low, lower=low - 0.5 * span,
                    message=f"{name} fell to {_num(col.min_numeric)}, far below anything seen before ({_num(low)}).",
                ))
        if col.mean_numeric is not None and len(s := series("mean_numeric")) >= MIN_HISTORY:
            add(_banded(
                column=name, metric="mean", observed=col.mean_numeric, history=s, abs_floor=1e-9, rel_floor=0.1,
                describe=lambda o, c: f"The average {name} is {_num(o)}; it is usually {_num(c)}.",
            ))

    elif col.family == TEXT and col.mean_numeric is not None and len(s := series("mean_numeric")) >= MIN_HISTORY:
        add(_banded(
            column=name, metric="mean_length", observed=col.mean_numeric, history=s, abs_floor=1.0, rel_floor=0.2,
            describe=lambda o, c: (
                f"{name} values average {o:.1f} characters; usually {c:.1f}. "
                "A shift like this is often truncation or a different field being loaded."
            ),
        ))

    elif col.family == BOOLEAN and col.mean_numeric is not None and len(s := series("mean_numeric")) >= MIN_HISTORY:
        add(_banded(
            column=name, metric="true_ratio", observed=col.mean_numeric, history=s, abs_floor=0.05,
            describe=lambda o, c: f"{name} is TRUE on {_pct(o)} of rows; usually {_pct(c)}.",
        ))

    elif col.family == TEMPORAL and col.max_numeric is not None and past and past[-1].max_numeric is not None:
        if col.max_numeric < past[-1].max_numeric and len(past) >= MIN_HISTORY:
            drafts.append(Draft(
                kind=AnomalyKind.ANOMALY.value, metric="max_went_backwards", severity=AnomalySeverity.MEDIUM.value,
                column_name=name, observed=col.max_numeric, expected=past[-1].max_numeric,
                message=(
                    f"The latest {name} is earlier than on the previous run. Recent rows were "
                    "deleted, or the table was reloaded from an older extract."
                ),
            ))

    return drafts


def detect(current: Snapshot, history: list[Snapshot], now_epoch: float) -> list[Draft]:
    """Everything worth reporting about `current`, given `history` oldest-first."""
    history = history[-HISTORY_WINDOW:]
    drafts = _findings(current, history, now_epoch)

    if history:
        drafts += _schema_changes(current, history[-1])

    if len(history) >= MIN_HISTORY:
        drafts += _row_volume(current, history)
        if current.row_count:
            became_all_null = {d.column_name for d in drafts if d.metric == "null_ratio"}
            for col in current.columns.values():
                past = [h.columns[col.name] for h in history if col.name in h.columns]
                drafts += _column_anomalies(col, past, skip_null_ratio=col.name in became_all_null)
    elif history and current.row_count == 0 and history[-1].row_count > 0:
        # An emptied table is loud enough to report before a baseline exists.
        drafts += _row_volume(current, history)

    if current.row_count == 0 and not any(d.metric == "row_count" for d in drafts):
        drafts.append(Draft(
            kind=AnomalyKind.FINDING.value, metric="empty_table", severity=AnomalySeverity.MEDIUM.value,
            message="The table has no rows. Nothing in it can be checked.",
        ))
    return drafts
