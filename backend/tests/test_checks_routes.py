"""The check routes actually respond.

These exist because of a bug that was live for some time and that no existing
test could have caught: `_query_with_relations` eager-loaded
`CheckRun.ticket`, a relationship deleted when tickets moved to Jira. Every
route in `routers/checks.py` raised AttributeError and returned 500, so the
check detail page rendered blank - and the whole suite stayed green, because it
tested the derivation and the engine directly and never asked the API for
anything.

So the assertion worth making is the dull one: each route returns a response at
all. A stale eager-load, a renamed relationship, a schema field pointing at an
attribute that no longer exists - none of those are visible from a unit test of
the layer underneath, and all of them take the page down completely rather than
degrading it.
"""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import models
from app.db import Base, get_db
from app.main import app
from app.models import cuid


@pytest.fixture()
def client(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)

    def override():
        db = Session()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override

    db = Session()
    project = models.Project(id=cuid(), slug="p", name="P")
    db.add(project)
    db.flush()
    connector = models.Connector(
        id=cuid(), project_id=project.id, name="sf", type="SNOWFLAKE", config={}
    )
    db.add(connector)
    db.flush()
    database = models.Database(
        id=cuid(), project_id=project.id, connector_id=connector.id, name="DB", slug="db"
    )
    db.add(database)
    db.flush()
    check = models.Check(
        id="chk-1",
        database_id=database.id,
        connector_id=connector.id,
        name="Orders parity",
        description="Every settled bronze order reaches silver exactly once.",
        rationale="Derived from the MERGE; the ON clause is the contract.",
        type="NULL_RATE",
        schedule="*/5 * * * *",
        config={"object": "DB.SILVER.ORDERS", "column": "ORDER_ID", "maxNullRatio": 0},
    )
    db.add(check)
    run = models.CheckRun(id=cuid(), check_id=check.id, status="PASSED")
    db.add(run)
    db.commit()
    db.close()

    yield TestClient(app)
    app.dependency_overrides.clear()


def test_list_checks_responds(client):
    response = client.get("/api/checks")
    assert response.status_code == 200, response.text
    assert response.json()[0]["name"] == "Orders parity"


def test_check_detail_responds(client):
    """The route behind the page that rendered blank."""
    response = client.get("/api/checks/chk-1")
    assert response.status_code == 200, response.text


def test_detail_carries_all_three_of_description_logic_and_sql(client):
    """What the page needs to render. A missing `statements` key is not an empty
    section in the UI - it throws on `.map` and blanks the whole page."""
    body = client.get("/api/checks/chk-1").json()
    assert body["description"]
    assert body["rationale"]
    assert body["statements"] and body["statements"][0]["sql"]
    assert body["statements_error"] is None


def test_a_run_relationship_does_not_break_the_route(client):
    """The specific shape of the original bug: the check has runs, and loading
    them must not reach for a relationship that no longer exists."""
    body = client.get("/api/checks/chk-1").json()
    assert len(body["runs"]) == 1


def test_missing_check_is_a_404_not_a_500(client):
    assert client.get("/api/checks/nope").status_code == 404
