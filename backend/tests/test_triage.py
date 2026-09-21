"""The incident lifecycle, which is the answer to FR10.

Every case here is a duplicate-ticket bug that the previous design could not
avoid, because a ticket bound to one `CheckRun` had nowhere to record a
second observation. The assertions are mostly "still exactly one ticket".

Runs against SQLite in memory: none of this logic is Postgres-specific, and
a test suite that needs a live database is a test suite that does not get
run. JSONB is the one exception, mapped to JSON for the same reason.
"""

import itertools
from datetime import timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import models
from app.db import Base
from app.models import (
    Check,
    CheckRun,
    Connector,
    Database,
    Incident,
    IncidentState,
    Project,
    Ticket,
    TicketPriority,
    TicketStatus,
    cuid,
)
from app.monitoring import incidents as lifecycle
from app.monitoring import triage


@pytest.fixture
def db():
    # JSONB is rendered as JSON for SQLite by tests/conftest.py.
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()


@pytest.fixture(autouse=True)
def board_backend(monkeypatch):
    """Force the in-app board, so a stray JIRA_* in the developer's .env
    cannot make the suite talk to a real tracker."""
    from app.tickets.registry import ticket_backend

    ticket_backend.cache_clear()
    monkeypatch.setattr("app.config.settings.jira_base_url", "")
    yield
    ticket_backend.cache_clear()


@pytest.fixture
def check(db):
    project = Project(id=cuid(), slug="p", name="P")
    connector = Connector(id=cuid(), project_id=project.id, name="c", type="SNOWFLAKE", config={})
    database = Database(
        id=cuid(), project_id=project.id, connector_id=connector.id, name="DB", slug="db"
    )
    check = Check(
        id=cuid(), name="Orders parity", type="ROW_COUNT", schedule="* * * * *",
        database_id=database.id, connector_id=connector.id, config={},
    )
    db.add_all([project, connector, database, check])
    db.commit()
    return check


# Runs are given increasing timestamps a minute apart. Real runs are always
# ordered in time, and letting them all share `now()` would test an ambiguity
# that cannot occur while hiding ordering bugs that can.
_run_clock = itertools.count()


def make_run(db, check, status, message="boom"):
    run = CheckRun(
        id=cuid(),
        check_id=check.id,
        status=status,
        message=message,
        started_at=lifecycle.utcnow() + timedelta(minutes=next(_run_clock)),
    )
    db.add(run)
    db.commit()
    return run


def fail(db, check, message="boom", rca=None):
    return triage.on_failed_run(db, check, make_run(db, check, "FAILED", message), rca)


def pass_(db, check):
    return triage.on_passed_run(db, check, make_run(db, check, "PASSED", None))


class TestOneIncidentPerProblem:
    def test_first_failure_opens_an_incident_and_files_one_ticket(self, db, check):
        incident = fail(db, check)
        assert incident.state == IncidentState.OPEN.value
        assert db.query(Ticket).count() == 1
        assert db.query(Ticket).one().incident_id == incident.id

    def test_repeated_failures_do_not_file_more_tickets(self, db, check):
        # This is FR10. Before incidents existed this produced five tickets.
        for i in range(5):
            fail(db, check, f"boom {i}")
        assert db.query(Incident).count() == 1
        assert db.query(Ticket).count() == 1
        assert db.query(Incident).one().failure_count == 5

    def test_a_changed_message_is_commented_not_re_ticketed(self, db, check):
        fail(db, check, "37 keys missing")
        incident = fail(db, check, "412 keys missing")
        bodies = [e.body for e in incident.events]
        assert db.query(Ticket).count() == 1
        assert any("412 keys missing" in b and "37 keys missing" in b for b in bodies)

    def test_an_unchanged_message_does_not_comment(self, db, check):
        # A comment per run on a check failing all day is how a ticket
        # becomes unreadable, and then muted.
        fail(db, check, "same")
        incident = fail(db, check, "same")
        assert len([e for e in incident.events if e.kind == "COMMENT"]) == 0


class TestRecovery:
    def test_one_pass_does_not_clear(self, db, check):
        # A single green run of a flapping check is not a recovery.
        fail(db, check)
        incident = pass_(db, check)
        assert incident.state == IncidentState.OPEN.value

    def test_two_passes_clear_and_close_the_ticket(self, db, check):
        fail(db, check)
        pass_(db, check)
        incident = pass_(db, check)
        assert incident.state == IncidentState.CLEARED.value
        assert incident.cleared_at is not None
        assert db.query(Ticket).one().status == TicketStatus.DONE.value
        assert any(e.kind == "CLEARED" for e in incident.events)

    def test_alternating_pass_fail_never_clears(self, db, check):
        # Passes must be consecutive, or a check flapping every other run
        # creeps to the threshold and closes something still happening.
        fail(db, check)
        for _ in range(4):
            pass_(db, check)
            fail(db, check)
        incident = db.query(Incident).one()
        assert incident.state == IncidentState.OPEN.value

    def test_failing_again_soon_after_clearing_reopens(self, db, check):
        fail(db, check)
        pass_(db, check)
        pass_(db, check)
        incident = fail(db, check)
        # One incident, recurring - not a second one. The fact that this is
        # the third recurrence is the most useful thing on the page.
        assert db.query(Incident).count() == 1
        assert incident.state == IncidentState.OPEN.value
        assert any(e.kind == "REOPENED" for e in incident.events)


class TestNoticingThatNobodyNoticed:
    def test_a_stale_untouched_ticket_is_escalated(self, db, check):
        incident = fail(db, check)
        incident.opened_at = lifecycle.utcnow() - timedelta(days=3)
        db.commit()

        escalated = triage.escalate_stale(db)
        assert [i.id for i in escalated] == [incident.id]
        assert incident.severity == TicketPriority.HIGH.value
        assert any(e.kind == "ESCALATED" for e in incident.events)

    def test_a_ticket_somebody_picked_up_is_not_escalated(self, db, check):
        # The point is absence of response, not slowness.
        incident = fail(db, check)
        incident.opened_at = lifecycle.utcnow() - timedelta(days=3)
        db.query(Ticket).one().status = TicketStatus.IN_PROGRESS.value
        db.commit()
        assert triage.escalate_stale(db) == []

    def test_escalation_backs_off(self, db, check):
        incident = fail(db, check)
        incident.opened_at = lifecycle.utcnow() - timedelta(days=3)
        db.commit()
        triage.escalate_stale(db)
        # Immediately again: a daily nag gets filtered and then ignored.
        assert triage.escalate_stale(db) == []

    def test_a_closed_ticket_on_a_still_failing_check_is_reopened(self, db, check):
        # The most dangerous state: everyone believes it is handled and
        # nothing is watching.
        incident = fail(db, check)
        lifecycle.clear_incident(db, incident, "cleared")
        db.query(Ticket).one().status = TicketStatus.DONE.value
        db.commit()
        make_run(db, check, "FAILED", "still broken")

        reopened = triage.reopen_closed_but_failing(db)
        assert [i.id for i in reopened] == [incident.id]
        assert incident.state == IncidentState.OPEN.value
        assert db.query(Ticket).one().status == TicketStatus.TODO.value

    def test_a_closed_ticket_on_a_passing_check_is_left_alone(self, db, check):
        incident = fail(db, check)
        lifecycle.clear_incident(db, incident, "cleared")
        db.query(Ticket).one().status = TicketStatus.DONE.value
        db.commit()
        make_run(db, check, "PASSED", None)
        assert triage.reopen_closed_but_failing(db) == []


class TestSeverity:
    def test_an_error_outranks_a_failure(self, db, check):
        # A check that could not run means the warehouse or the credentials
        # are wrong, which blocks every other answer.
        run = make_run(db, check, "ERROR", "could not connect")
        incident = triage.on_failed_run(db, check, run, None)
        assert incident.severity == TicketPriority.HIGH.value

    def test_sustained_failure_raises_severity_on_its_own(self, db, check):
        for _ in range(10):
            fail(db, check)
        assert db.query(Incident).one().severity == TicketPriority.HIGH.value
