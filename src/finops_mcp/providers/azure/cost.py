from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone

from azure.mgmt.costmanagement import CostManagementClient
from azure.mgmt.costmanagement.models import (
    QueryAggregation,
    QueryDataset,
    QueryDefinition,
    QueryGrouping,
    QueryTimePeriod,
)

from ...models import (
    CostChangeExplanation,
    CostContributor,
    CostPoint,
    CostSummary,
)

_GROUP_BY_DIM = {
    "service": "ServiceName",
    "resource_group": "ResourceGroup",
    "location": "ResourceLocation",
    "subscription": "SubscriptionName",
}


def _normalize_scope(scope: str) -> str:
    return scope if scope.startswith("/") else f"/subscriptions/{scope}"


def query_cost_summary(
    credential, scope: str, days: int, group_by: str
) -> CostSummary:
    client = CostManagementClient(credential)

    # Azure Cost Management caps history at <1 year per query.
    days = max(1, min(days, 364))
    end_date = date.today()
    start_date = end_date - timedelta(days=days)
    start_dt = datetime.combine(start_date, time.min, tzinfo=timezone.utc)
    end_dt = datetime.combine(end_date, time.max, tzinfo=timezone.utc)

    grouping: list[QueryGrouping] = []
    if group_by != "none":
        dim = _GROUP_BY_DIM.get(group_by)
        if dim is None:
            raise ValueError(
                f"Unknown group_by '{group_by}'. "
                f"Use one of: {', '.join(_GROUP_BY_DIM)}, or 'none'."
            )
        grouping = [QueryGrouping(type="Dimension", name=dim)]

    query = QueryDefinition(
        type="ActualCost",
        timeframe="Custom",
        time_period=QueryTimePeriod(from_property=start_dt, to=end_dt),
        dataset=QueryDataset(
            granularity="None",
            aggregation={
                "totalCost": QueryAggregation(name="Cost", function="Sum"),
            },
            grouping=grouping,
        ),
    )

    full_scope = _normalize_scope(scope)
    result = client.query.usage(scope=full_scope, parameters=query)

    col_index = {c.name.lower(): i for i, c in enumerate(result.columns or [])}
    cost_i = col_index.get("cost") or col_index.get("pretaxcost")
    currency_i = col_index.get("currency")
    group_i = next(
        (
            i
            for name, i in col_index.items()
            if name not in {"cost", "pretaxcost", "currency"}
        ),
        None,
    )

    if cost_i is None:
        return CostSummary(
            cloud="azure",
            scope=full_scope,
            days=days,
            group_by=group_by,
            currency="USD",
            total=0.0,
            points=[],
        )

    points: list[CostPoint] = []
    total = 0.0
    currency = "USD"
    for row in result.rows or []:
        amount = float(row[cost_i])
        if currency_i is not None:
            currency = str(row[currency_i])
        group_val = str(row[group_i]) if group_i is not None else "total"
        points.append(
            CostPoint(
                period_start=start_date,
                period_end=end_date,
                group=group_val,
                amount=round(amount, 2),
                currency=currency,
            )
        )
        total += amount

    points.sort(key=lambda p: p.amount, reverse=True)

    return CostSummary(
        cloud="azure",
        scope=full_scope,
        days=days,
        group_by=group_by,
        currency=currency,
        total=round(total, 2),
        points=points,
    )


def query_service_costs(
    credential, scope: str, start_date: date, end_date: date
) -> tuple[dict[str, float], str]:
    """Return ({service_name: cost}, currency) for the date range."""
    client = CostManagementClient(credential)
    start_dt = datetime.combine(start_date, time.min, tzinfo=timezone.utc)
    end_dt = datetime.combine(end_date, time.max, tzinfo=timezone.utc)

    query = QueryDefinition(
        type="ActualCost",
        timeframe="Custom",
        time_period=QueryTimePeriod(from_property=start_dt, to=end_dt),
        dataset=QueryDataset(
            granularity="None",
            aggregation={
                "totalCost": QueryAggregation(name="Cost", function="Sum"),
            },
            grouping=[QueryGrouping(type="Dimension", name="ServiceName")],
        ),
    )
    full_scope = _normalize_scope(scope)
    result = client.query.usage(scope=full_scope, parameters=query)

    col_index = {c.name.lower(): i for i, c in enumerate(result.columns or [])}
    cost_i = col_index.get("cost") or col_index.get("pretaxcost")
    currency_i = col_index.get("currency")
    service_i = col_index.get("servicename")

    out: dict[str, float] = {}
    currency = "USD"
    if cost_i is None:
        return out, currency
    for row in result.rows or []:
        amount = float(row[cost_i])
        service = str(row[service_i]) if service_i is not None else "unknown"
        if currency_i is not None:
            currency = str(row[currency_i])
        out[service] = out.get(service, 0.0) + amount
    return out, currency


def explain_cost_change_query(
    credential,
    scope: str,
    target_date: date,
    window_days: int,
    top_n: int,
) -> CostChangeExplanation:
    """Diff service-level spend for the `window_days` ending on target_date
    vs. the immediately preceding equal-length window.
    """
    window_days = max(1, min(window_days, 90))
    top_n = max(1, min(top_n, 50))

    target_end = target_date
    target_start = target_end - timedelta(days=window_days - 1)
    baseline_end = target_start - timedelta(days=1)
    baseline_start = baseline_end - timedelta(days=window_days - 1)

    target, cur_t = query_service_costs(credential, scope, target_start, target_end)
    baseline, cur_b = query_service_costs(
        credential, scope, baseline_start, baseline_end
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
        cloud="azure",
        scope=_normalize_scope(scope),
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
