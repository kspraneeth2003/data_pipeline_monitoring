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
    """Key-and-value parity between two layers of a pipeline.

    Named for the bronze -> silver case it was written for, but the comparison
    is layer-agnostic: silver -> gold states its key and column mapping in a
    MERGE exactly the same way, and the only thing bronze-specific here is that
    one side may be untyped VARIANT, which `ParityColumn` already handles by
    carrying an explicit expression per side.
    """

    bronzeObject: str
    silverObject: str

    # A predicate the MERGE applies to the source, lifted from its WHERE clause
    # and applied to the source side here so both sides describe the same
    # population. Without it a filtered MERGE cannot be checked at all: the
    # target is *supposed* to hold fewer rows, so unfiltered parity fails
    # forever on correct data and the check gets muted.
    #
    # This is a fragment of SQL from the DDL, not user input, and it is
    # interpolated into the generated statement. Anything reaching this field
    # from outside the parser must be reviewed before it is saved - which is
    # what the ingestion review step is for.
    sourceFilter: str | None = None

    # The mirror of `sourceFilter`, on the target side. Its reason for existing
    # is the SCD2 dimension: that target holds one row per version of a key, so
    # an unrestricted comparison reads a member with three versions as three
    # duplicate keys - the dimension working exactly as designed - and the check
    # fails forever on correct data. `IS_CURRENT = TRUE` restores the statement
    # the check was always meant to make.
    #
    # Same provenance caveat as `sourceFilter`: this is a SQL fragment lifted
    # from the DDL and interpolated into the generated statement, so anything
    # reaching it from outside the parser has to be reviewed before it is saved.
    silverFilter: str | None = None

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


class Scd2IntegrityConfig(BaseModel):
    """The four integrity assertions on a type-2 dimension.

    Tolerances default to zero and should stay there. Unlike a null rate, where
    some non-zero level is normal and the question is how much, every one of
    these is a contradiction in the data: there is no correct number of keys
    with two current rows. They are settable only because a table mid-backfill
    can have a known, temporary population of them, and a check that cannot be
    told so gets disabled instead - which loses the check permanently to fix a
    problem that lasts an afternoon.
    """

    object: str
    # The source's own identifier, repeated once per version. Not the surrogate
    # key, which is unique per row and would make every assertion here trivially
    # pass - each "key" would have exactly one version and no window to overlap.
    naturalKeyColumns: list[str] = Field(min_length=1)

    validFromColumn: str = "VALID_FROM"
    validToColumn: str = "VALID_TO"
    currentFlagColumn: str = "IS_CURRENT"

    # How an open-ended version is written: a sentinel timestamp, or None for
    # the NULL convention. There is no detecting this from the data - a table
    # that mixes both is the fault being looked for, so inferring the convention
    # from whichever appears more often would make the check agree with it.
    openEndedSentinel: str | None = "9999-12-31"
    validToType: str = "TIMESTAMP_NTZ"

    maxKeysWithNoCurrent: int = Field(default=0, ge=0)
    maxKeysWithManyCurrent: int = Field(default=0, ge=0)
    maxOverlappingVersions: int = Field(default=0, ge=0)
    maxGappedVersions: int = Field(default=0, ge=0)
    maxInvalidWindows: int = Field(default=0, ge=0)

    sampleLimit: int = Field(default=5, ge=0, le=100)


CONFIG_SCHEMAS_BY_TYPE: dict[str, type[BaseModel]] = {
    "ROW_COUNT": RowCountConfig,
    "FRESHNESS": FreshnessConfig,
    "NULL_RATE": NullRateConfig,
    "SCHEMA_DRIFT": SchemaDriftConfig,
    "CROSS_SOURCE_PARITY": CrossSourceParityConfig,
    "BRONZE_TO_SILVER_PARITY": BronzeToSilverParityConfig,
    "SCD2_INTEGRITY": Scd2IntegrityConfig,
}
