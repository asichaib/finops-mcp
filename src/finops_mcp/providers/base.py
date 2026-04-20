from __future__ import annotations

from datetime import date
from typing import Protocol, runtime_checkable

from ..models import CostChangeExplanation, CostSummary, Finding


@runtime_checkable
class Provider(Protocol):
    """Cloud provider interface. Every cloud (azure, aws, gcp) implements this.

    Keeping the surface small and cloud-agnostic is the point: it's what lets
    v0.2 (AWS) and v0.3 (GCP) be additive instead of a rewrite.
    """

    cloud: str

    def get_cost_summary(
        self, scope: str, days: int, group_by: str
    ) -> CostSummary: ...

    def find_idle_resources(
        self, scope: str, kinds: list[str]
    ) -> list[Finding]: ...

    def explain_cost_change(
        self,
        scope: str,
        target_date: date,
        window_days: int,
        top_n: int,
    ) -> CostChangeExplanation: ...
