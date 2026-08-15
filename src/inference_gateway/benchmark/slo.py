"""Versioned workload SLO schema, hashing, and fail-closed lookup."""

from __future__ import annotations

import hashlib
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Annotated, Self

import yaml
from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, ValidationError, model_validator


def _decimal(value: object) -> Decimal:
    if isinstance(value, float):
        raise ValueError("SLO rates must be quoted decimals")
    try:
        return Decimal(str(value))
    except InvalidOperation as exc:
        raise ValueError("invalid decimal SLO rate") from exc


Rate = Annotated[Decimal, BeforeValidator(_decimal), Field(ge=0, le=1)]


class SLOTarget(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    p95_ttft_ms: int = Field(gt=0)
    p95_e2e_ms: int = Field(gt=0)
    max_error_rate: Rate
    min_quality_rate: Rate | None = None


class SLODocument(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    version: str = Field(min_length=1)
    effective_date: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    cells: dict[str, SLOTarget] = Field(min_length=1)

    @model_validator(mode="after")
    def workload_quality_rules(self) -> Self:
        for cell, target in self.cells.items():
            if not cell.startswith(("WL-01/", "WL-02/", "WL-03/")):
                raise ValueError(f"invalid SLO cell {cell!r}")
            if cell.startswith("WL-03/") and target.min_quality_rate is not None:
                raise ValueError("WL-03 cells must omit min_quality_rate")
            if not cell.startswith("WL-03/") and target.min_quality_rate is None:
                raise ValueError(f"objective SLO cell {cell!r} needs min_quality_rate")
        return self

    def require_cell(self, cell: str) -> SLOTarget:
        try:
            return self.cells[cell]
        except KeyError as exc:
            raise ValueError(f"missing SLO target for {cell}") from exc


def load_slo(path: str | Path) -> tuple[SLODocument, str]:
    config_path = Path(path)
    try:
        content = config_path.read_bytes()
        raw = yaml.safe_load(content.decode("utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise ValueError(f"cannot load SLO configuration {config_path}: {exc}") from exc
    try:
        document = SLODocument.model_validate(raw)
    except ValidationError as exc:
        raise ValueError(f"invalid SLO configuration {config_path}:\n{exc}") from exc
    return document, hashlib.sha256(content).hexdigest()
