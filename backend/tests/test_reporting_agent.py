"""The reporting agent's guard rails.

No model is involved. `apply_decision` is the boundary between whatever the
agent returned and the database, and it is the part that has to hold when
the model is wrong - so it is tested directly with the responses a wrong
model would produce.

The two refusals are the important ones. An agent that can close tickets on
reasoning alone will eventually close one on a pipeline that is still
broken, and that failure is silent: everybody believes it is handled.
"""

import itertools
from datetime import timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

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
from app.monitoring.agent import IncidentDecision, apply_decision

_clock = itertools.count()


@pytest.fixture
def db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()


@pytest.fixture(autouse=True)
def board_backend(monkeypatch):
    from app.tickets.registry import ticket_backend

    ticket_backend.cache_clear()
    monkeypatch.setattr("app.config.settings.jira_base_url", "")
    yield
    ticket_backend.cache_clear()


@pytest.fixture
def incident(db):
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
    run = CheckRun(
        id=cuid(), check_id=check.id, status="FAILED", message="37 missing",
        started_at=lifecycle.utcnow() + timedelta(minutes=next(_clock)),
    )
    db.add(run)
    db.commit()
    return triage.on_failed_run(db, check, run, None)


def decide(**kwargs) -> IncidentDecision:
    kwargs.setdefault("reasoning", "because")
    kwargs.setdefault("comment", "Missing keys went from 37 to 412 across four runs.")
    return IncidentDecision(incident_id=kwargs.pop("incident_id", "x"), **kwargs)


class TestRefusals:
    def test_clear_is_refused_and_downgraded_to_a_comment(self, db, incident):
        # Only a passing check clears an incident. A model reasoning its way
        # to "this looks resolved" would close tickets on live breakage.
        applied = apply_decision(db, incident, decide(action="CLEAR"))
        assert applied is True
        assert incident.state == IncidentState.OPEN.value
        assert incident.cleared_at is None
        assert [e.kind for e in incident.events][-1] == "COMMENT"

    def test_warn_on_a_currently_failing_incident_is_refused(self, db, incident):
        # WARNING is "not broken yet". Applying it to something actively
        # failing understates a real failure.
        apply_decision(db, incident, decide(action="WARN"))
        assert incident.state == IncidentState.OPEN.value

    def test_warn_is_allowed_once_the_check_is_passing_again(self, db, incident):
        incident.consecutive_passes = 1
        db.commit()
        apply_decision(db, incident, decide(action="WARN"))
        assert incident.state == IncidentState.WARNING.value

    def test_an_action_with_no_comment_is_dropped(self, db, incident):
        # An escalation nobody can read is just a priority bump with no
        # explanation attached.
        before = len(incident.events)
        assert apply_decision(db, incident, decide(action="ESCALATE", comment="  ")) is False
        assert len(incident.events) == before

    def test_none_writes_nothing(self, db, incident):
        before = len(incident.events)
        assert apply_decision(db, incident, decide(action="NONE")) is False
        assert len(incident.events) == before

    def test_an_invalid_severity_is_ignored_rather_than_stored(self, db, incident):
        original = incident.severity
        apply_decision(db, incident, decide(action="COMMENT", severity="SUPER_URGENT"))
        assert incident.severity == original


class TestAppliedActions:
    def test_comment_is_recorded_against_the_agent(self, db, incident):
        apply_decision(db, incident, decide(action="COMMENT"))
        event = incident.events[-1]
        assert event.kind == "COMMENT"
        assert event.author == "agent"
        assert "412" in event.body
        assert incident.triage_source == "agent"

    def test_escalate_bumps_the_counter_and_the_ticket(self, db, incident):
        apply_decision(
            db, incident, decide(action="ESCALATE", severity=TicketPriority.HIGH.value)
        )
        assert incident.escalation_count == 1
        assert incident.last_escalated_at is not None
        assert incident.severity == TicketPriority.HIGH.value
        assert db.query(Ticket).one().priority == TicketPriority.HIGH.value

    def test_suppress_closes_the_ticket_and_says_why(self, db, incident):
        # Suppression is a state with a reason attached, never a silent drop.
        apply_decision(
            db,
            incident,
            decide(action="SUPPRESS", comment="Opened and cleared 6 times this week."),
        )
        assert incident.state == IncidentState.SUPPRESSED.value
        assert db.query(Ticket).one().status == TicketStatus.DONE.value
        assert incident.events[-1].kind == "SUPPRESSED"

    def test_correlation_group_is_namespaced(self, db, incident):
        # An unqualified label the model reuses next week would silently
        # merge two unrelated groups.
        apply_decision(db, incident, decide(action="COMMENT", correlation_group="task-down"))
        assert incident.correlation_id == "agent:task-down"


class TestBatchHandling:
    def test_a_decision_for_an_unknown_incident_is_dropped(self, db, incident, monkeypatch):
        from app.monitoring import agent as agent_module

        monkeypatch.setattr(
            agent_module,
            "build_agent",
            lambda **_: object(),
        )
        monkeypatch.setattr(
            agent_module,
            "invoke_agent",
            lambda *_args, **_kw: agent_module.ReportingDecisions(
                decisions=[
                    decide(incident_id="does-not-exist", action="ESCALATE"),
                    decide(incident_id=incident.id, action="COMMENT"),
                ]
            ),
        )
        # Acting on an incident nobody asked about turns a bounded review
        # into an unbounded one.
        assert agent_module.review_incidents(db, [incident]) == 1
