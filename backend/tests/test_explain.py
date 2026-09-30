"""Run explanations: the template, the guards on a model draft, and when to ask.

The guards are the point. A draft that cites a number it was not given, or
states a verdict, must never reach the page - the model path is tested here by
injecting what a model might write, not by calling one.
"""

from datetime import datetime, timedelta, timezone

from app.checks.explain import (
    PreviousRun,
    build_facts,
    explain_run,
    template_text,
    verify,
)

NOW = datetime(2026, 9, 30, 12, 0, tzinfo=timezone.utc)

PARITY_CONFIG = {"bronzeObject": "DB.SILVER.INVOICES", "silverObject": "DB.GOLD.TRANSACTION"}


def _parity(*, missing=0, extra=0, dups=0, mismatches=0, keys=14_300, reported=None, by_column=None) -> dict:
    return {
        "bronzeRowsSettled": keys,
        "bronzeDistinctKeys": keys,
        "silverRows": keys - missing + extra,
        "silverDistinctKeys": keys - missing + extra,
        "silverDuplicateKeys": dups,
        "silverSurplusRows": dups,
        "missingInSilver": missing,
        "extraInSilver": extra,
        "valueMismatches": mismatches,
        "silverAheadOfSettled": 0,
        "mismatchByColumn": by_column or {},
        "thresholds": {
            "maxMissingInSilver": 0,
            "maxExtraInSilver": 0,
            "maxDuplicateKeys": 0,
            "maxValueMismatches": 0,
        },
        "reported": reported or {},
        "samples": {"missingInSilver": ["INV-10422"]},
    }


def _explain(status, metrics, previous=None, *, model="claude-cli", call_model=None, now=NOW):
    calls = []

    def fake(facts):
        calls.append(facts)
        return call_model(facts) if call_model else ("264 invoice keys never reached the gold table.", None)

    result = explain_run(
        check_name="Invoices reach gold",
        check_type="BRONZE_TO_SILVER_PARITY",
        description=None,
        config=PARITY_CONFIG,
        status=status,
        metrics=metrics,
        message="engine message",
        previous=previous,
        model_name=model,
        cooldown_minutes=60,
        now=now,
        call_model=fake,
    )
    return result, calls


# --- template -----------------------------------------------------------------


def test_template_names_the_loss_and_its_shape():
    text = template_text("BRONZE_TO_SILVER_PARITY", "FAILED", _parity(missing=264), None, PARITY_CONFIG, None)
    assert "264 of 14,300 settled source keys (1.8%) never reached the target" in text
    assert "rows are being dropped" in text


def test_template_says_what_moved_since_the_previous_run():
    text = template_text(
        "BRONZE_TO_SILVER_PARITY", "FAILED", _parity(missing=264), None, PARITY_CONFIG, _parity(missing=0)
    )
    assert "(was 0)" in text


def test_template_marks_report_only_extras():
    metrics = _parity(extra=4181, reported={"extraInSilver": {"value": 4181, "threshold": 0}})
    text = template_text("BRONZE_TO_SILVER_PARITY", "PASSED", metrics, None, PARITY_CONFIG, None)
    assert "(reported only)" in text
    assert "point-in-time source" in text


def test_template_does_not_call_an_empty_comparison_clean():
    text = template_text("BRONZE_TO_SILVER_PARITY", "PASSED", _parity(keys=0), None, PARITY_CONFIG, None)
    assert "Nothing was compared" in text


def test_template_for_invalid_and_error_says_nothing_about_the_data():
    assert "says nothing about the data" in template_text("NULL_RATE", "INVALID", {}, "x", {}, None)
    error = template_text("NULL_RATE", "ERROR", {}, "Object 'X' does not exist\nmore", {}, None)
    assert "could not run" in error and "does not exist" in error


def test_template_falls_back_to_the_message_on_old_metrics():
    # An older engine's metrics lacking a key must not blank the box.
    assert template_text("ROW_COUNT", "FAILED", {"comparisonCount": 3}, "the message", {}, None) == "the message"


# --- verify -------------------------------------------------------------------


def _facts(metrics):
    return build_facts(
        check_name="c",
        check_type="BRONZE_TO_SILVER_PARITY",
        description=None,
        config=PARITY_CONFIG,
        status="FAILED",
        metrics=metrics,
        previous=None,
        draft="",
    )


def test_verify_accepts_numbers_from_facts_including_rounding():
    facts = _facts(_parity(missing=264))
    assert verify("264 of 14,300 invoice keys (about 1.8%) never reached DB.GOLD.TRANSACTION.", facts) is None
    assert verify("Roughly 2% of invoice keys never arrived.", facts) is None


def test_verify_rejects_a_number_it_was_not_given():
    facts = _facts(_parity(missing=264))
    reason = verify("About 300 invoice keys never arrived.", facts)
    assert reason is not None and "300" in reason


def test_verify_rejects_a_verdict():
    assert verify("The check failed on 264 keys.", _facts(_parity(missing=264))) == "states a verdict"


def test_verify_rejects_markdown_and_length():
    facts = _facts(_parity(missing=264))
    assert verify("- 264 keys missing", facts) == "uses markdown"
    assert verify("264 keys. " * 60, facts) is not None
    assert verify("One. Two. Three. Four.", facts) is not None


def test_verify_checks_spelled_out_numbers():
    facts = _facts(_parity(missing=264, by_column={"EMAIL": 3, "STATUS": 2}))
    assert verify("Values differ on three keys of EMAIL.", facts) is None
    assert verify("Values differ on seven keys of EMAIL.", facts) is not None
    assert verify("About three hundred keys never arrived.", facts) is not None
    # "one" stays free: "one current row per key" is not a count of anything.
    assert verify("Each key needs one current row; 264 keys never arrived.", facts) is None


def test_verify_ignores_digits_inside_identifiers():
    assert verify("CUSTOMER_360 is missing 264 keys.", _facts(_parity(missing=264))) is None


def test_samples_are_not_facts():
    # The page shows samples itself; a draft quoting one is making a claim
    # nobody checked against the facts it was given.
    facts = _facts(_parity(missing=264))
    assert "samples" not in facts["measured"]
    assert verify("Key INV 10422 is among them.", facts) is not None


# --- when to ask --------------------------------------------------------------


def test_failure_asks_the_model_and_records_it():
    result, calls = _explain("FAILED", _parity(missing=264))
    assert len(calls) == 1
    assert result["source"] == "ai"
    assert result["model"] == "claude-cli"
    assert result["askedAt"] == NOW.isoformat()


def test_clean_pass_never_asks():
    result, calls = _explain("PASSED", _parity())
    assert calls == []
    assert result["source"] == "template"


def test_reported_pass_asks():
    metrics = _parity(extra=76, reported={"extraInSilver": {"value": 76, "threshold": 0}})
    _, calls = _explain("PASSED", metrics)
    assert len(calls) == 1


def test_no_model_configured_uses_the_template():
    result, calls = _explain("FAILED", _parity(missing=264), model=None)
    assert calls == []
    assert result["source"] == "template"


def test_rejected_draft_falls_back_and_says_why():
    result, _ = _explain("FAILED", _parity(missing=264), call_model=lambda facts: (None, "states a verdict"))
    assert result["source"] == "template"
    assert result["rejected"] == "states a verdict"
    assert result["askedAt"] == NOW.isoformat()


def test_identical_metrics_reuse_the_previous_text():
    first, _ = _explain("FAILED", _parity(missing=264))
    previous = PreviousRun(id="run-1", status="FAILED", metrics=_parity(missing=264), explanation=first)
    second, calls = _explain("FAILED", _parity(missing=264), previous, now=NOW + timedelta(hours=5))

    assert calls == []
    assert second["text"] == first["text"]
    assert second["reusedFromRunId"] == "run-1"


def test_same_shape_new_numbers_within_cooldown_uses_the_template():
    first, _ = _explain("FAILED", _parity(missing=264))
    previous = PreviousRun(id="run-1", status="FAILED", metrics=_parity(missing=264), explanation=first)
    second, calls = _explain("FAILED", _parity(missing=270), previous, now=NOW + timedelta(minutes=10))

    assert calls == []
    assert second["source"] == "template"
    assert "270" in second["text"] and "(was 264)" in second["text"]
    # The streak's last call is carried, so the cooldown keeps counting.
    assert second["askedAt"] == NOW.isoformat()


def test_same_shape_after_cooldown_asks_again():
    first, _ = _explain("FAILED", _parity(missing=264))
    previous = PreviousRun(id="run-1", status="FAILED", metrics=_parity(missing=264), explanation=first)
    _, calls = _explain("FAILED", _parity(missing=270), previous, now=NOW + timedelta(minutes=61))
    assert len(calls) == 1


def test_new_shape_asks_immediately():
    first, _ = _explain("FAILED", _parity(missing=264))
    previous = PreviousRun(id="run-1", status="FAILED", metrics=_parity(missing=264), explanation=first)
    _, calls = _explain("FAILED", _parity(missing=264, dups=3), previous, now=NOW + timedelta(minutes=1))
    assert len(calls) == 1


def test_a_rejecting_model_is_asked_once_per_cooldown_not_every_run():
    reject = lambda facts: (None, "states a verdict")  # noqa: E731
    first, _ = _explain("FAILED", _parity(missing=264), call_model=reject)
    previous = PreviousRun(id="run-1", status="FAILED", metrics=_parity(missing=264), explanation=first)
    _, calls = _explain("FAILED", _parity(missing=270), previous, call_model=reject, now=NOW + timedelta(minutes=5))
    assert calls == []


def test_list_lengths_are_facts():
    facts = build_facts(
        check_name="c",
        check_type="SCHEMA_DRIFT",
        description=None,
        config={"object": "DB.GOLD.EMAIL"},
        status="FAILED",
        metrics={"missing": [], "extra": [], "typeMismatches": ["A x", "B y", "C z"]},
        previous=None,
        draft="",
    )
    assert verify("3 columns of DB.GOLD.EMAIL changed type.", facts) is None
