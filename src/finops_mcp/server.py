from __future__ import annotations

import logging
import os
from datetime import date, datetime, timedelta
from typing import Literal

from mcp.server.fastmcp import FastMCP

from .models import CostChangeExplanation, CostSummary, Finding
from .providers.azure.provider import AzureProvider

# Azure SDK credential chains emit noisy warnings to stderr on each failed
# link; our friendly-error wrapper already translates the final outcome.
# Users can re-enable with FINOPS_MCP_VERBOSE=1.
if not os.getenv("FINOPS_MCP_VERBOSE"):
    for _name in ("azure", "azure.identity", "azure.core"):
        logging.getLogger(_name).setLevel(logging.CRITICAL)

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


def _parse_date(value: str) -> date:
    v = value.strip().lower()
    today = date.today()
    if v in ("today", "now"):
        return today
    if v == "yesterday":
        return today - timedelta(days=1)
    try:
        return datetime.strptime(v, "%Y-%m-%d").date()
    except ValueError as e:
        raise ValueError(
            f"Unrecognized date '{value}'. Use YYYY-MM-DD, 'today', or 'yesterday'."
        ) from e


@mcp.tool()
def explain_cost_change(
    date: str,
    scope: str | None = None,
    window_days: int = 7,
    top_n: int = 10,
    cloud: Literal["azure"] = "azure",
) -> CostChangeExplanation:
    """Explain what drove a cloud spend change around a given date.

    Compares actual spend by service in the `window_days`-day window ending
    on `date` against the equivalent window immediately before. Returns
    ranked service-level contributors to the change (increases and decreases),
    their share of the total delta, and total change.

    `date` accepts ISO `YYYY-MM-DD`, `today`, or `yesterday`.
    `window_days` clamps to 1..90. `top_n` clamps to 1..50.

    Example: "why did my Azure spend jump last Tuesday?"
    → explain_cost_change(date="2026-04-14", window_days=7)
    """
    full_scope = _resolve_scope(scope, cloud)
    target_date = _parse_date(date)
    return _provider(cloud).explain_cost_change(
        full_scope, target_date, window_days, top_n
    )


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
