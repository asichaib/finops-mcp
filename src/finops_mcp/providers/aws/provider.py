from __future__ import annotations

from contextlib import contextmanager
from datetime import date
from functools import cached_property

import boto3
from botocore.exceptions import (
    BotoCoreError,
    ClientError,
    NoCredentialsError,
)

from ...errors import FinOpsError
from ...models import CostChangeExplanation, CostSummary, Finding
from .cost import explain_cost_change_query, query_cost_summary
from .idle import find_idle


class AwsProvider:
    cloud = "aws"

    @cached_property
    def session(self) -> boto3.Session:
        return boto3.Session()

    @contextmanager
    def _friendly_errors(self, scope: str | None):
        scope_label = scope or "current account"
        try:
            yield
        except NoCredentialsError as e:
            raise FinOpsError(
                "No AWS credentials found. Configure via one of:\n"
                "  - `aws configure` (writes ~/.aws/credentials)\n"
                "  - AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY env vars\n"
                "  - SSO: `aws sso login --profile <name>` then "
                "AWS_PROFILE=<name>\n"
                "  - An instance/container IAM role (automatic on AWS-hosted runners)."
            ) from e
        except ClientError as e:
            err = e.response.get("Error", {}) if hasattr(e, "response") else {}
            code = err.get("Code", "")
            msg = err.get("Message", str(e))
            if code in (
                "AccessDenied",
                "AccessDeniedException",
                "UnauthorizedOperation",
            ):
                raise FinOpsError(
                    f"AWS access denied for {scope_label}: {msg}\n"
                    "Required permissions: `ce:GetCostAndUsage` for cost tools, "
                    "`ec2:Describe*` for idle-resource scans."
                ) from e
            if code == "OptInRequired":
                raise FinOpsError(
                    "AWS Cost Explorer is not enabled on this account. "
                    "Enable it in the Billing console → Cost Explorer → "
                    "Launch (takes ~24h for data to appear)."
                ) from e
            if code == "DataUnavailableException":
                raise FinOpsError(
                    "AWS Cost Explorer has no data for the requested window. "
                    "Cost data can lag up to 24h; try a wider window or a later date."
                ) from e
            if code in ("Throttling", "ThrottlingException", "TooManyRequestsException"):
                raise FinOpsError(
                    f"AWS throttled the request for {scope_label}. "
                    "Wait a few seconds and retry."
                ) from e
            if code == "ExpiredToken":
                raise FinOpsError(
                    "AWS credentials have expired. Re-authenticate "
                    "(e.g. `aws sso login` for SSO, or refresh STS creds)."
                ) from e
            raise FinOpsError(f"AWS API error [{code}]: {msg}") from e
        except BotoCoreError as e:
            raise FinOpsError(f"AWS SDK error: {e}") from e

    def _normalize_scope(self, scope: str | None) -> str | None:
        return scope.strip() if isinstance(scope, str) and scope.strip() else None

    def get_cost_summary(
        self, scope: str | None, days: int, group_by: str
    ) -> CostSummary:
        s = self._normalize_scope(scope)
        with self._friendly_errors(s):
            return query_cost_summary(self.session, s, days, group_by)

    def find_idle_resources(
        self, scope: str | None, kinds: list[str]
    ) -> list[Finding]:
        s = self._normalize_scope(scope)
        with self._friendly_errors(s):
            return find_idle(self.session, s, kinds)

    def explain_cost_change(
        self,
        scope: str | None,
        target_date: date,
        window_days: int,
        top_n: int,
    ) -> CostChangeExplanation:
        s = self._normalize_scope(scope)
        with self._friendly_errors(s):
            return explain_cost_change_query(
                self.session, s, target_date, window_days, top_n
            )
