from __future__ import annotations

import re
from functools import lru_cache
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

_TENANT_RE = re.compile(
    r"login\.(?:microsoftonline|windows)\.(?:com|net|us)/([0-9a-f-]{36})",
    re.IGNORECASE,
)


@lru_cache(maxsize=256)
def discover_subscription_tenant(subscription_id: str) -> str | None:
    """Return the Entra tenant ID that owns an Azure subscription.

    Uses ARM's unauthenticated tenant-discovery pattern: an anonymous GET to
    `/subscriptions/<id>` returns 401 with a `WWW-Authenticate` header whose
    `authorization_uri` encodes the tenant. No credentials required.
    """
    url = (
        f"https://management.azure.com/subscriptions/{subscription_id}"
        f"?api-version=2022-12-01"
    )
    try:
        with urlopen(Request(url, method="GET"), timeout=10):
            return None
    except HTTPError as e:
        headers = getattr(e, "headers", None)
        auth = headers.get("WWW-Authenticate", "") if headers is not None else ""
        match = _TENANT_RE.search(auth or "")
        return match.group(1) if match else None
    except (URLError, TimeoutError, OSError):
        return None
