from __future__ import annotations

from datetime import date, datetime
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field

Cloud = Literal["azure", "aws", "gcp"]


class Severity(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class CostPoint(BaseModel):
    period_start: date
    period_end: date
    group: str
    amount: float
    currency: str


class CostSummary(BaseModel):
    cloud: Cloud
    scope: str
    days: int
    group_by: str
    currency: str
    total: float
    points: list[CostPoint]


class Finding(BaseModel):
    cloud: Cloud
    kind: str
    resource_id: str
    resource_name: str
    location: str | None = None
    monthly_cost_estimate: float | None = None
    currency: str | None = None
    severity: Severity = Severity.MEDIUM
    detected_at: datetime
    details: dict[str, Any] = Field(default_factory=dict)
    recommendation: str
