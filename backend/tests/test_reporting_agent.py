"""The reporting agent's guard rails.

No model is involved. `apply_decision` is the boundary between whatever the
agent returned and the database, and it is the part that has to hold when
the model is wrong - so it is tested directly with the responses a wrong
model would produce.

The two refusals are the important ones. An agent that can close an issue on
reasoning alone will eventually close one on a pipeline that is still
broken, and that failure is silent: everybody believes it is handled.

A fake Jira stands in for the tracker. It records what would have been sent,
which is what the mirroring assertions actually care about.
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
    IncidentSeverity,
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
def jira(monkeypatch):
    from app.tickets.base import TicketRef, TicketState
    from app.tickets.registry import ticket_backend

    class FakeJira:
        name = "jira"

        def __init__(self):
            self.comments: list[str] = []
            self.transitions: list[bool] = []
            self.priorities: list[str] = []

        def create(self, incident, content):
            return TicketRef(key="DATA-1", url="https://example.atlassian.net/browse/DATA-1")

        def comment(self, incident, body):
            self.comments.append(body)
            return True

        def transition(self, incident, done, comment=None):
            if comment:
                self.comments.append(comment)
            self.transitions.append(done)
            return True

        def set_priority(self, incident, priority):
            self.priorities.append(priority)
            return True

        def fetch_state(self, incident):
            return TicketState(key="DATA-1", status="To Do")

    fake = FakeJira()
    ticket_backend.cache_clear()
    monkeypatch.setattr("app.monitoring.triage.ticket_backend", lambda: fake)
    monkeypatch.setattr("app.monitoring.agent.ticket_backend", lambda: fake)
    yield fake
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

    def test_escalate_bumps_the_counter_and_the_jira_priority(self, db, incident, jira):
        apply_decision(
            db, incident, decide(action="ESCALATE", severity=IncidentSeverity.HIGH.value)
        )
        assert incident.escalation_count == 1
        assert incident.last_escalated_at is not None
        assert incident.severity == IncidentSeverity.HIGH.value
        assert jira.priorities == [IncidentSeverity.HIGH.value]

    def test_suppress_closes_the_issue_and_says_why(self, db, incident, jira):
        # Suppression is a state with a reason attached, never a silent drop -
        # and leaving an issue open that nothing will comment on again is
        # worse than closing it with a stated reason.
        apply_decision(
            db,
            incident,
            decide(action="SUPPRESS", comment="Opened and cleared 6 times this week."),
        )
        assert incident.state == IncidentState.SUPPRESSED.value
        assert jira.transitions == [True]
        assert incident.events[-1].kind == "SUPPRESSED"

    def test_a_comment_is_mirrored_to_jira(self, db, incident, jira):
        apply_decision(db, incident, decide(action="COMMENT"))
        assert any("412" in c for c in jira.comments)

    def test_nothing_breaks_when_there_is_no_tracker(self, db, incident, monkeypatch):
        # No Jira is a supported way to run this: the incident record is
        # still written, it just has no external face.
        monkeypatch.setattr("app.monitoring.agent.ticket_backend", lambda: None)
        assert apply_decision(db, incident, decide(action="COMMENT")) is True
        assert incident.events[-1].author == "agent"

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
        # Both were "investigated", so only the unknown id is what gets
        # this decision dropped - not the anti-fabrication guard.
        monkeypatch.setattr(
            agent_module,
            "build_tools",
            lambda _db, seen: seen.update({incident.id, "does-not-exist"}) or [],
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


class TestAntiFabricationGuard:
    """A decision about an incident the agent never opened is invention.

    This is not hypothetical. On the first real sweep the agent was handed
    25 incidents with a loop budget affording maybe two investigations, and
    it escalated one with a confident comment citing a metric climbing
    "3841 -> 5089" over seven runs, a "migration 0047" and a "WMS feed
    merged 2026-09-15". None of it existed; the real metric was 0 on every
    run. A fabricated escalation reads exactly like a good one, so the
    defence has to be mechanical rather than a line in the prompt.
    """

    def run_agent_with(self, db, monkeypatch, incident, decisions, investigated):
        from app.monitoring import agent as agent_module

        monkeypatch.setattr(agent_module, "build_agent", lambda **_: object())
        # Stand in for the tools having been used: the real ones record
        # into this set as the agent calls them.
        monkeypatch.setattr(
            agent_module,
            "build_tools",
            lambda _db, seen: seen.update(investigated) or [],
        )
        monkeypatch.setattr(
            agent_module,
            "invoke_agent",
            lambda *_a, **_k: agent_module.ReportingDecisions(decisions=decisions),
        )
        return agent_module.review_incidents(db, [incident])

    def test_a_write_on_an_uninvestigated_incident_is_dropped(
        self, db, incident, monkeypatch
    ):
        applied = self.run_agent_with(
            db,
            monkeypatch,
            incident,
            [decide(incident_id=incident.id, action="ESCALATE")],
            investigated=set(),
        )
        assert applied == 0
        assert not any(e.author == "agent" for e in incident.events)

    def test_the_same_decision_is_applied_once_investigated(
        self, db, incident, monkeypatch
    ):
        applied = self.run_agent_with(
            db,
            monkeypatch,
            incident,
            [decide(incident_id=incident.id, action="ESCALATE")],
            investigated={incident.id},
        )
        assert applied == 1
        assert any(e.author == "agent" for e in incident.events)

    def test_none_needs_no_investigation(self, db, incident, monkeypatch):
        # Saying nothing about an incident is not a claim about it, so it
        # does not need evidence behind it.
        applied = self.run_agent_with(
            db,
            monkeypatch,
            incident,
            [decide(incident_id=incident.id, action="NONE")],
            investigated=set(),
        )
        assert applied == 0


class TestReviewSelection:
    def test_an_unreviewed_incident_is_selected(self, db, incident):
        from app.monitoring.sweep import needing_review

        assert [i.id for i in needing_review(db, [incident])] == [incident.id]

    def test_an_incident_with_nothing_new_is_skipped(self, db, incident):
        # 209 active incidents in exactly the state they were in five
        # minutes ago is both expensive to review and an invitation to
        # manufacture something to say.
        from app.monitoring.incidents import add_event, utcnow
        from app.models import IncidentEventKind
        from app.monitoring.sweep import needing_review

        incident.last_seen_at = utcnow() - timedelta(hours=2)
        add_event(db, incident, IncidentEventKind.COMMENT, "said my piece", author="agent")
        db.commit()
        db.refresh(incident)
        assert needing_review(db, [incident]) == []

    def test_a_further_failure_brings_it_back(self, db, incident):
        from app.monitoring.incidents import add_event, utcnow
        from app.models import IncidentEventKind
        from app.monitoring.sweep import needing_review

        add_event(db, incident, IncidentEventKind.COMMENT, "said my piece", author="agent")
        db.commit()
        db.refresh(incident)
        incident.last_seen_at = utcnow() + timedelta(minutes=5)
        db.commit()
        assert [i.id for i in needing_review(db, [incident])] == [incident.id]

    def test_jira_movement_brings_it_back(self, db, incident):
        from app.monitoring.incidents import add_event, utcnow
        from app.models import IncidentEventKind
        from app.monitoring.sweep import needing_review

        add_event(db, incident, IncidentEventKind.COMMENT, "said my piece", author="agent")
        db.commit()
        db.refresh(incident)
        incident.ticket_moved_at = utcnow() + timedelta(minutes=5)
        db.commit()
        assert [i.id for i in needing_review(db, [incident])] == [incident.id]
