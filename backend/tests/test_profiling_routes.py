"""Profiling end to end through the API, with the warehouse replaced by a fake.

The fake answers the two statements a profile issues - the column list and
the aggregate row - so what is exercised is everything this app does with
them: storing, the baseline, detection, acknowledgement carrying forward, and
every route returning at all.
"""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import models
from app.db import Base, get_db
from app.main import app
from app.models import cuid
from app.profiling import service
from app.profiling.detector import MIN_HISTORY


class FakeWarehouse:
    """Serves one table: PRODUCT_ID (number), WAREHOUSE_ID (always NULL), PRICE."""

    def __init__(self):
        self.rows = 1000
        self.price_nulls = 0
        self.issued: list[str] = []

    def run_query(self, sql: str) -> list[dict]:
        self.issued.append(sql)
        if "INFORMATION_SCHEMA.TABLES" in sql:
            return [{"TABLE_SCHEMA": "SILVER", "TABLE_NAME": "PRODUCTS"}]
        if "INFORMATION_SCHEMA.COLUMNS" in sql:
            return [
                {"COLUMN_NAME": "PRODUCT_ID", "DATA_TYPE": "NUMBER", "ORDINAL_POSITION": 1},
                {"COLUMN_NAME": "WAREHOUSE_ID", "DATA_TYPE": "TEXT", "ORDINAL_POSITION": 2},
                {"COLUMN_NAME": "PRICE", "DATA_TYPE": "NUMBER", "ORDINAL_POSITION": 3},
            ]
        n = self.rows
        return [{
            "ROW_COUNT": n,
            "C0_NULLS": 0, "C0_DISTINCT": n, "C0_MIN": 1.0, "C0_MAX": float(n), "C0_MEAN": n / 2,
            "C1_NULLS": n, "C1_DISTINCT": 0, "C1_BLANKS": 0, "C1_MIN": None, "C1_MAX": None, "C1_MEAN": None,
            "C2_NULLS": self.price_nulls, "C2_DISTINCT": 50, "C2_MIN": 1.0, "C2_MAX": 99.0, "C2_MEAN": 40.0,
        }]

    def close(self):
        pass


@pytest.fixture()
def env(tmp_path, monkeypatch):
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
    warehouse = FakeWarehouse()
    monkeypatch.setattr(service, "build_connector", lambda *_: warehouse)

    db = Session()
    project = models.Project(id=cuid(), slug="p", name="P")
    db.add(project)
    db.flush()
    connector = models.Connector(id=cuid(), project_id=project.id, name="sf", type="SNOWFLAKE", config={})
    db.add(connector)
    db.flush()
    database = models.Database(
        id="db-1", project_id=project.id, connector_id=connector.id, name="DPM_SRC_INVENTORY", slug="inv"
    )
    db.add(database)
    db.commit()
    db.close()

    yield TestClient(app), warehouse
    app.dependency_overrides.clear()


def _add(client) -> str:
    res = client.post("/api/projects/p/profiling/targets", json={"database_id": "db-1", "object": "SILVER.PRODUCTS"})
    assert res.status_code == 201, res.text
    assert res.json()["object"] == "DPM_SRC_INVENTORY.SILVER.PRODUCTS"
    return res.json()["id"]


def test_first_run_finds_the_all_null_column(env):
    client, _ = env
    target_id = _add(client)
    run = client.post(f"/api/profiling/targets/{target_id}/run").json()
    assert run["status"] == "SUCCEEDED"
    assert run["row_count"] == 1000

    anomalies = client.get("/api/projects/p/anomalies").json()
    assert [(a["column_name"], a["metric"]) for a in anomalies] == [("WAREHOUSE_ID", "all_null")]

    detail = client.get(f"/api/profiling/targets/{target_id}").json()
    assert [c["column_name"] for c in detail["columns"]] == ["PRODUCT_ID", "WAREHOUSE_ID", "PRICE"]
    assert detail["baseline_runs_needed"] == MIN_HISTORY


def test_acknowledged_finding_stays_quiet_on_later_runs(env):
    client, _ = env
    target_id = _add(client)
    client.post(f"/api/profiling/targets/{target_id}/run")
    [finding] = client.get("/api/projects/p/anomalies").json()
    client.post(f"/api/profiling/anomalies/{finding['id']}/acknowledge")

    client.post(f"/api/profiling/targets/{target_id}/run")
    assert client.get("/api/projects/p/anomalies").json() == []
    assert len(client.get("/api/projects/p/anomalies?include_acknowledged=true").json()) == 1


def test_null_rate_jump_is_detected_once_a_baseline_exists(env):
    client, warehouse = env
    target_id = _add(client)
    for _ in range(MIN_HISTORY):
        client.post(f"/api/profiling/targets/{target_id}/run")
    warehouse.price_nulls = 600
    client.post(f"/api/profiling/targets/{target_id}/run")

    found = {(a["column_name"], a["metric"]) for a in client.get("/api/projects/p/anomalies").json()}
    assert ("PRICE", "null_ratio") in found

    # Recovery clears it: "active" means present on the latest run.
    warehouse.price_nulls = 0
    client.post(f"/api/profiling/targets/{target_id}/run")
    found = {(a["column_name"], a["metric"]) for a in client.get("/api/projects/p/anomalies").json()}
    assert ("PRICE", "null_ratio") not in found


def test_profile_only_ever_issues_selects(env):
    client, warehouse = env
    target_id = _add(client)
    client.post(f"/api/profiling/targets/{target_id}/run")
    assert warehouse.issued and all(s.lstrip().upper().startswith("SELECT") for s in warehouse.issued)


def test_discover_adds_every_table_once(env):
    client, _ = env
    first = client.post("/api/projects/p/profiling/discover", json={"database_id": "db-1"}).json()
    again = client.post("/api/projects/p/profiling/discover", json={"database_id": "db-1"}).json()
    assert first["added"] == ["DPM_SRC_INVENTORY.SILVER.PRODUCTS"]
    assert again["added"] == [] and again["already_profiled"] == first["added"]


def test_failed_profile_is_recorded_not_raised(env, monkeypatch):
    client, warehouse = env
    target_id = _add(client)

    def boom(sql):
        raise RuntimeError("Database 'DPM_SRC_INVENTORY' does not exist or not authorized.")

    monkeypatch.setattr(warehouse, "run_query", boom)
    run = client.post(f"/api/profiling/targets/{target_id}/run").json()
    assert run["status"] == "ERROR"
    assert "not authorized" in run["message"]


def test_validation(env):
    client, _ = env
    bad_name = client.post("/api/projects/p/profiling/targets", json={"database_id": "db-1", "object": "SILVER.X;DROP"})
    other_db = client.post(
        "/api/projects/p/profiling/targets", json={"database_id": "db-1", "object": "OTHER.SILVER.PRODUCTS"}
    )
    bad_cron = client.post(
        "/api/projects/p/profiling/targets",
        json={"database_id": "db-1", "object": "SILVER.PRODUCTS", "schedule": "every hour"},
    )
    assert (bad_name.status_code, other_db.status_code, bad_cron.status_code) == (422, 422, 422)

    target_id = _add(client)
    assert client.post(
        "/api/projects/p/profiling/targets", json={"database_id": "db-1", "object": "SILVER.PRODUCTS"}
    ).status_code == 409
    assert client.patch(f"/api/profiling/targets/{target_id}", json={"enabled": False}).json()["enabled"] is False
    assert client.delete(f"/api/profiling/targets/{target_id}").status_code == 204
    assert client.get("/api/projects/p/profiling").json() == []
