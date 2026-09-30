"""A check that cannot reach its data is not a pipeline problem.

The classification decides whether RCA blames the pipeline's latest commit,
so both directions matter: an expired password must not reach RCA, and a
dropped column must not be mistaken for a network blip and hidden.
"""

import pytest

from app.checks import runner
from app.checks.defects import unreachable_kind
from app.checks.engine import CheckOutcome


@pytest.mark.parametrize(
    "message",
    [
        "390100 (08004): Incorrect username or password was specified.",
        "390144 (08004): JWT token is invalid.",
        "Role 'DPM_READER' specified in the connect string does not exist or not authorized.",
        "000606 (57P03): No active warehouse selected in the current session.",
        "Warehouse 'DPM_WH' cannot be resumed because resource monitor 'RM' has exceeded its quota.",
        "003001 (42501): SQL access control error: Insufficient privileges to operate on table 'X'",
        # Recorded from the live account with a mistyped account identifier.
        "290404 (08001): 404 Not Found: post no-such-account.snowflakecomputing.com:443/session/v1/login-request",
    ],
)
def test_access_errors(message):
    assert unreachable_kind(message) == "access"


@pytest.mark.parametrize(
    "message",
    [
        "250001 (08001): Failed to connect to DB: xy.snowflakecomputing.com:443. Connection reset by peer",
        "390114 (08001): Authentication token has expired. The user must authenticate again.",
        "Read timed out. (read timeout=60)",
        "Failed to establish a new connection: [Errno 11001] getaddrinfo failed",
    ],
)
def test_transient_errors(message):
    assert unreachable_kind(message) == "transient"


@pytest.mark.parametrize(
    "message",
    [
        # Our SQL, or the pipeline moving - never "unreachable".
        "000904 (42000): SQL compilation error: invalid identifier 'X.INDIVIDUAL_ID'",
        "002003 (42S02): SQL compilation error: Object 'DB.SILVER.X' does not exist or not authorized.",
        "Numeric value 'abc' is not recognized",
    ],
)
def test_data_and_sql_errors_are_not_unreachable(message):
    assert unreachable_kind(message) is None


class _Check:
    """_reach only hands the check to _run, which is replaced here."""


@pytest.fixture
def attempts(monkeypatch):
    monkeypatch.setattr(runner.settings, "transient_retry_seconds", 0)
    queue: list[CheckOutcome] = []
    calls: list[int] = []

    def fake_run(check):
        calls.append(1)
        return queue.pop(0)

    monkeypatch.setattr(runner, "_run", fake_run)
    return queue, calls


def _error(message):
    return CheckOutcome(status="ERROR", metrics={}, message=message)


def test_access_error_is_not_retried(attempts):
    queue, calls = attempts
    outcome = runner._reach(_Check(), _error("Incorrect username or password was specified."))
    assert outcome.status == "UNREACHABLE"
    assert outcome.metrics["unreachable"] == {
        "kind": "access",
        "error": "Incorrect username or password was specified.",
        "retried": False,
    }
    assert calls == []


def test_transient_error_recovers_on_retry_and_says_so(attempts):
    queue, calls = attempts
    queue.append(CheckOutcome(status="PASSED", metrics={"primaryCount": 5}, message="ok"))
    outcome = runner._reach(_Check(), _error("Read timed out."))
    assert outcome.status == "PASSED"
    assert outcome.metrics["retried"]["afterError"] == "Read timed out."
    assert len(calls) == 1


def test_transient_error_twice_is_unreachable(attempts):
    queue, calls = attempts
    queue.append(_error("Read timed out."))
    outcome = runner._reach(_Check(), _error("Read timed out."))
    assert outcome.status == "UNREACHABLE"
    assert outcome.metrics["unreachable"]["retried"] is True


def test_retry_that_connects_and_hits_a_data_error_goes_to_defect_assessment(attempts):
    queue, _ = attempts
    queue.append(_error("SQL compilation error: invalid identifier 'X.ID'"))
    outcome = runner._reach(_Check(), _error("Read timed out."))
    assert outcome.status == "ERROR"
    assert "invalid identifier" in outcome.message


def test_data_error_passes_through_untouched(attempts):
    _, calls = attempts
    original = _error("SQL compilation error: invalid identifier 'X.ID'")
    assert runner._reach(_Check(), original) is original
    assert calls == []
