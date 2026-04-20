from __future__ import annotations

from datetime import date, timedelta
from typing import Any

import boto3

from ...models import (
    CostChangeExplanation,
    CostContributor,
    CostPoint,
    CostSummary,
)

_GROUP_BY = {
    "service": {"Type": "DIMENSION", "Key": "SERVICE"},
    "account": {"Type": "DIMENSION", "Key": "LINKED_ACCOUNT"},
    "region": {"Type": "DIMENSION", "Key": "REGION"},
    "usage_type": {"Type": "DIMENSION", "Key": "USAGE_TYPE"},
    "instance_type": {"Type": "DIMENSION", "Key": "INSTANCE_TYPE"},
}


def _ce_client(session: boto3.Session):
    # Cost Explorer is a global service with the endpoint in us-east-1.
    return session.client("ce", region_name="us-east-1")


def _linked_account_filter(scope: str | None) -> dict | None:
    """If scope is a 12-digit account ID, filter to that linked account."""
    if scope and scope.isdigit() and len(scope) == 12:
        return {
            "Dimensions": {"Key": "LINKED_ACCOUNT", "Values": [scope]}
        }
    return None


def _granularity(days: int) -> str:
    return "DAILY" if days <= 31 else "MONTHLY"


def query_cost_summary(
    session: boto3.Session,
    scope: str | None,
    days: int,
    group_by: str,
) -> CostSummary:
    days = max(1, min(days, 364))
    end_date = date.today()
    start_date = end_date - timedelta(days=days)

    kwargs: dict[str, Any] = {
        # CE End is exclusive.
        "TimePeriod": {
            "Start": start_date.isoformat(),
            "End": end_date.isoformat(),
        },
        "Granularity": _granularity(days),
        "Metrics": ["UnblendedCost"],
    }

    if group_by != "none":
        dim = _GROUP_BY.get(group_by)
        if dim is None:
            raise ValueError(
                f"Unknown group_by '{group_by}'. Use one of: "
                f"{', '.join(_GROUP_BY)}, or 'none'."
            )
        kwargs["GroupBy"] = [dim]

    scope_filter = _linked_account_filter(scope)
    if scope_filter:
        kwargs["Filter"] = scope_filter

    client = _ce_client(session)
    resp = client.get_cost_and_usage(**kwargs)

    totals: dict[str, float] = {}
    currency = "USD"
    for result in resp.get("ResultsByTime", []):
        if group_by == "none":
            total = result.get("Total", {}).get("UnblendedCost", {})
            amount = float(total.get("Amount", "0") or 0)
            currency = total.get("Unit", currency)
            totals["total"] = totals.get("total", 0.0) + amount
            continue
        for group in result.get("Groups", []):
            key = group["Keys"][0] if group.get("Keys") else "unknown"
            metric = group.get("Metrics", {}).get("UnblendedCost", {})
            amount = float(metric.get("Amount", "0") or 0)
            currency = metric.get("Unit", currency)
            totals[key] = totals.get(key, 0.0) + amount

    points = [
        CostPoint(
            period_start=start_date,
            period_end=end_date,
            group=key,
            amount=round(amt, 2),
            currency=currency,
        )
        for key, amt in totals.items()
    ]
    points.sort(key=lambda p: p.amount, reverse=True)

    return CostSummary(
        cloud="aws",
        scope=scope or "current-account",
        days=days,
        group_by=group_by,
        currency=currency,
        total=round(sum(totals.values()), 2),
        points=points,
    )


def query_service_costs(
    session: boto3.Session,
    scope: str | None,
    start_date: date,
    end_date: date,
) -> tuple[dict[str, float], str]:
    """Return ({service: cost}, currency) for [start_date, end_date] inclusive."""
    kwargs: dict[str, Any] = {
        "TimePeriod": {
            "Start": start_date.isoformat(),
            # CE End is exclusive; add a day to include end_date.
            "End": (end_date + timedelta(days=1)).isoformat(),
        },
        "Granularity": _granularity((end_date - start_date).days + 1),
        "Metrics": ["UnblendedCost"],
        "GroupBy": [{"Type": "DIMENSION", "Key": "SERVICE"}],
    }
    scope_filter = _linked_account_filter(scope)
    if scope_filter:
        kwargs["Filter"] = scope_filter

    client = _ce_client(session)
    resp = client.get_cost_and_usage(**kwargs)

    out: dict[str, float] = {}
    currency = "USD"
    for result in resp.get("ResultsByTime", []):
        for group in result.get("Groups", []):
            svc = group["Keys"][0] if group.get("Keys") else "unknown"
            metric = group.get("Metrics", {}).get("UnblendedCost", {})
            amount = float(metric.get("Amount", "0") or 0)
            currency = metric.get("Unit", currency)
            out[svc] = out.get(svc, 0.0) + amount
    return out, currency


def explain_cost_change_query(
    session: boto3.Session,
    scope: str | None,
    target_date: date,
    window_days: int,
    top_n: int,
) -> CostChangeExplanation:
    window_days = max(1, min(window_days, 90))
    top_n = max(1, min(top_n, 50))

    target_end = target_date
    target_start = target_end - timedelta(days=window_days - 1)
    baseline_end = target_start - timedelta(days=1)
    baseline_start = baseline_end - timedelta(days=window_days - 1)

    target, cur_t = query_service_costs(session, scope, target_start, target_end)
    baseline, cur_b = query_service_costs(
        session, scope, baseline_start, baseline_end
    )
    currency = cur_t or cur_b or "USD"

    services = set(target) | set(baseline)
    rows: list[tuple[str, float, float, float, float | None]] = []
    for svc in services:
        t = target.get(svc, 0.0)
        b = baseline.get(svc, 0.0)
        delta = t - b
        delta_pct = (delta / b * 100.0) if b else None
        rows.append((svc, b, t, delta, delta_pct))

    baseline_total = sum(baseline.values())
    target_total = sum(target.values())
    total_delta = target_total - baseline_total
    total_delta_pct = (
        (total_delta / baseline_total * 100.0) if baseline_total else None
    )

    rows.sort(key=lambda r: abs(r[3]), reverse=True)
    top = rows[:top_n]

    contributors = [
        CostContributor(
            service=svc,
            baseline=round(b, 2),
            target=round(t, 2),
            delta=round(delta, 2),
            delta_pct=round(delta_pct, 1) if delta_pct is not None else None,
            share_of_change=(
                round(delta / total_delta, 3) if total_delta else 0.0
            ),
        )
        for svc, b, t, delta, delta_pct in top
    ]

    return CostChangeExplanation(
        cloud="aws",
        scope=scope or "current-account",
        baseline_start=baseline_start,
        baseline_end=baseline_end,
        target_start=target_start,
        target_end=target_end,
        baseline_total=round(baseline_total, 2),
        target_total=round(target_total, 2),
        total_delta=round(total_delta, 2),
        total_delta_pct=(
            round(total_delta_pct, 1) if total_delta_pct is not None else None
        ),
        currency=currency,
        top_contributors=contributors,
    )
