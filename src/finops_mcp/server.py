from __future__ import annotations

import os
from typing import Literal

from mcp.server.fastmcp import FastMCP

from .models import CostSummary, Finding
from .providers.azure.provider import AzureProvider

mcp = FastMCP("finops-mcp")

_providers: dict[str, object] = {}


def _provider(cloud: str):
    if cloud not in _providers:
        if cloud == "azure":
            _providers[cloud] = AzureProvider()
        else:
            raise ValueError(
                f"Cloud '{cloud}' is not yet supported in v0.1. "
                "Azure is the only provider right now; AWS and GCP are on the roadmap."
            )
    return _providers[cloud]


def _resolve_scope(scope: str | None, cloud: str) -> str:
    if scope:
        return scope
    if cloud == "azure":
        env = os.getenv("AZURE_SUBSCRIPTION_ID")
        if env:
            return env
    raise ValueError(
        "scope is required. For Azure, pass a subscription ID or a full scope path "
        "(e.g. /subscriptions/<id>), or set AZURE_SUBSCRIPTION_ID."
    )


@mcp.tool()
def get_cost_summary(
    scope: str | None = None,
    days: int = 30,
    group_by: Literal[
        "service", "resource_group", "location", "subscription", "none"
    ] = "service",
    cloud: Literal["azure"] = "azure",
) -> CostSummary:
    """Return actual cloud spend over the last N days, grouped by service/RG/location.

    Azure: `scope` is a subscription ID or full ARM scope path
    (e.g. /subscriptions/<id> or /subscriptions/<id>/resourceGroups/<rg>).
    Defaults to AZURE_SUBSCRIPTION_ID if unset.
    """
    full_scope = _resolve_scope(scope, cloud)
    return _provider(cloud).get_cost_summary(full_scope, days, group_by)


@mcp.tool()
def find_idle_resources(
    scope: str | None = None,
    kinds: list[str] | None = None,
    cloud: Literal["azure"] = "azure",
) -> list[Finding]:
    """Find idle/wasted resources in a cloud scope.

    Azure `kinds` options: "disk", "public_ip", "nic", "snapshot".
    Defaults to all four. Returns Findings with a recommendation each.
    """
    full_scope = _resolve_scope(scope, cloud)
    default_kinds = ["disk", "public_ip", "nic", "snapshot"]
    return _provider(cloud).find_idle_resources(full_scope, kinds or default_kinds)


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
