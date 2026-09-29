"""Stage derivation, pinning, and the version history of a check's logic.

Two claims are worth pinning down here, because both are quiet when they break.

Stage decides which tab a check appears under on the project page. A check
filed in the wrong tab is not an error anyone sees - it is simply absent from
where they looked, which reads as "we do not check that" and is the worst kind
of monitoring bug.

Versions are the record of what a check used to assert. The failure mode is a
config edit that writes no version, or writes one whose SQL snapshot does not
reflect the config it claims to be a snapshot of. Either way nothing is
reported at the time and the gap is only discovered when someone needs the
history, which is exactly when it cannot be reconstructed.
"""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import models
from app.checks.stage import derive_stage, layer_of
from app.db import Base, get_db
from app.main import app
from app.models import cuid

PARITY_CONFIG = {
    "bronzeObject": "DB.BRONZE.CUSTOMERS_RAW",
    "silverObject": "DB.SILVER.CUSTOMERS",
    "keyColumns": [{"name": "ID", "bronze": "RAW:id::NUMBER", "silver": "ID"}],
    "lagMinutes": 5,
}


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
    db.add(
        models.Check(
            id="chk-parity",
            database_id=database.id,
            connector_id=connector.id,
            name="Bronze to silver parity",
            type="BRONZE_TO_SILVER_PARITY",
            schedule="*/5 * * * *",
            config=dict(PARITY_CONFIG),
            stage="BRONZE_TO_SILVER",
        )
    )
    db.add(
        models.Check(
            id="chk-nulls",
            database_id=database.id,
            connector_id=connector.id,
            name="Email null rate",
            type="NULL_RATE",
            schedule="*/5 * * * *",
            config={"object": "DB.SILVER.CUSTOMERS", "column": "EMAIL", "maxNullRatio": 0.01},
            stage="DATA_QUALITY",
        )
    )
    db.commit()
    db.close()

    yield TestClient(app)
    app.dependency_overrides.clear()


# --- derivation ----------------------------------------------------------


@pytest.mark.parametrize(
    "check_type,config,expected",
    [
        ("BRONZE_TO_SILVER_PARITY", PARITY_CONFIG, "BRONZE_TO_SILVER"),
        (
            "BRONZE_TO_SILVER_PARITY",
            {"bronzeObject": "DB.STG.C", "silverObject": "DB.BRONZE.C"},
            "STG_TO_BRONZE",
        ),
        (
            "BRONZE_TO_SILVER_PARITY",
            {"bronzeObject": "DB.SILVER.C", "silverObject": "GOLD_DB.GOLD.C360"},
            "SILVER_TO_GOLD",
        ),
        (
            "ROW_COUNT",
            {"object": "DB.SILVER.C", "comparisonObject": "GOLD_DB.GOLD.C360"},
            "SILVER_TO_GOLD",
        ),
        ("ROW_COUNT", {"object": "DB.SILVER.C"}, "DATA_QUALITY"),
        ("NULL_RATE", {"object": "DB.SILVER.C", "column": "EMAIL"}, "DATA_QUALITY"),
        ("SCD2_INTEGRITY", {"object": "DB.SILVER.DIM"}, "DATA_QUALITY"),
        (
            "CROSS_SOURCE_PARITY",
            {
                "primaryQuery": "SELECT COUNT(*) FROM DB.SILVER.A",
                "secondaryQuery": "SELECT COUNT(*) FROM OTHER.GOLD.B",
            },
            "SILVER_TO_GOLD",
        ),
    ],
)
def test_stage_is_derived_from_what_the_check_compares(check_type, config, expected):
    assert derive_stage(check_type, config) == expected


def test_a_hop_that_skips_a_layer_is_not_guessed_into_a_tab():
    """Bronze straight to gold names none of the three hops.

    Filing it under one of them would put a check in a tab whose label is a
    claim about what it watches, and that claim would be false.
    """
    config = {"bronzeObject": "DB.BRONZE.C", "silverObject": "DB.GOLD.C"}
    assert derive_stage("BRONZE_TO_SILVER_PARITY", config) == "DATA_QUALITY"


def test_the_layer_comes_from_the_schema_not_the_table_name():
    """`CUSTOMERS_RAW` in a BRONZE schema is bronze.

    Reading the table name would call it staging, on the strength of a naming
    habit, and move every bronze landing table into the wrong tab.
    """
    assert layer_of("DB.BRONZE.CUSTOMERS_RAW") == "BRONZE"
    assert layer_of("DB.RAW.CUSTOMERS") == "STG"
    assert layer_of("CUSTOMERS") is None


def test_a_query_touching_two_layers_names_no_hop():
    """Picking the first match would make the tab depend on join order."""
    config = {
        "primaryQuery": "SELECT * FROM DB.SILVER.A JOIN DB.GOLD.B USING (ID)",
        "secondaryQuery": "SELECT * FROM DB.GOLD.B",
    }
    assert derive_stage("CROSS_SOURCE_PARITY", config) == "DATA_QUALITY"


# --- the project listing -------------------------------------------------


def test_project_checks_carry_their_stage_and_statements(client):
    """What the tab strip needs: one request, every check, stage and SQL."""
    body = client.get("/api/projects/p/checks").json()
    stages = {c["name"]: c["stage"] for c in body}
    assert stages == {
        "Bronze to silver parity": "BRONZE_TO_SILVER",
        "Email null rate": "DATA_QUALITY",
    }
    assert all(c["statements"] for c in body)


# --- versions ------------------------------------------------------------


def test_editing_config_writes_a_version_with_the_sql_it_rendered_to(client):
    config = dict(PARITY_CONFIG, lagMinutes=11)
    response = client.patch(
        "/api/checks/chk-parity", json={"config": config, "note": "Widen the settling lag"}
    )
    assert response.status_code == 200, response.text

    versions = client.get("/api/checks/chk-parity/versions").json()
    assert [v["version"] for v in versions] == [1]
    latest = versions[0]
    assert latest["author"] == "human"
    assert latest["note"] == "Widen the settling lag"
    # 11 minutes reaches the SQL as a second count, which is the point of
    # storing the rendered statement rather than only the config: the number in
    # the query is not the number in the form.
    assert "-660" in latest["sql_text"]


def test_an_edit_that_changes_no_logic_writes_no_version(client):
    """Toggling `enabled` is not a change to what the check asserts.

    A history padded with entries that say nothing is one nobody scrolls, which
    costs more than the rows do.
    """
    client.patch("/api/checks/chk-parity", json={"config": dict(PARITY_CONFIG)})
    before = len(client.get("/api/checks/chk-parity/versions").json())
    client.patch("/api/checks/chk-parity", json={"enabled": False})
    assert len(client.get("/api/checks/chk-parity/versions").json()) == before


def test_restoring_a_version_adds_to_the_history_rather_than_rewinding_it(client):
    client.patch("/api/checks/chk-parity", json={"config": dict(PARITY_CONFIG)})
    client.patch("/api/checks/chk-parity", json={"config": dict(PARITY_CONFIG, lagMinutes=99)})
    versions = client.get("/api/checks/chk-parity/versions").json()
    assert [v["version"] for v in versions] == [2, 1]

    response = client.post("/api/checks/chk-parity/versions/1/restore")
    assert response.status_code == 200, response.text
    assert response.json()["config"]["lagMinutes"] == 5

    # The version being backed out of is still there. It is what explains why
    # the restore was needed.
    versions = client.get("/api/checks/chk-parity/versions").json()
    assert [v["version"] for v in versions] == [3, 2, 1]
    assert versions[1]["config"]["lagMinutes"] == 99


def test_restoring_a_version_that_does_not_exist_is_a_404(client):
    assert client.post("/api/checks/chk-parity/versions/47/restore").status_code == 404


# --- stage overrides and pinning -----------------------------------------


def test_a_hand_set_stage_survives_a_later_config_edit(client):
    """Re-deriving would move the check back with nothing in the record to say
    why, which is the kind of change nobody can find afterwards."""
    client.patch("/api/checks/chk-parity", json={"stage": "DATA_QUALITY"})
    assert client.get("/api/checks/chk-parity").json()["stage_locked"] is True

    client.patch("/api/checks/chk-parity", json={"config": dict(PARITY_CONFIG, lagMinutes=7)})
    body = client.get("/api/checks/chk-parity").json()
    assert body["stage"] == "DATA_QUALITY"


def test_an_underived_stage_follows_the_config(client):
    """The ordinary case: move the objects, and the check moves tab with them."""
    client.patch(
        "/api/checks/chk-parity",
        json={"config": dict(PARITY_CONFIG, bronzeObject="DB.SILVER.C", silverObject="G.GOLD.C")},
    )
    assert client.get("/api/checks/chk-parity").json()["stage"] == "SILVER_TO_GOLD"


def test_pinning_does_not_count_as_a_human_edit(client):
    """Deciding to watch a check is not editing it.

    If pinning stamped `human_edited_at`, it would take the check out of the
    maintenance agent's hands - so watching a derived check would quietly stop
    it being maintained.
    """
    response = client.post("/api/checks/chk-parity/pin", json={"pinned": True})
    assert response.status_code == 200, response.text
    assert response.json()["pinned"] is True

    body = client.get("/api/checks/chk-parity").json()
    assert body["pinned"] is True
    assert body["human_edited_at"] is None
    # No version was written either: the logic did not change.
    assert client.get("/api/checks/chk-parity/versions").json() == []
