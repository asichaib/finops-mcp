from __future__ import annotations

from typing import Protocol, runtime_checkable

from ..models import CostSummary, Finding


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
