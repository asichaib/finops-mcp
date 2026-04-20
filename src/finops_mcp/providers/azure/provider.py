from __future__ import annotations

import os
from contextlib import contextmanager
from datetime import date

from azure.core.exceptions import (
    ClientAuthenticationError,
    HttpResponseError,
    ResourceNotFoundError,
)
from azure.identity import (
    ChainedTokenCredential,
    EnvironmentCredential,
    ManagedIdentityCredential,
)

from ...errors import FinOpsError
from ...models import CostChangeExplanation, CostSummary, Finding
from .cost import explain_cost_change_query, query_cost_summary
from .credentials import AzureCliSubscriptionCredential
from .idle import find_idle
from .tenant import discover_subscription_tenant


def _subscription_from_scope(scope: str) -> str:
    if scope.startswith("/subscriptions/"):
        return scope.split("/")[2]
    return scope


def _build_credential(subscription_id: str) -> ChainedTokenCredential:
    """Credential chain biased to the given subscription.

    Order: env-var service principal (if set) → az CLI scoped to this sub →
    managed identity. The az-CLI-with-subscription hop is what makes
    cross-tenant queries work transparently: it uses `az ... --subscription X`
    which resolves the right tenant natively.
    """
    parts: list = []
    if (
        os.getenv("AZURE_CLIENT_ID")
        and os.getenv("AZURE_CLIENT_SECRET")
        and os.getenv("AZURE_TENANT_ID")
    ):
        parts.append(EnvironmentCredential())
    parts.append(AzureCliSubscriptionCredential(subscription_id))
    try:
        parts.append(ManagedIdentityCredential())
    except Exception:
        pass
    return ChainedTokenCredential(*parts)


class AzureProvider:
    cloud = "azure"

    def __init__(self) -> None:
        self._creds: dict[str, ChainedTokenCredential] = {}

    def _credential_for(self, scope: str) -> ChainedTokenCredential:
        sub = _subscription_from_scope(scope)
        if sub not in self._creds:
            self._creds[sub] = _build_credential(sub)
        return self._creds[sub]

    @contextmanager
    def _friendly_errors(self, scope: str):
        sub = _subscription_from_scope(scope)
        try:
            yield
        except ClientAuthenticationError as e:
            msg = str(e)
            tenant_auth_markers = (
                "InvalidAuthenticationTokenTenant",
                "wrong issuer",
                "AADSTS50076",
                "AADSTS700016",
                "AADSTS65001",
                "AADSTS90002",
                "Could not retrieve credential from local cache",
                "Interactive authentication is needed",
                "Please run: az login",
                "Please run 'az login'",
            )
            if any(m in msg for m in tenant_auth_markers):
                tenant = discover_subscription_tenant(sub) or "<target-tenant-id>"
                raise FinOpsError(
                    f"Not authenticated for Azure subscription {sub}. "
                    f"It lives in tenant {tenant}, but no credential "
                    f"(az CLI, service principal env vars, or managed identity) "
                    f"currently has a token for that tenant.\n\n"
                    f"Fix: run  az login --tenant {tenant}\n"
                    f"Or: set AZURE_CLIENT_ID / AZURE_CLIENT_SECRET / "
                    f"AZURE_TENANT_ID={tenant} for a service principal in that tenant."
                ) from e
            raise FinOpsError(f"Azure authentication failed: {msg}") from e
        except ResourceNotFoundError as e:
            raise FinOpsError(
                f"Azure subscription {sub} not found or not visible to the current identity."
            ) from e
        except HttpResponseError as e:
            status = getattr(e, "status_code", None) or getattr(
                getattr(e, "response", None), "status_code", None
            )
            msg = str(e)
            if status == 403 or "AuthorizationFailed" in msg:
                raise FinOpsError(
                    f"Azure authorization failed for subscription {sub}: {msg}. "
                    "The signed-in identity needs 'Cost Management Reader' and 'Reader' "
                    "on the target scope."
                ) from e
            if status == 429:
                raise FinOpsError(
                    f"Azure rate-limited the request for subscription {sub}. "
                    "Wait a few seconds and retry."
                ) from e
            raise

    def get_cost_summary(
        self, scope: str, days: int, group_by: str
    ) -> CostSummary:
        cred = self._credential_for(scope)
        with self._friendly_errors(scope):
            return query_cost_summary(cred, scope, days, group_by)

    def find_idle_resources(
        self, scope: str, kinds: list[str]
    ) -> list[Finding]:
        cred = self._credential_for(scope)
        with self._friendly_errors(scope):
            return find_idle(cred, scope, kinds)

    def explain_cost_change(
        self,
        scope: str,
        target_date: date,
        window_days: int,
        top_n: int,
    ) -> CostChangeExplanation:
        cred = self._credential_for(scope)
        with self._friendly_errors(scope):
            return explain_cost_change_query(
                cred, scope, target_date, window_days, top_n
            )
