from __future__ import annotations

import logging
import os
from datetime import date, datetime, timedelta
from typing import Literal

from mcp.server.fastmcp import FastMCP

from .models import CostChangeExplanation, CostSummary, Finding
from .providers.aws.provider import AwsProvider
from .providers.azure.provider import AzureProvider

# SDK credential chains emit noisy warnings on each failed link; our
# friendly-error wrapper already translates the final outcome. Opt back in
# with FINOPS_MCP_VERBOSE=1.
if not os.getenv("FINOPS_MCP_VERBOSE"):
    for _name in ("azure", "azure.identity", "azure.core", "boto3", "botocore"):
        logging.getLogger(_name).setLevel(logging.CRITICAL)

mcp = FastMCP("finops-mcp")

_Cloud = Literal["azure", "aws"]
_GroupBy = Literal[
    "service",
    # Azure
    "resource_group",
    "location",
    "subscription",
    # AWS
    "account",
    "region",
    "usage_type",
    "instance_type",
    #
    "none",
]

_providers: dict[str, object] = {}


def _provider(cloud: str):
    if cloud not in _providers:
        if cloud == "azure":
            _providers[cloud] = AzureProvider()
        elif cloud == "aws":
            _providers[cloud] = AwsProvider()
        else:
            raise ValueError(
                f"Cloud '{cloud}' is not yet supported. "
                "Supported: 'azure', 'aws'. GCP and Kubernetes are on the roadmap."
            )
    return _providers[cloud]


def _resolve_scope(scope: str | None, cloud: str) -> str | None:
    if scope and scope.strip():
        return scope.strip()
    if cloud == "azure":
        env = os.getenv("AZURE_SUBSCRIPTION_ID")
        if env:
            return env
        raise ValueError(
            "scope is required for Azure. Pass a subscription ID or ARM scope "
            "path (e.g. /subscriptions/<id>), or set AZURE_SUBSCRIPTION_ID."
        )
    if cloud == "aws":
        # AWS scope is optional — None means "whichever account the caller is in".
        return None
    raise ValueError(f"Unsupported cloud '{cloud}'.")


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
def get_cost_summary(
    cloud: _Cloud = "azure",
    scope: str | None = None,
    days: int = 30,
    group_by: _GroupBy = "service",
) -> CostSummary:
    """Return actual cloud spend over the last N days, grouped by service etc.

    `scope`:
      - Azure: subscription ID or ARM scope path (e.g. /subscriptions/<id>
        or /subscriptions/<id>/resourceGroups/<rg>). Defaults to
        AZURE_SUBSCRIPTION_ID.
      - AWS: optional 12-digit linked account ID filter. Omit for the
        calling identity's own account.

    `group_by`:
      - Azure: service, resource_group, location, subscription, none
      - AWS: service, account, region, usage_type, instance_type, none
    """
    resolved = _resolve_scope(scope, cloud)
    return _provider(cloud).get_cost_summary(resolved, days, group_by)


@mcp.tool()
def explain_cost_change(
    date: str,
    cloud: _Cloud = "azure",
    scope: str | None = None,
    window_days: int = 7,
    top_n: int = 10,
) -> CostChangeExplanation:
    """Explain what drove a cloud spend change around a given date.

    Compares actual spend by service in the `window_days`-day window ending
    on `date` against the equivalent window immediately before. Returns
    ranked service-level contributors (increases AND decreases), each one's
    share of the total delta, and the total change.

    `date` accepts ISO `YYYY-MM-DD`, `today`, or `yesterday`.
    `window_days` is clamped to 1..90. `top_n` is clamped to 1..50.

    Example: "why did my AWS spend jump last Tuesday?"
    → explain_cost_change(cloud="aws", date="2026-04-14", window_days=7)
    """
    resolved = _resolve_scope(scope, cloud)
    target_date = _parse_date(date)
    return _provider(cloud).explain_cost_change(
        resolved, target_date, window_days, top_n
    )


@mcp.tool()
def find_idle_resources(
    cloud: _Cloud = "azure",
    scope: str | None = None,
    kinds: list[str] | None = None,
) -> list[Finding]:
    """Find idle/wasted resources — returns Findings with recommendations.

    `kinds` (defaults to all):
      - Azure: disk, public_ip, nic, snapshot (scoped to a subscription)
      - AWS: disk, public_ip, nic, snapshot (scanned across all enabled regions)
    """
    resolved = _resolve_scope(scope, cloud)
    default_kinds = ["disk", "public_ip", "nic", "snapshot"]
    return _provider(cloud).find_idle_resources(resolved, kinds or default_kinds)


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
