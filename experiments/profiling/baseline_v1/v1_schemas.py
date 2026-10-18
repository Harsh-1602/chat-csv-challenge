"""Frozen copy of the v1 profile models, so the baseline stays reproducible as app schemas evolve."""

from typing import Literal

from pydantic import BaseModel

Scalar = str | int | float | bool | None
ColumnRole = Literal["identifier", "numeric", "datetime", "boolean", "categorical", "text"]


class ValueCount(BaseModel):
    value: Scalar
    count: int


class ColumnProfile(BaseModel):
    name: str
    data_type: str
    role: ColumnRole
    non_null_count: int
    null_count: int
    null_pct: float
    distinct_count: int
    min: Scalar = None
    max: Scalar = None
    mean: float | None = None
    median: float | None = None
    std: float | None = None
    outlier_count: int | None = None
    top_values: list[ValueCount] | None = None


class TableProfile(BaseModel):
    table_name: str
    file_name: str
    file_size_bytes: int
    row_count: int
    column_count: int
    duplicate_row_count: int
    primary_key: str | None
    columns: list[ColumnProfile]
    warnings: list[str]


class Relationship(BaseModel):
    from_table: str
    from_column: str
    to_table: str
    to_column: str
    cardinality: str
    match_rate: float
    orphan_count: int


class FailedFile(BaseModel):
    file_name: str
    error: str


class DatasetProfile(BaseModel):
    table_count: int
    total_rows: int
    tables: list[TableProfile]
    relationships: list[Relationship]
    failed_files: list[FailedFile]
