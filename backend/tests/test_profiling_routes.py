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
        # A second table `sync` can discover once appended, to prove a plain
        # catalog view does not pick it up on its own.
        self.extra_tables: list[dict] = []

    def run_query(self, sql: str) -> list[dict]:
        self.issued.append(sql)
        if "INFORMATION_SCHEMA.TABLES" in sql:
            return [{"TABLE_SCHEMA": "SILVER", "TABLE_NAME": "PRODUCTS"}, *self.extra_tables]
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

    # min_numeric/max_numeric are the raw values behind min_value/max_value's
    # display strings - what a column-header sort needs, since "1,000" and
    # "9 chars" can't be compared as numbers once formatted.
    product_id = detail["columns"][0]
    assert (product_id["min_numeric"], product_id["max_numeric"]) == (1.0, 1000.0)


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
    assert "cannot read DPM_SRC_INVENTORY" in run["message"]


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


def test_first_catalog_view_auto_tracks_every_table_it_finds(env):
    # A project nobody has opened Profiling for yet still ends up with every
    # table tracked after the very first view - no "Profile" click needed.
    client, _ = env
    [database] = client.get("/api/projects/p/profiling/catalog").json()
    assert database["readable"] is True
    assert database["synced_at"] is not None
    [schema] = database["schemas"]
    assert schema["name"] == "SILVER"
    [table] = schema["tables"]
    assert table["table"] == "PRODUCTS"
    assert table["target"] is not None
    assert table["target"]["object"] == "DPM_SRC_INVENTORY.SILVER.PRODUCTS"

    # A second view sees the same target - it was not created twice.
    [database] = client.get("/api/projects/p/profiling/catalog").json()
    assert database["schemas"][0]["tables"][0]["target"]["id"] == table["target"]["id"]


def test_catalog_reads_the_cache_not_the_warehouse_after_the_first_view(env):
    client, warehouse = env
    client.get("/api/projects/p/profiling/catalog")
    issued_after_first = len(warehouse.issued)

    client.get("/api/projects/p/profiling/catalog")
    client.get("/api/projects/p/profiling/catalog")

    # No new INFORMATION_SCHEMA.TABLES round trip - the warehouse was not
    # asked again, which is the whole point of caching the catalog.
    assert len(warehouse.issued) == issued_after_first
    assert sum("INFORMATION_SCHEMA.TABLES" in q for q in warehouse.issued) == 1


def test_sync_refreshes_the_cache_and_tracks_a_newly_appeared_table(env):
    client, warehouse = env
    client.get("/api/projects/p/profiling/catalog")  # discovers and tracks PRODUCTS

    warehouse.extra_tables = [{"TABLE_SCHEMA": "GOLD", "TABLE_NAME": "STOCK_SUMMARY"}]

    # The cache is stale until a sync - the new table is not visible yet.
    [database] = client.get("/api/projects/p/profiling/catalog").json()
    assert {s["name"] for s in database["schemas"]} == {"SILVER"}

    result = client.post("/api/projects/p/profiling/sync").json()
    assert result["databases"] == [
        {"database": "DPM_SRC_INVENTORY", "readable": True, "tables": 2, "new_tables_queued": 0}
    ]

    [database] = client.get("/api/projects/p/profiling/catalog").json()
    names = {s["name"] for s in database["schemas"]}
    assert names == {"SILVER", "GOLD"}
    [gold] = [s for s in database["schemas"] if s["name"] == "GOLD"]
    assert gold["tables"][0]["target"] is not None  # tracked without a click, same as the first table


def test_sync_reports_but_does_not_requeue_an_already_tracked_table(env):
    client, _ = env
    client.get("/api/projects/p/profiling/catalog")
    result = client.post("/api/projects/p/profiling/sync").json()
    assert result["databases"][0]["new_tables_queued"] == 0  # nothing new to queue


def test_discover_can_be_limited_to_one_schema(env):
    client, _ = env
    none = client.post("/api/projects/p/profiling/discover", json={"database_id": "db-1", "schema_name": "GOLD"})
    silver = client.post("/api/projects/p/profiling/discover", json={"database_id": "db-1", "schema_name": "silver"})
    assert none.json()["added"] == []
    assert silver.json()["added"] == ["DPM_SRC_INVENTORY.SILVER.PRODUCTS"]


def test_unreadable_database_is_listed_with_a_reason_not_dropped(env, monkeypatch):
    client, warehouse = env

    def denied(sql):
        raise RuntimeError(
            "002003 (02000): 01c7: SQL compilation error:\nDatabase 'DPM_SRC_INVENTORY' does not exist or not authorized."
        )

    monkeypatch.setattr(warehouse, "run_query", denied)
    [database] = client.get("/api/projects/p/profiling/catalog").json()
    assert database["readable"] is False
    assert database["schemas"] == []
    assert "cannot read DPM_SRC_INVENTORY" in database["error"]
    assert "002003" not in database["error"]


def test_run_all_without_a_running_scheduler_queues_nothing(env):
    # Tests run without the scheduler; the hourly schedule is the fallback.
    client, _ = env
    discovered = client.post("/api/projects/p/profiling/discover", json={"database_id": "db-1"}).json()
    assert discovered["queued"] == 0
    assert client.post("/api/projects/p/profiling/run-all", json={"database_id": "db-1"}).json() == {
        "queued": 0,
        "targets": 1,
    }
