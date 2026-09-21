"""Change detection and rule-based reconciliation, against a real git repo.

The detector is driven by actual commits in a temporary repository rather
than by mocked git output, because the things most likely to break are the
things a mock would paper over: what `git diff --name-only` returns across a
range, how a force-push looks, and whether an unrelated file counts as a
change.

The agent is not exercised here - it needs a model. What is tested is the
boundary around it: which checks may be rewritten without review, and the
no-model path that still catches mechanical drift.
"""

import subprocess
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import models
from app.db import Base
from app.maintenance import detector, service
from app.models import (
    Check,
    CheckOrigin,
    CheckRevision,
    Connector,
    Database,
    Project,
    RevisionStatus,
    cuid,
)
from app.monitoring.incidents import utcnow


def git(args, cwd):
    subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        env={
            "GIT_AUTHOR_NAME": "T", "GIT_AUTHOR_EMAIL": "t@e.com",
            "GIT_COMMITTER_NAME": "T", "GIT_COMMITTER_EMAIL": "t@e.com",
            "PATH": __import__("os").environ.get("PATH", ""),
        },
    )


BRONZE_SQL = """
CREATE TABLE D.BRONZE.ORDERS_RAW (RAW_PAYLOAD VARIANT, LOADED_AT TIMESTAMP_NTZ);
CREATE TABLE D.SILVER.ORDERS (ORDER_ID NUMBER, TOTAL NUMBER, UPDATED_AT TIMESTAMP_NTZ);
MERGE INTO D.SILVER.ORDERS tgt USING (
  SELECT src.RAW_PAYLOAD:order_id::NUMBER AS ORDER_ID,
         src.RAW_PAYLOAD:total::NUMBER AS TOTAL
  FROM D.BRONZE.ORDERS_RAW src
) s ON tgt.ORDER_ID = s.ORDER_ID WHEN MATCHED THEN UPDATE SET tgt.TOTAL = s.TOTAL;
"""


@pytest.fixture
def repo(tmp_path) -> Path:
    root = tmp_path / "pipeline"
    root.mkdir()
    git(["init", "-b", "main"], root)
    (root / "sql").mkdir()
    (root / "sql" / "bronze.sql").write_text(BRONZE_SQL)
    (root / "README.md").write_text("unrelated")
    git(["add", "."], root)
    git(["commit", "-m", "Initial pipeline"], root)
    return root


@pytest.fixture
def db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()


def head(root: Path) -> str:
    return subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=root, capture_output=True, text=True
    ).stdout.strip()


@pytest.fixture
def project(db, repo):
    project = Project(
        id=cuid(), slug="p", name="P",
        repo_url=str(repo), repo_ref="main", repo_commit=head(repo),
    )
    connector = Connector(id=cuid(), project_id=project.id, name="c", type="SNOWFLAKE", config={})
    database = Database(
        id=cuid(), project_id=project.id, connector_id=connector.id,
        name="D", slug="d", repo_paths={"BRONZE": "sql/bronze.sql"},
    )
    db.add_all([project, connector, database])
    db.commit()
    return project


@pytest.fixture(autouse=True)
def local_checkout(monkeypatch, repo):
    """Point `fetch_repo` at the temp repo instead of cloning over the
    network - the detector's logic is the subject, not git transport."""
    def fake_fetch(url, token=None, ref=None):
        return {"url": url, "ref": ref or "main", "commit": head(Path(url)),
                "subject": "", "path": url}

    monkeypatch.setattr(detector, "fetch_repo", fake_fetch)
    monkeypatch.setattr(service, "fetch_repo", fake_fetch)


class TestRepoChangeDetection:
    def test_no_new_commits_is_no_change(self, db, project):
        report = detector.detect_changes(db, project, include_warehouse=False)
        assert report.has_changes is False

    def test_a_commit_to_a_watched_file_is_a_change(self, db, project, repo):
        (repo / "sql" / "bronze.sql").write_text(
            BRONZE_SQL.replace("TOTAL NUMBER", "TOTAL NUMBER NOT NULL")
        )
        git(["commit", "-am", "Make TOTAL required"], repo)

        report = detector.detect_changes(db, project, include_warehouse=False)
        assert report.has_changes is True
        assert report.repo.changed_paths == ["sql/bronze.sql"]
        assert "Make TOTAL required" in report.repo.subjects

    def test_a_commit_to_an_unwatched_file_is_not_a_change(self, db, project, repo):
        # A monorepo commit touching an unrelated service must not trigger
        # re-derivation, or the agent runs on every push forever.
        (repo / "README.md").write_text("still unrelated, but edited")
        git(["commit", "-am", "Update docs"], repo)

        report = detector.detect_changes(db, project, include_warehouse=False)
        assert report.has_changes is False

    def test_a_missing_previous_commit_reports_everything_watched(self, db, project, repo):
        # What a force-push looks like. Reporting nothing would leave the
        # checks stale forever with no signal that anything happened.
        project.repo_commit = "0" * 40
        db.commit()

        report = detector.detect_changes(db, project, include_warehouse=False)
        assert report.repo.changed_paths == ["sql/bronze.sql"]
        assert any("rewritten" in s.lower() for s in report.repo.subjects)

    def test_a_project_with_no_repo_reports_nothing(self, db, project):
        project.repo_url = None
        db.commit()
        report = detector.detect_changes(db, project, include_warehouse=False)
        assert report.repo is None


class TestRuleBasedReconciliation:
    def add_check(self, db, project, config, origin=CheckOrigin.DERIVED.value, edited=False):
        database = project.databases[0]
        check = Check(
            id=cuid(), name="Orders parity", type="BRONZE_TO_SILVER_PARITY",
            schedule="*/10 * * * *", database_id=database.id,
            connector_id=database.connector_id, config=config, origin=origin,
            human_edited_at=utcnow() if edited else None,
        )
        db.add(check)
        db.commit()
        return check

    def stale_config(self):
        return {
            "bronzeObject": "D.BRONZE.ORDERS_RAW",
            "silverObject": "D.SILVER.ORDERS",
            "bronzeLoadedAtColumn": "LOADED_AT",
            "keyColumns": [
                {"name": "ORDER_ID", "bronze": "OLD_EXPR", "silver": "ORDER_ID"}
            ],
            "valueColumns": [],
        }

    def change_the_ddl(self, repo):
        (repo / "sql" / "bronze.sql").write_text(
            BRONZE_SQL.replace("RAW_PAYLOAD:total", "RAW_PAYLOAD:order_total")
        )
        git(["commit", "-am", "Rename the payload key"], repo)

    def test_drift_from_the_derived_config_is_proposed(self, db, project, repo):
        self.add_check(db, project, self.stale_config())
        self.change_the_ddl(repo)

        result = service.run_maintenance(db, project, include_warehouse=False)
        assert result["changed"] is True
        assert result["revisions"] == 1
        revision = db.query(CheckRevision).one()
        # Never auto-applied without the agent: the rules cannot tell a
        # rename from a replacement, so a person decides.
        assert revision.status == RevisionStatus.PENDING.value
        assert revision.proposed_config["keyColumns"][0]["bronze"] != "OLD_EXPR"

    def test_a_human_edited_check_is_never_proposed_against(self, db, project, repo):
        # A hand-written check encodes what someone believed the data should
        # be, which is exactly what catches the bugs derivation cannot.
        self.add_check(db, project, self.stale_config(), origin=CheckOrigin.HUMAN.value)
        self.change_the_ddl(repo)

        service.run_maintenance(db, project, include_warehouse=False)
        assert db.query(CheckRevision).count() == 0

    def test_a_derived_check_a_person_edited_is_also_protected(self, db, project, repo):
        self.add_check(
            db, project, self.stale_config(), origin=CheckOrigin.DERIVED.value, edited=True
        )
        self.change_the_ddl(repo)

        service.run_maintenance(db, project, include_warehouse=False)
        assert db.query(CheckRevision).count() == 0

    def test_the_same_change_is_not_proposed_twice(self, db, project, repo):
        # A review queue that re-proposes what was already rejected is a
        # queue people stop opening.
        self.add_check(db, project, self.stale_config())
        self.change_the_ddl(repo)
        service.run_maintenance(db, project, include_warehouse=False)

        (repo / "sql" / "bronze.sql").write_text(
            (repo / "sql" / "bronze.sql").read_text() + "\n-- another edit\n"
        )
        git(["commit", "-am", "Another change"], repo)
        service.run_maintenance(db, project, include_warehouse=False)

        assert db.query(CheckRevision).count() == 1

    def test_the_watermark_advances_so_work_is_not_repeated(self, db, project, repo):
        self.add_check(db, project, self.stale_config())
        self.change_the_ddl(repo)
        service.run_maintenance(db, project, include_warehouse=False)

        assert project.repo_commit == head(repo)
        assert service.run_maintenance(db, project, include_warehouse=False)["changed"] is False


class TestAutoApplyBoundary:
    def decision(self, confidence=0.9, action="UPDATE"):
        from app.maintenance.agent import CheckDecision

        return CheckDecision(
            action=action, check_id="c", reason="r", confidence=confidence,
            proposed_config={"object": "A", "comparisonObject": "B"},
        )

    def check(self, origin, edited=False):
        return Check(
            id="c", name="n", type="ROW_COUNT", schedule="* * * * *",
            database_id="d", connector_id="k", config={}, origin=origin,
            human_edited_at=utcnow() if edited else None,
        )

    def test_an_agent_authored_check_may_be_auto_applied(self):
        from app.maintenance.agent import _may_auto_apply

        assert _may_auto_apply(self.check(CheckOrigin.DERIVED.value), self.decision())

    def test_a_human_check_may_never_be_auto_applied(self):
        from app.maintenance.agent import _may_auto_apply

        assert not _may_auto_apply(self.check(CheckOrigin.HUMAN.value), self.decision())

    def test_an_edited_check_may_never_be_auto_applied(self):
        from app.maintenance.agent import _may_auto_apply

        assert not _may_auto_apply(
            self.check(CheckOrigin.DERIVED.value, edited=True), self.decision()
        )

    def test_a_low_confidence_decision_is_never_auto_applied(self):
        from app.maintenance.agent import _may_auto_apply

        assert not _may_auto_apply(
            self.check(CheckOrigin.DERIVED.value), self.decision(confidence=0.4)
        )

    def test_a_new_check_is_always_reviewed(self):
        # Nothing was broken before it, so nothing breaks by waiting - and a
        # spurious check failing on its own schedule is expensive to trace.
        from app.maintenance.agent import _may_auto_apply

        assert not _may_auto_apply(None, self.decision(action="CREATE"))
