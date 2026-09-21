"""What the rules propose, and what they admit they cannot.

The coverage cases matter as much as the proposal cases. Ingestion's failure
mode is quiet - a repo the parser does not understand yields a short list, and
a short list looks exactly like a simple pipeline - so each reason a table goes
uncovered is asserted here rather than left to be discovered on a real repo.
"""

from app.checks.config_schemas import CONFIG_SCHEMAS_BY_TYPE
from app.ingest.ddl_parser import parse_merges, parse_tables
from app.ingest.heuristic import assess_coverage, propose_checks

PARITY = "BRONZE_TO_SILVER_PARITY"


def build_repo(sql: str) -> dict:
    """A ParsedRepo from one blob of SQL, with the fields the rules read."""
    return {
        "schemas": {},
        "tables": parse_tables(sql, "pipeline.sql"),
        "merges": parse_merges(sql, "pipeline.sql"),
        "streams": {},
        "cadence": {},
        "files": ["pipeline.sql"],
        "warnings": [],
    }


def by_type(proposals: list[dict], check_type: str) -> list[dict]:
    return [p for p in proposals if p["type"] == check_type]


BRONZE_TO_SILVER = """
CREATE TABLE D.BRONZE.ORDERS_RAW (RAW_PAYLOAD VARIANT, LOADED_AT TIMESTAMP_NTZ);
CREATE TABLE D.SILVER.ORDERS (ORDER_ID NUMBER, TOTAL NUMBER, UPDATED_AT TIMESTAMP_NTZ);
MERGE INTO D.SILVER.ORDERS tgt USING (
  SELECT src.RAW_PAYLOAD:order_id::NUMBER AS ORDER_ID,
         src.RAW_PAYLOAD:total::NUMBER AS TOTAL
  FROM D.BRONZE.ORDERS_RAW src
) s ON tgt.ORDER_ID = s.ORDER_ID WHEN MATCHED THEN UPDATE SET tgt.TOTAL = s.TOTAL;
"""

SILVER_TO_GOLD = BRONZE_TO_SILVER + """
CREATE TABLE D.GOLD.ORDER_SUMMARY (ORDER_ID NUMBER, TOTAL NUMBER, UPDATED_AT TIMESTAMP_NTZ);
MERGE INTO D.GOLD.ORDER_SUMMARY tgt USING (
  SELECT s.ORDER_ID AS ORDER_ID, s.TOTAL AS TOTAL FROM D.SILVER.ORDERS s
) x ON tgt.ORDER_ID = x.ORDER_ID WHEN MATCHED THEN UPDATE SET tgt.TOTAL = x.TOTAL;
"""


class TestParityAtEveryLayer:
    def test_bronze_to_silver_gets_parity(self):
        parity = by_type(propose_checks(build_repo(BRONZE_TO_SILVER)), PARITY)
        assert len(parity) == 1
        assert parity[0]["config"]["bronzeObject"] == "D.BRONZE.ORDERS_RAW"
        assert parity[0]["config"]["bronzeLoadedAtColumn"] == "LOADED_AT"

    def test_silver_to_gold_gets_parity_not_just_a_row_count(self):
        # The rule used to fire only for a landing source, which left every
        # gold table checked by row count alone - blind to a lost row and to a
        # wrong value alike.
        parity = by_type(propose_checks(build_repo(SILVER_TO_GOLD)), PARITY)
        targets = {p["config"]["silverObject"] for p in parity}
        assert targets == {"D.SILVER.ORDERS", "D.GOLD.ORDER_SUMMARY"}

    def test_typed_source_settles_on_its_update_column(self):
        # A typed table maintained by a MERGE stamps UPDATED_AT, not LOADED_AT.
        # Settling against a column that never advances silently breaks the
        # late-vs-lost distinction the whole check rests on.
        gold = next(
            p for p in by_type(propose_checks(build_repo(SILVER_TO_GOLD)), PARITY)
            if p["config"]["silverObject"] == "D.GOLD.ORDER_SUMMARY"
        )
        assert gold["config"]["bronzeLoadedAtColumn"] == "UPDATED_AT"

    def test_filtered_merge_carries_the_filter_onto_the_source_side(self):
        sql = SILVER_TO_GOLD.replace(
            "FROM D.SILVER.ORDERS s\n", "FROM D.SILVER.ORDERS s WHERE s.TOTAL > 0\n"
        )
        gold = next(
            p for p in by_type(propose_checks(build_repo(sql)), PARITY)
            if p["config"]["silverObject"] == "D.GOLD.ORDER_SUMMARY"
        )
        assert gold["config"]["sourceFilter"] == "(TOTAL > 0)"
        # A filter lifted from one place and applied to another is exactly the
        # kind of thing that rots silently, so it is flagged for review.
        assert any("filtered by" in c for c in gold["concerns"])

    def test_every_proposal_validates_against_the_api_schema(self):
        for proposal in propose_checks(build_repo(SILVER_TO_GOLD)):
            CONFIG_SCHEMAS_BY_TYPE[proposal["type"]].model_validate(proposal["config"])


class TestNullRate:
    def test_not_null_column_gets_a_zero_tolerance_check(self):
        sql = "CREATE TABLE D.SILVER.T (A NUMBER NOT NULL, B STRING);"
        proposals = by_type(propose_checks(build_repo(sql)), "NULL_RATE")
        assert len(proposals) == 1
        assert proposals[0]["config"] == {
            "object": "D.SILVER.T",
            "column": "A",
            "maxNullRatio": 0,
        }

    def test_nullable_columns_get_nothing(self):
        # Any non-zero threshold is a judgement the DDL does not state, and
        # guessing one produces a check that is either muted or ignored.
        sql = "CREATE TABLE D.SILVER.T (A NUMBER, B STRING);"
        assert by_type(propose_checks(build_repo(sql)), "NULL_RATE") == []

    def test_landing_tables_are_skipped(self):
        sql = "CREATE TABLE D.BRONZE.T_RAW (RAW_PAYLOAD VARIANT NOT NULL);"
        assert by_type(propose_checks(build_repo(sql)), "NULL_RATE") == []


class TestCoverage:
    def assess(self, sql: str):
        repo = build_repo(sql)
        return assess_coverage(repo, propose_checks(repo))

    def test_a_fully_covered_pipeline_says_so(self):
        report = self.assess(SILVER_TO_GOLD)
        assert report["uncovered"] == []
        assert report["tables_with_parity"] == 2
        # Landing tables are exempt, so they must not drag the ratio down.
        assert report["tables_expecting_parity"] == 2
        assert report["tables_total"] == 3

    def test_table_no_merge_writes_is_reported(self):
        sql = BRONZE_TO_SILVER + "CREATE TABLE D.GOLD.ORPHAN (A NUMBER);"
        gaps = {u["table"]: u["gaps"][0] for u in self.assess(sql)["uncovered"]}
        assert "No MERGE in the repository writes this table" in gaps["D.GOLD.ORPHAN"]

    def test_merge_without_a_readable_key_is_reported(self):
        sql = """
        CREATE TABLE D.SILVER.S (A NUMBER, UPDATED_AT TIMESTAMP_NTZ);
        CREATE TABLE D.GOLD.T (A NUMBER, UPDATED_AT TIMESTAMP_NTZ);
        MERGE INTO D.GOLD.T tgt USING (SELECT s.A AS A FROM D.SILVER.S s) x
          ON 1 = 1 WHEN MATCHED THEN UPDATE SET tgt.A = x.A;
        """
        gaps = {u["table"]: u["gaps"][0] for u in self.assess(sql)["uncovered"]}
        assert "no ON condition the parser could read as a key" in gaps["D.GOLD.T"]

    def test_source_without_a_timestamp_is_reported(self):
        sql = """
        CREATE TABLE D.SILVER.S (A NUMBER);
        CREATE TABLE D.GOLD.T (A NUMBER, UPDATED_AT TIMESTAMP_NTZ);
        MERGE INTO D.GOLD.T tgt USING (SELECT s.A AS A FROM D.SILVER.S s) x
          ON tgt.A = x.A WHEN MATCHED THEN UPDATE SET tgt.A = x.A;
        """
        gaps = {u["table"]: u["gaps"][0] for u in self.assess(sql)["uncovered"]}
        assert "no load or update timestamp column" in gaps["D.GOLD.T"]

    def test_source_defined_outside_the_repo_is_reported(self):
        sql = """
        CREATE TABLE D.GOLD.T (A NUMBER, UPDATED_AT TIMESTAMP_NTZ);
        MERGE INTO D.GOLD.T tgt USING (SELECT s.A AS A FROM OTHER.SILVER.S s) x
          ON tgt.A = x.A WHEN MATCHED THEN UPDATE SET tgt.A = x.A;
        """
        gaps = {u["table"]: u["gaps"][0] for u in self.assess(sql)["uncovered"]}
        assert "not defined by any CREATE TABLE in this repository" in gaps["D.GOLD.T"]

    def test_a_repo_of_only_landing_tables_is_called_out(self):
        # The shape a dbt or view-only repo degrades into. Reporting "0 of 0
        # covered" as success would be the worst possible answer here.
        report = self.assess("CREATE TABLE D.BRONZE.A_RAW (P VARIANT, LOADED_AT TIMESTAMP_NTZ);")
        assert "all of them landing tables" in report["summary"]
