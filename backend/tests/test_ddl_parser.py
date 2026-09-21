"""Parser behaviour that the generated checks depend on being exactly right.

Two of these encode judgement calls rather than mechanics, and they are the
reason this file exists: which conditions count as a row filter, and what a
`NOT NULL` column licenses. Get either wrong and the check generator either
proposes a check that fails forever on correct data, or silently proposes
nothing at all - and "nothing" is indistinguishable from "all clear" when you
are reading a list of proposals.
"""

from app.ingest.ddl_parser import parse_merges, parse_tables


def columns_of(sql: str) -> dict[str, dict]:
    return {c["name"]: c for c in parse_tables(sql, "t.sql")[0]["columns"]}


def one_merge(sql: str) -> dict:
    merges = parse_merges(sql, "m.sql")
    assert len(merges) == 1, f"expected exactly one MERGE, parsed {len(merges)}"
    return merges[0]


class TestColumnNullability:
    def test_not_null_is_detected(self):
        columns = columns_of("CREATE TABLE D.S.T (A NUMBER NOT NULL, B STRING);")
        assert columns["A"]["nullable"] is False
        assert columns["B"]["nullable"] is True

    def test_not_null_after_default_is_detected(self):
        # The type parse stops at DEFAULT, so a naive implementation reading
        # only up to the first stopword never sees the NOT NULL behind it.
        columns = columns_of("CREATE TABLE D.S.T (A NUMBER(38,2) DEFAULT 0 NOT NULL);")
        assert columns["A"]["nullable"] is False

    def test_explicit_null_is_nullable(self):
        # `NULL` and `NOT NULL` share a stopword and mean opposite things.
        columns = columns_of("CREATE TABLE D.S.T (A TIMESTAMP_NTZ NULL);")
        assert columns["A"]["nullable"] is True


class TestRowFilter:
    def test_unfiltered_merge_has_no_predicate(self):
        merge = one_merge(
            "MERGE INTO D.S.T tgt USING (SELECT s.A AS A FROM D.B.R s) s "
            "ON tgt.A = s.A WHEN MATCHED THEN UPDATE SET tgt.A = s.A"
        )
        assert merge["filtered"] is False
        assert merge["filter_predicate"] is None

    def test_business_filter_is_captured_without_aliases(self):
        # The predicate is re-applied to the source table selected with no
        # alias, so `src.` has to come off or the generated SQL will not run.
        merge = one_merge(
            "MERGE INTO D.S.T tgt USING (SELECT src.A AS A FROM D.B.R src "
            "WHERE src.QUANTITY > 0) s ON tgt.A = s.A WHEN MATCHED THEN UPDATE SET tgt.A = s.A"
        )
        assert merge["filtered"] is True
        assert merge["filter_predicate"] == "(QUANTITY > 0)"

    def test_qualified_object_name_survives_alias_stripping(self):
        merge = one_merge(
            "MERGE INTO D.S.T tgt USING (SELECT src.A AS A FROM DB.BRONZE.RAW src "
            "WHERE src.REGION = 'EU') s ON tgt.A = s.A WHEN MATCHED THEN UPDATE SET tgt.A = s.A"
        )
        assert merge["filter_predicate"] == "(REGION = 'EU')"
        assert merge["source"] == "DB.BRONZE.RAW"

    def test_stream_mechanics_are_not_a_filter(self):
        # METADATA$ACTION drops nothing that was ever meant to land.
        merge = one_merge(
            "MERGE INTO D.S.T tgt USING (SELECT s.A AS A FROM D.B.R s "
            "WHERE METADATA$ACTION = 'INSERT') s ON tgt.A = s.A "
            "WHEN MATCHED THEN UPDATE SET tgt.A = s.A"
        )
        assert merge["filtered"] is False

    def test_incremental_subquery_is_not_a_filter(self):
        # `IN (SELECT ... FROM changed_ids)` says which rows *this run* touches,
        # not which rows belong in the target - the target still accumulates
        # every row, so full parity is the correct assertion. Treating it as a
        # filter would leave every gold table with no parity check at all.
        merge = one_merge(
            "MERGE INTO D.G.T tgt USING (WITH changed_ids AS (SELECT A FROM D.S.STREAM) "
            "SELECT s.A AS A FROM D.S.SRC s WHERE s.A IN (SELECT A FROM changed_ids)) s "
            "ON tgt.A = s.A WHEN MATCHED THEN UPDATE SET tgt.A = s.A"
        )
        assert merge["filtered"] is False
        assert merge["filter_predicate"] is None

    def test_business_filter_survives_alongside_an_incremental_one(self):
        merge = one_merge(
            "MERGE INTO D.G.T tgt USING (WITH changed_ids AS (SELECT A FROM D.S.STREAM) "
            "SELECT s.A AS A FROM D.S.SRC s WHERE s.A IN (SELECT A FROM changed_ids) "
            "AND s.QUANTITY > 0) s ON tgt.A = s.A WHEN MATCHED THEN UPDATE SET tgt.A = s.A"
        )
        assert merge["filter_predicate"] == "(QUANTITY > 0)"

    def test_trailing_clause_is_not_swallowed_into_the_predicate(self):
        # A greedy `WHERE (.*)$` grab pulls GROUP BY into the filter text and
        # produces SQL that does not parse.
        merge = one_merge(
            "MERGE INTO D.G.T tgt USING (SELECT s.A AS A FROM D.S.SRC s "
            "WHERE s.REGION = 'EU' GROUP BY A ORDER BY A) s "
            "ON tgt.A = s.A WHEN MATCHED THEN UPDATE SET tgt.A = s.A"
        )
        assert merge["filter_predicate"] == "(REGION = 'EU')"

    def test_or_inside_a_condition_is_parenthesised(self):
        # Re-joining bare conditions with AND would let an OR capture its
        # neighbours and silently widen the filter.
        merge = one_merge(
            "MERGE INTO D.G.T tgt USING (SELECT s.A AS A FROM D.S.SRC s "
            "WHERE (s.R = 'EU' OR s.R = 'US') AND s.Q > 0) s "
            "ON tgt.A = s.A WHEN MATCHED THEN UPDATE SET tgt.A = s.A"
        )
        assert merge["filter_predicate"] == "((R = 'EU' OR R = 'US')) AND (Q > 0)"
