from typing import Protocol, TypedDict


class ColumnInfo(TypedDict):
    name: str
    data_type: str
    nullable: bool


class SchemaInfo(TypedDict):
    object: str
    columns: list[ColumnInfo]


class Connector(Protocol):
    type: str

    def run_query(self, sql: str) -> list[dict]: ...

    def get_schema(self, object_name: str) -> SchemaInfo: ...

    def close(self) -> None: ...
