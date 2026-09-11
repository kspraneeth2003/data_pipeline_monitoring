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


CONFIG_SCHEMAS_BY_TYPE: dict[str, type[BaseModel]] = {
    "ROW_COUNT": RowCountConfig,
    "FRESHNESS": FreshnessConfig,
    "NULL_RATE": NullRateConfig,
    "SCHEMA_DRIFT": SchemaDriftConfig,
    "CROSS_SOURCE_PARITY": CrossSourceParityConfig,
}
