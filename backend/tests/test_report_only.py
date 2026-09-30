"""Report-only metrics: measured and shown, never the reason a run fails.

The property worth protecting is that demoting a metric changes the verdict
and nothing else - the breach is still on the run, with its value and
threshold, and the message does not claim a clean 1:1 it did not see.
"""

import pytest
from pydantic import ValidationError

from app.checks import engine
from app.checks.config_schemas import BronzeToSilverParityConfig, Scd2IntegrityConfig


class _OneRow:
    def __init__(self, row: dict):
        self.row = row

    def run_query(self, sql: str) -> list[dict]:
        return [self.row]


PARITY_CONFIG = {
    "bronzeObject": "DB.BRONZE.X_RAW",
    "silverObject": "DB.SILVER.X",
    "keyColumns": [{"name": "ID", "bronze": "RAW_PAYLOAD:id::NUMBER", "silver": "ID"}],
}


def _parity_row(*, missing=0, extra=0, dups=0, mismatches=0) -> dict:
    return {
        "BRONZE_ROWS_SETTLED": 100,
        "BRONZE_DISTINCT_KEYS": 100,
        "SILVER_ROWS": 100 - missing + extra,
        "SILVER_DISTINCT_KEYS": 100 - missing + extra,
        "SILVER_DUPLICATE_KEYS": dups,
        "SILVER_SURPLUS_ROWS": dups,
        "MISSING_IN_SILVER": missing,
        "EXTRA_IN_SILVER": extra,
        "VALUE_MISMATCHES": mismatches,
        "SILVER_AHEAD_OF_SETTLED": 0,
    }


def test_parity_extra_is_a_failure_by_default():
    outcome = engine._run_bronze_to_silver_parity(_OneRow(_parity_row(extra=76)), PARITY_CONFIG)
    assert outcome.status == "FAILED"
    assert outcome.metrics["reported"] == {}


def test_parity_report_only_extra_passes_but_is_recorded():
    config = {**PARITY_CONFIG, "reportOnly": ["extraInSilver"]}
    outcome = engine._run_bronze_to_silver_parity(_OneRow(_parity_row(extra=76)), config)

    assert outcome.status == "PASSED"
    assert outcome.metrics["extraInSilver"] == 76
    assert outcome.metrics["reported"]["extraInSilver"]["value"] == 76
    assert outcome.metrics["reported"]["extraInSilver"]["threshold"] == 0
    assert "reported only" in outcome.message
    # The clean-pass sentence would be a false claim here.
    assert "map 1:1" not in outcome.message


def test_parity_report_only_does_not_hide_an_asserted_failure():
    config = {**PARITY_CONFIG, "reportOnly": ["extraInSilver"]}
    outcome = engine._run_bronze_to_silver_parity(_OneRow(_parity_row(missing=3, extra=76)), config)

    assert outcome.status == "FAILED"
    assert "3 settled bronze key(s) never reached silver" in outcome.message
    assert "reported only" in outcome.message


def test_parity_clean_pass_unchanged():
    config = {**PARITY_CONFIG, "reportOnly": ["extraInSilver"]}
    outcome = engine._run_bronze_to_silver_parity(_OneRow(_parity_row()), config)

    assert outcome.status == "PASSED"
    assert "map 1:1" in outcome.message
    assert outcome.metrics["reported"] == {}


def test_unknown_report_only_metric_is_rejected():
    with pytest.raises(ValidationError):
        BronzeToSilverParityConfig.model_validate({**PARITY_CONFIG, "reportOnly": ["extra"]})


SCD2_CONFIG = {"object": "DB.SILVER.MEMBERS", "naturalKeyColumns": ["MEMBER_ID"]}


def _scd2_row(**overrides) -> dict:
    row = {
        "TOTAL_ROWS": 50,
        "TOTAL_KEYS": 20,
        "KEYS_WITH_NO_CURRENT": 0,
        "KEYS_WITH_MANY_CURRENT": 0,
        "OVERLAPPING_VERSIONS": 0,
        "GAPPED_VERSIONS": 0,
        "INVALID_WINDOWS": 0,
        "CURRENT_NOT_OPEN_ENDED": 0,
        "OPEN_ENDED_NOT_CURRENT": 0,
    }
    row.update(overrides)
    return row


def test_scd2_report_only_gap_passes():
    config = {**SCD2_CONFIG, "reportOnly": ["gappedVersions"]}
    outcome = engine._run_scd2_integrity(_OneRow(_scd2_row(GAPPED_VERSIONS=4)), config)

    assert outcome.status == "PASSED"
    assert outcome.metrics["reported"]["gappedVersions"]["value"] == 4


def test_scd2_flag_disagreement_cannot_be_demoted():
    with pytest.raises(ValidationError):
        Scd2IntegrityConfig.model_validate({**SCD2_CONFIG, "reportOnly": ["currentNotOpenEnded"]})

    config = {**SCD2_CONFIG, "reportOnly": ["gappedVersions"]}
    outcome = engine._run_scd2_integrity(_OneRow(_scd2_row(CURRENT_NOT_OPEN_ENDED=2)), config)
    assert outcome.status == "FAILED"
