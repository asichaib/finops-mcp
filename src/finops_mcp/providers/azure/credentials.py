from __future__ import annotations

import json
import subprocess
import time
from datetime import datetime, timezone
from typing import Any

from azure.core.credentials import AccessToken
from azure.core.exceptions import ClientAuthenticationError


def _scope_to_resource(scope: str) -> str:
    return scope[: -len("/.default")] if scope.endswith("/.default") else scope


class AzureCliSubscriptionCredential:
    """Credential that gets tokens via `az account get-access-token --subscription <id>`.

    Why this exists: `AzureCliCredential(tenant_id=X)` calls `az ... --tenant X`,
    which fails when the CLI's signed-in identity (user or SP) isn't a member of
    tenant X. But `az ... --subscription <id>` resolves cross-tenant access
    natively — as long as `az account list` shows the sub, a token can be minted
    for the subscription's home tenant. This credential takes that working route.
    """

    def __init__(self, subscription_id: str):
        self.subscription_id = subscription_id

    def get_token(self, *scopes: str, **kwargs: Any) -> AccessToken:
        if not scopes:
            raise ValueError("At least one scope is required.")
        resource = _scope_to_resource(scopes[0])
        cmd = [
            "az", "account", "get-access-token",
            "--subscription", self.subscription_id,
            "--resource", resource,
            "-o", "json",
        ]
        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=30
            )
        except FileNotFoundError as e:
            raise ClientAuthenticationError(
                "Azure CLI (`az`) is not installed or not on PATH."
            ) from e
        except subprocess.TimeoutExpired as e:
            raise ClientAuthenticationError(
                "Azure CLI timed out while fetching a token."
            ) from e
        if result.returncode != 0:
            raise ClientAuthenticationError(
                (result.stderr or result.stdout).strip()
                or f"az exited with code {result.returncode}"
            )
        try:
            data = json.loads(result.stdout)
        except json.JSONDecodeError as e:
            raise ClientAuthenticationError(
                f"Azure CLI returned non-JSON output: {result.stdout[:200]}"
            ) from e
        return AccessToken(data["accessToken"], _parse_expiry(data))

    def close(self) -> None:
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return None


def _parse_expiry(data: dict[str, Any]) -> int:
    if "expires_on" in data:
        return int(data["expires_on"])
    raw = data.get("expiresOn")
    if isinstance(raw, str):
        try:
            dt = datetime.fromisoformat(raw)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return int(dt.timestamp())
        except ValueError:
            pass
    return int(time.time()) + 55 * 60
