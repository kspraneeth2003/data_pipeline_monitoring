"""The short "what is off" box on a run: what exactly is wrong, and how big.

It sits between the engine's one-line message, which is terse, and RCA,
which is about *why* and runs only on failure. The reader it serves is an
engineer glancing at a run to learn what it found without reading the metrics
JSON - the thing the old sign-off sheets' Notes column did by hand.

Three rules make it safe to show next to a verdict, and each is enforced in
code rather than asked for in the prompt, because this repo has already seen
an agent cite a metric "climbing 3841 -> 5089" that was 0 on every run. A
fabricated explanation reads exactly like a true one, so the defence has to be
mechanical:

1. **A template always exists.** `template_text` builds the explanation from
   the metrics alone. It is what is shown with no model, on any model error,
   and whenever a model draft is rejected. The model improves wording; it is
   never required.
2. **Every number the model writes must be in its facts.** `verify` pulls each
   number out of the draft and discards the draft if one is not among the
   numbers it was given (allowing for rounding what it was given). Percentages
   it might want are computed here and handed over, so it has no reason to do
   arithmetic.
3. **The model never states the verdict.** The engine decides PASSED/FAILED
   and the page shows it beside the box. A draft using pass/fail words is
   discarded - it could only agree with the badge or contradict it.

The model is also asked only when it can add something: a failure, or a pass
with a report-only breach. A clean pass, an ERROR, INVALID or UNREACHABLE run get the
template. A check failing the same way on every run keeps its text while the
metrics are unchanged, and otherwise gets the template - which carries the new
numbers exactly - until `explain_ai_cooldown_minutes` has passed. A change of
shape (status, or which metrics breached) always asks again, because that is
when the story changes.
"""

import hashlib
import json
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

logger = logging.getLogger(__name__)

MAX_CHARS = 480
MAX_SENTENCES = 3

# metric name (as used by reportOnly) -> (metrics key, thresholds key)
_PARITY_METRICS = {
    "duplicateKeys": ("silverDuplicateKeys", "maxDuplicateKeys"),
    "missingInSilver": ("missingInSilver", "maxMissingInSilver"),
    "extraInSilver": ("extraInSilver", "maxExtraInSilver"),
    "valueMismatches": ("valueMismatches", "maxValueMismatches"),
}
_SCD2_METRICS = {
    "keysWithManyCurrent": ("keysWithManyCurrent", "maxKeysWithManyCurrent"),
    "keysWithNoCurrent": ("keysWithNoCurrent", "maxKeysWithNoCurrent"),
    "overlappingVersions": ("overlappingVersions", "maxOverlappingVersions"),
    "gappedVersions": ("gappedVersions", "maxGappedVersions"),
    "invalidWindows": ("invalidWindows", "maxInvalidWindows"),
}
_METRICS_BY_TYPE = {"BRONZE_TO_SILVER_PARITY": _PARITY_METRICS, "SCD2_INTEGRITY": _SCD2_METRICS}

# Keys that describe how the run was obtained rather than what it measured.
# Samples also stay out of the fingerprint: their order is not stable, and the
# box does not quote them (the page shows them separately).
_UNMEASURED = {"samples", "repair", "diagnostics", "explanation"}


@dataclass
class PreviousRun:
    id: str
    status: str
    metrics: dict | None
    explanation: dict | None


# --- formatting --------------------------------------------------------------


def _n(value) -> str:
    if isinstance(value, float) and not value.is_integer():
        return f"{value:,.1f}"
    return f"{int(value):,}" if isinstance(value, (int, float)) else str(value)


def _pct(part, whole) -> str | None:
    if not whole:
        return None
    return f"{part / whole * 100:.1f}%"


def _sentence(text: str) -> str:
    text = text.strip()
    if not text:
        return text
    text = text[0].upper() + text[1:]
    return text if text.endswith((".", "!", "?")) else text + "."


def _was(metrics: dict, previous: dict | None, key: str) -> str:
    if not previous:
        return ""
    before, now = previous.get(key), metrics.get(key)
    if isinstance(before, (int, float)) and isinstance(now, (int, float)) and before != now:
        return f" (was {_n(before)})"
    return ""


# --- the template -------------------------------------------------------------


def _parity_text(status: str, m: dict, prev: dict | None) -> str:
    source_keys = m.get("bronzeDistinctKeys", 0)
    target_keys = m.get("silverDistinctKeys", 0)
    thresholds = m.get("thresholds") or {}
    reported = m.get("reported") or {}

    def over(metric: str) -> bool:
        key, limit = _PARITY_METRICS[metric]
        return (m.get(key) or 0) > thresholds.get(limit, 0)

    def tag(metric: str) -> str:
        return " (reported only)" if metric in reported else ""

    findings: list[str] = []
    if over("duplicateKeys"):
        findings.append(
            f"{_n(m['silverDuplicateKeys'])} keys appear more than once in the target"
            f"{_was(m, prev, 'silverDuplicateKeys')}{tag('duplicateKeys')}"
        )
    if over("missingInSilver"):
        share = _pct(m["missingInSilver"], source_keys)
        findings.append(
            f"{_n(m['missingInSilver'])} of {_n(source_keys)} settled source keys"
            f"{f' ({share})' if share else ''} never reached the target"
            f"{_was(m, prev, 'missingInSilver')}{tag('missingInSilver')}"
        )
    if over("extraInSilver"):
        share = _pct(m["extraInSilver"], target_keys)
        findings.append(
            f"{_n(m['extraInSilver'])} target keys{f' ({share})' if share else ''} have no source row"
            f"{_was(m, prev, 'extraInSilver')}{tag('extraInSilver')}"
        )
    if over("valueMismatches"):
        columns = m.get("mismatchByColumn") or {}
        on = (
            " on " + ", ".join(f"{col} ({_n(n)})" for col, n in sorted(columns.items()))
            if columns
            else ""
        )
        findings.append(
            f"{_n(m['valueMismatches'])} keys carry different values{on}"
            f"{_was(m, prev, 'valueMismatches')}{tag('valueMismatches')}"
        )

    if not findings:
        if not source_keys:
            return (
                "Nothing was compared: there were 0 settled source keys, so this result says "
                "nothing about the data."
            )
        ahead = m.get("silverAheadOfSettled") or 0
        return _sentence(
            f"all {_n(source_keys)} settled source keys arrived exactly once in the target, with no extras"
        ) + (f" {_n(ahead)} newer keys are still settling." if ahead else "")

    text = _sentence("; ".join(findings))
    breached = {metric for metric in _PARITY_METRICS if over(metric)}
    if breached == {"missingInSilver"}:
        text += " Nothing was duplicated or invented: rows are being dropped on the way."
    elif breached == {"valueMismatches"}:
        text += " Every key arrived exactly once, so the problem is in the values, not the row movement."
    elif breached == {"extraInSilver"}:
        text += " Nothing was lost: the target holds keys the source does not have."
    elif "duplicateKeys" in breached:
        text += " Any join on this table will double-count those keys."
    if "extraInSilver" in reported:
        text += " Extras are expected when the target keeps loading after a point-in-time source."
    return text


def _scd2_text(status: str, m: dict, prev: dict | None) -> str:
    thresholds = m.get("thresholds") or {}
    reported = m.get("reported") or {}
    phrasing = {
        "keysWithManyCurrent": "keys have more than one current row, so joins through this dimension double-count",
        "keysWithNoCurrent": "keys have no current row, so they drop out of current-state joins",
        "overlappingVersions": "versions overlap the next version of the same key",
        "gappedVersions": "gaps sit between consecutive versions of a key",
        "invalidWindows": "rows have a validity window that ends before it starts",
    }
    findings: list[str] = []
    for metric, (key, limit) in _SCD2_METRICS.items():
        value = m.get(key) or 0
        if value > thresholds.get(limit, 0):
            findings.append(
                f"{_n(value)} {phrasing[metric]}{_was(m, prev, key)}"
                + (" (reported only)" if metric in reported else "")
            )
    flag = (m.get("currentNotOpenEnded") or 0) + (m.get("openEndedNotCurrent") or 0)
    if flag:
        findings.append(f"the current flag and the open-ended marker disagree on {_n(flag)} rows")

    if not findings:
        return _sentence(
            f"{_n(m.get('totalKeys', 0))} keys across {_n(m.get('totalRows', 0))} versions: each has "
            f"exactly one current row and an unbroken, non-overlapping history"
        )
    return _sentence(f"out of {_n(m.get('totalKeys', 0))} keys, " + "; ".join(findings))


def _null_rate_text(m: dict, config: dict) -> str:
    total, nulls = m.get("total", 0), m.get("nulls", 0)
    if not total:
        return "The table is empty, so there was no null rate to measure."
    limit = m.get("maxNullRatio", config.get("maxNullRatio", 0))
    return _sentence(
        f"{_n(nulls)} of {_n(total)} rows ({_pct(nulls, total)}) have no value in "
        f"{config.get('column', 'the column')}; the limit is {limit * 100:.1f}%"
    )


def _freshness_text(m: dict, config: dict) -> str:
    age, limit = m.get("ageMinutes"), m.get("maxAgeMinutes")
    if age is None:
        return "The table is empty, so there is no newest row to date."
    hours = f" (about {age / 60:.0f} hours)" if age >= 120 else ""
    return _sentence(
        f"the newest {config.get('timestampColumn', 'timestamp')} is {_n(round(age, 1))} minutes old"
        f"{hours}; the limit is {_n(limit)} minutes"
    )


def _row_count_text(m: dict, config: dict) -> str:
    obj = config.get("object", "the table")
    if "comparisonCount" in m:
        return _sentence(
            f"{obj} has {_n(m['primaryCount'])} rows and {config.get('comparisonObject')} has "
            f"{_n(m['comparisonCount'])}, a difference of {_n(m['diff'])}; the tolerance is "
            f"{_n(m.get('toleranceAbs', 0))}"
        )
    return _sentence(f"{obj} has {_n(m.get('primaryCount', 0))} rows; the minimum is {_n(m.get('minRows', 0))}")


def _schema_text(m: dict) -> str:
    parts: list[str] = []
    if m.get("missing"):
        parts.append(_sentence("columns missing from the table: " + ", ".join(m["missing"])))
    if m.get("typeMismatches"):
        parts.append(_sentence("type changes: " + "; ".join(m["typeMismatches"])))
    if m.get("extra"):
        parts.append(_sentence("new columns outside the contract, which is not a failure: " + ", ".join(m["extra"])))
    return " ".join(parts) or "Every expected column is present with its expected type."


def _cross_source_text(m: dict) -> str:
    return _sentence(
        f"the primary query returned {_n(m.get('primaryValue'))} and the secondary {_n(m.get('secondaryValue'))}, "
        f"a difference of {_n(m.get('diff'))}; the tolerance is {_n(m.get('toleranceAbs', 0))}"
    )


def template_text(
    check_type: str, status: str, metrics: dict | None, message: str | None, config: dict, previous: dict | None
) -> str:
    """The explanation from the metrics alone. Always available, never wrong."""
    m = metrics or {}
    if status == "INVALID":
        # The defect assessment wrote a specific reason ("its key comes from a
        # joined table"); a generic line would throw it away.
        return (message or "").strip() or (
            "Not monitored: this check's own SQL is broken, so this run says nothing about the data."
        )
    if status == "UNREACHABLE":
        return (
            (message or "Couldn't run.").strip()
            + " Nothing about the data was checked; the last result stands until it can run again."
        )
    if status == "ERROR":
        detail = (message or "").strip().splitlines()[0][:200] if message else ""
        return "The check could not run, so nothing about the data was learned." + (
            f" The warehouse said: {detail}" if detail else ""
        )
    try:
        if check_type == "BRONZE_TO_SILVER_PARITY":
            return _parity_text(status, m, previous)
        if check_type == "SCD2_INTEGRITY":
            return _scd2_text(status, m, previous)
        if check_type == "NULL_RATE":
            return _null_rate_text(m, config)
        if check_type == "FRESHNESS":
            return _freshness_text(m, config)
        if check_type == "ROW_COUNT":
            return _row_count_text(m, config)
        if check_type == "SCHEMA_DRIFT":
            return _schema_text(m)
        if check_type == "CROSS_SOURCE_PARITY":
            return _cross_source_text(m)
    except (KeyError, TypeError, ValueError):
        # Metrics from an older engine version can lack a key. The engine's
        # own message is a correct, if terse, fallback.
        logger.warning("template explanation failed for %s run; using its message", check_type, exc_info=True)
    return message or ""


# --- shape, fingerprint, reuse ------------------------------------------------


def breached_metrics(check_type: str, metrics: dict | None) -> list[str]:
    """Which demotable metrics are over their threshold, asserted or reported."""
    m = metrics or {}
    thresholds = m.get("thresholds") or {}
    return sorted(
        metric
        for metric, (key, limit) in _METRICS_BY_TYPE.get(check_type, {}).items()
        if (m.get(key) or 0) > thresholds.get(limit, 0)
    )


def fingerprint(metrics: dict | None) -> str:
    measured = {k: v for k, v in (metrics or {}).items() if k not in _UNMEASURED}
    return hashlib.sha256(json.dumps(measured, sort_keys=True, default=str).encode()).hexdigest()[:16]


# --- the model path -----------------------------------------------------------

SYSTEM_PROMPT = """\
You write the short explanation box on a data check's result page. A data \
engineer reads it to learn, in seconds, what exactly is off in this run.

Rules:
- Two sentences, under 60 words; never more than 3. Plain text: no \
markdown, no bullet points, no headings.
- Say what is wrong and how big it is, in terms of the tables involved. If \
nothing asserted is wrong, say briefly what was verified.
- Only findings. Do not recite settings (lag, tolerances that were met) or \
list zero metrics one by one; "nothing else is off" covers them. Do not say \
a status is unchanged - only numbers that moved are news.
- Write every number as digits.
- Use only numbers that appear in FACTS. Do not compute new numbers; any \
percentage you need is already in FACTS.
- Never state a verdict. Do not use the words pass, passed, fail, failed or \
failure: the page shows the status next to your text.
- Do not guess causes and do not name people or commits. Root-cause analysis \
is shown separately.
- Metrics under measured.reported are reported only, not asserted. Say so.
- If FACTS has a previous run and the numbers moved, say how.
- Do not list sample keys.
- DRAFT is a correct template version of this box. Make it clearer; do not \
add claims FACTS does not support.

Reply with the explanation text and nothing else."""


def build_facts(
    *,
    check_name: str,
    check_type: str,
    description: str | None,
    config: dict,
    status: str,
    metrics: dict | None,
    previous: PreviousRun | None,
    draft: str,
) -> dict:
    measured = {k: v for k, v in (metrics or {}).items() if k not in _UNMEASURED}
    derived: dict[str, str] = {}
    m = metrics or {}
    if check_type == "BRONZE_TO_SILVER_PARITY":
        for key, whole in (
            ("missingInSilver", "bronzeDistinctKeys"),
            ("extraInSilver", "silverDistinctKeys"),
            ("valueMismatches", "bronzeDistinctKeys"),
            ("silverDuplicateKeys", "silverDistinctKeys"),
        ):
            share = _pct(m.get(key) or 0, m.get(whole) or 0)
            if share and m.get(key):
                derived[f"{key} as share of {whole}"] = share
    # "3 columns changed type" is a fair thing to say, and counting is
    # arithmetic the model would otherwise do unchecked.
    for key, value in measured.items():
        if isinstance(value, list) and value:
            derived[f"{key} count"] = str(len(value))
    if check_type == "NULL_RATE" and m.get("total"):
        derived["null share"] = _pct(m.get("nulls", 0), m["total"]) or ""
    objects = {
        k: config[k]
        for k in ("bronzeObject", "silverObject", "object", "comparisonObject", "column", "timestampColumn")
        if config.get(k)
    }
    return {
        "check": check_name,
        "type": check_type,
        "asserts": description,
        "status": status,
        "objects": objects,
        "measured": measured,
        "derived": derived,
        "previousRun": (
            {
                "status": previous.status,
                "measured": {
                    k: v
                    for k, v in (previous.metrics or {}).items()
                    if k not in _UNMEASURED and isinstance(v, (int, float))
                },
            }
            if previous
            else None
        ),
        "draft": draft,
    }


_NUMBER = re.compile(r"\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?")
# Digits inside an identifier (CUSTOMER_360, V2) are names, not claims.
_WRITTEN_NUMBER = re.compile(r"(?<![A-Za-z_\d.])(?:\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)")
# Spelled-out numbers are claims too. Without this, "three hundred" sails
# past a check that only reads digits. The prompt asks for digits; this is
# what happens when it is not followed.
_NUMBER_WORDS = {
    word: value
    for value, word in enumerate(
        "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen "
        "fifteen sixteen seventeen eighteen nineteen twenty".split()
    )
} | {"thirty": 30, "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90}
_WORD_NUMBER = re.compile(r"\b(" + "|".join(_NUMBER_WORDS) + r")\b", re.I)
_SCALE_WORD = re.compile(r"\b(hundreds?|thousands?|millions?|dozens?)\b", re.I)
_VERDICT = re.compile(r"\b(pass|passes|passed|passing|fail|fails|failed|failing|failure|failures)\b", re.I)
_SENTENCE_END = re.compile(r"[.!?](?=\s|$)")


def _numbers(pattern: re.Pattern, text: str) -> list[tuple[float, int]]:
    """(value, decimals written) for every number in `text`."""
    found = []
    for match in pattern.findall(text):
        plain = match.replace(",", "")
        decimals = len(plain.split(".")[1]) if "." in plain else 0
        found.append((float(plain), decimals))
    return found


def verify(text: str, facts: dict) -> str | None:
    """Why a model draft cannot be shown, or None if it can."""
    if not text:
        return "empty"
    if len(text) > MAX_CHARS:
        return f"longer than {MAX_CHARS} characters"
    if len(_SENTENCE_END.findall(text)) > MAX_SENTENCES:
        return f"more than {MAX_SENTENCES} sentences"
    if "**" in text or re.search(r"^\s*([-*#]|\d+\.)\s", text, re.M):
        return "uses markdown"
    if _VERDICT.search(text):
        return "states a verdict"

    scale = _SCALE_WORD.search(text)
    if scale:
        return f"uses '{scale.group(0)}', a number that cannot be checked"

    allowed = [value for value, _ in _numbers(_NUMBER, json.dumps(facts, default=str))]
    written = _numbers(_WRITTEN_NUMBER, text) + [
        (float(_NUMBER_WORDS[word.lower()]), 0) for word in _WORD_NUMBER.findall(text)
    ]
    for value, decimals in written:
        if value in (0, 1):
            continue
        # A number written to d decimals matches a fact that rounds to it:
        # "1.8%" from 1.83, "2%" from 1.8. Nothing looser - "about 300" for
        # 264 is exactly the kind of drift this exists to stop.
        if not any(abs(round(fact, decimals) - value) < 1e-9 for fact in allowed):
            return f"cites {value:g}, which is not in its facts"
    return None


def _content_text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):  # some providers return content blocks
        return "".join(part.get("text", "") if isinstance(part, dict) else str(part) for part in content)
    return str(content)


def ai_text(facts: dict) -> tuple[str | None, str | None]:
    """(text, rejection reason). Text is None when no draft can be shown."""
    from langchain_core.messages import HumanMessage, SystemMessage

    from app.agents.model import agent_model

    model = agent_model()
    if model is None:
        return None, None
    try:
        reply = model.invoke(
            [SystemMessage(SYSTEM_PROMPT), HumanMessage("FACTS:\n" + json.dumps(facts, indent=2, default=str))]
        )
    except Exception:  # noqa: BLE001 - the template is the answer to any model failure
        logger.warning("run explanation model call failed; using the template", exc_info=True)
        return None, "model call failed"
    text = _content_text(reply.content).strip().strip('"').strip()
    reason = verify(text, facts)
    if reason:
        logger.info("run explanation draft rejected (%s): %r", reason, text[:200])
        return None, reason
    return text, None


# --- entry point --------------------------------------------------------------


def explain_run(
    *,
    check_name: str,
    check_type: str,
    description: str | None,
    config: dict,
    status: str,
    metrics: dict | None,
    message: str | None,
    previous: PreviousRun | None,
    model_name: str | None,
    cooldown_minutes: float,
    now: datetime | None = None,
    call_model=ai_text,
) -> dict:
    """The explanation to store on a run, as its JSON.

    `call_model` is injectable so tests exercise the reuse and cooldown
    decisions without a model; production passes the default.
    """
    now = now or datetime.now(timezone.utc)
    shape = [status, *breached_metrics(check_type, metrics)]
    fp = fingerprint(metrics)
    prev_expl = previous.explanation if previous else None
    same_shape = bool(prev_expl) and prev_expl.get("shape") == shape

    base = {"shape": shape, "fingerprint": fp, "model": None, "askedAt": None, "reusedFromRunId": None, "rejected": None}

    # Identical measurements: the previous text is still exactly true.
    if same_shape and prev_expl.get("fingerprint") == fp and prev_expl.get("text"):
        return {
            **prev_expl,
            "reusedFromRunId": prev_expl.get("reusedFromRunId") or previous.id,
        }

    previous_metrics = previous.metrics if previous else None
    draft = template_text(check_type, status, metrics, message, config, previous_metrics)
    template = {**base, "text": draft, "source": "template"}
    if same_shape:
        # Carry the streak's last model call forward so the cooldown spans
        # template runs, not just consecutive model runs.
        template["askedAt"] = prev_expl.get("askedAt")

    worth_asking = status == "FAILED" or (status == "PASSED" and (metrics or {}).get("reported"))
    if not worth_asking or not model_name:
        return template

    if same_shape and prev_expl.get("askedAt"):
        last = datetime.fromisoformat(prev_expl["askedAt"])
        if now - last < timedelta(minutes=cooldown_minutes):
            return template

    facts = build_facts(
        check_name=check_name,
        check_type=check_type,
        description=description,
        config=config,
        status=status,
        metrics=metrics,
        previous=previous,
        draft=draft,
    )
    text, rejected = call_model(facts)
    if text is None:
        # Stamped even on a rejection, so a model that keeps writing drafts
        # we cannot show is asked once per cooldown, not once per run.
        asked = now.isoformat() if rejected else template["askedAt"]
        return {**template, "rejected": rejected, "askedAt": asked}
    return {**base, "text": text, "source": "ai", "model": model_name, "askedAt": now.isoformat()}
