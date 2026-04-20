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

from ...models import CostPoint, CostSummary

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
