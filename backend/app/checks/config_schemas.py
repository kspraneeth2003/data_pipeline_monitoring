from pydantic import BaseModel, Field


class RowCountConfig(BaseModel):
    object: str
    comparisonObject: str | None = None
    minRows: int | None = None
    toleranceAbs: int = 0


class FreshnessConfig(BaseModel):
    object: str
    timestampColumn: str
    maxAgeMinutes: float


class NullRateConfig(BaseModel):
    object: str
    column: str
    maxNullRatio: float = Field(ge=0, le=1)


class ExpectedColumn(BaseModel):
    name: str
    dataType: str


class SchemaDriftConfig(BaseModel):
    object: str
    expectedColumns: list[ExpectedColumn]


class CrossSourceParityConfig(BaseModel):
    primaryQuery: str
    secondaryQuery: str
    toleranceAbs: float = 0


class ParityColumn(BaseModel):
    """One logical column, with the expression that produces it on each side.

    Both sides are written explicitly rather than inferred, because bronze is
    untyped VARIANT and silver is typed: `RAW_PAYLOAD:customer_id::NUMBER` on the
    left has to line up with `CUSTOMER_ID` on the right. Making the cast explicit
    and symmetric is what stops `'1'` vs `1` from reading as a mismatch.
    """

    name: str
    bronze: str
    silver: str


class BronzeToSilverParityConfig(BaseModel):
    bronzeObject: str
    silverObject: str

    # The composite key silver is expected to be unique on - normally lifted
    # straight from the MERGE's ON clause.
    keyColumns: list[ParityColumn] = Field(min_length=1)

    # Optional value-level comparison. Key parity alone cannot see a MERGE that
    # reads the wrong payload field, since the keys still line up perfectly.
    valueColumns: list[ParityColumn] = Field(default_factory=list)

    bronzeLoadedAtColumn: str = "LOADED_AT"
    bronzeSequenceColumn: str | None = None

    # How long a bronze row is allowed to sit unmerged before its absence from
    # silver counts as real loss rather than normal pipeline latency.
    lagMinutes: float = Field(default=5, ge=0)

    # Snowflake time-travel pin, for replaying a historical run bit-for-bit.
    asOfTimestamp: str | None = None

    maxMissingInSilver: int = Field(default=0, ge=0)
    maxExtraInSilver: int = Field(default=0, ge=0)
    maxDuplicateKeys: int = Field(default=0, ge=0)
    maxValueMismatches: int = Field(default=0, ge=0)

    sampleLimit: int = Field(default=5, ge=0, le=100)


CONFIG_SCHEMAS_BY_TYPE: dict[str, type[BaseModel]] = {
    "ROW_COUNT": RowCountConfig,
    "FRESHNESS": FreshnessConfig,
    "NULL_RATE": NullRateConfig,
    "SCHEMA_DRIFT": SchemaDriftConfig,
    "CROSS_SOURCE_PARITY": CrossSourceParityConfig,
    "BRONZE_TO_SILVER_PARITY": BronzeToSilverParityConfig,
}
