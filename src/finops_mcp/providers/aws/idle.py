from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

import boto3

from ...models import Finding, Severity

# Approximate on-demand pricing (USD/mo) — used only for "potential savings"
# hints shown to the LLM. Not authoritative; real pricing is region-specific
# and can be looked up via the AWS Pricing API in a later version.
_EBS_PRICE_PER_GB_MO = {
    "gp2": 0.10,
    "gp3": 0.08,
    "io1": 0.125,
    "io2": 0.125,
    "st1": 0.045,
    "sc1": 0.015,
    "standard": 0.05,
}
_EIP_UNASSOCIATED_PRICE_MO = 3.65
_SNAPSHOT_PRICE_PER_GB_MO = 0.05


def _enabled_regions(session: boto3.Session) -> list[str]:
    ec2 = session.client("ec2", region_name="us-east-1")
    resp = ec2.describe_regions(AllRegions=False)
    return sorted(r["RegionName"] for r in resp.get("Regions", []))


def _name_tag(tags: list[dict] | None, fallback: str) -> str:
    for t in tags or []:
        if t.get("Key") == "Name":
            return t.get("Value") or fallback
    return fallback


def _scan_region(
    session: boto3.Session, region: str, kinds: set[str]
) -> list[Finding]:
    findings: list[Finding] = []
    now = datetime.now(timezone.utc)
    ec2 = session.client("ec2", region_name=region)

    if "disk" in kinds:
        for page in ec2.get_paginator("describe_volumes").paginate(
            Filters=[{"Name": "status", "Values": ["available"]}]
        ):
            for vol in page.get("Volumes", []):
                vtype = vol.get("VolumeType", "")
                size = int(vol.get("Size", 0) or 0)
                price = _EBS_PRICE_PER_GB_MO.get(vtype, 0.10)
                monthly = round(size * price, 2)
                name = _name_tag(vol.get("Tags"), vol["VolumeId"])
                findings.append(
                    Finding(
                        cloud="aws",
                        kind="idle_disk",
                        resource_id=vol["VolumeId"],
                        resource_name=name,
                        location=region,
                        monthly_cost_estimate=monthly,
                        currency="USD",
                        severity=Severity.MEDIUM,
                        detected_at=now,
                        details={
                            "size_gb": size,
                            "type": vtype,
                            "created_at": (
                                vol["CreateTime"].isoformat()
                                if vol.get("CreateTime")
                                else None
                            ),
                        },
                        recommendation=(
                            f"EBS volume {vol['VolumeId']} ({size} GB {vtype}) "
                            f"is unattached in {region}. "
                            f"Snapshot and delete (~${monthly}/mo)."
                        ),
                    )
                )

    if "public_ip" in kinds:
        resp = ec2.describe_addresses()
        for addr in resp.get("Addresses", []):
            if addr.get("AssociationId"):
                continue
            findings.append(
                Finding(
                    cloud="aws",
                    kind="idle_public_ip",
                    resource_id=(
                        addr.get("AllocationId") or addr.get("PublicIp") or "?"
                    ),
                    resource_name=addr.get("PublicIp") or "",
                    location=region,
                    monthly_cost_estimate=_EIP_UNASSOCIATED_PRICE_MO,
                    currency="USD",
                    severity=Severity.LOW,
                    detected_at=now,
                    details={"public_ip": addr.get("PublicIp")},
                    recommendation=(
                        f"Elastic IP {addr.get('PublicIp')} in {region} is "
                        f"unassociated. Release it "
                        f"(~${_EIP_UNASSOCIATED_PRICE_MO}/mo)."
                    ),
                )
            )

    if "snapshot" in kinds:
        for page in ec2.get_paginator("describe_snapshots").paginate(
            OwnerIds=["self"]
        ):
            for snap in page.get("Snapshots", []):
                created = snap.get("StartTime")
                if created is None:
                    continue
                age_days = (now - created).days
                if age_days <= 90:
                    continue
                size = int(snap.get("VolumeSize", 0) or 0)
                monthly = round(size * _SNAPSHOT_PRICE_PER_GB_MO, 2)
                findings.append(
                    Finding(
                        cloud="aws",
                        kind="old_snapshot",
                        resource_id=snap["SnapshotId"],
                        resource_name=(
                            snap.get("Description") or snap["SnapshotId"]
                        ),
                        location=region,
                        monthly_cost_estimate=monthly,
                        currency="USD",
                        severity=Severity.LOW,
                        detected_at=now,
                        details={"age_days": age_days, "size_gb": size},
                        recommendation=(
                            f"Snapshot {snap['SnapshotId']} in {region} is "
                            f"{age_days} days old ({size} GB, ~${monthly}/mo). "
                            "Review retention."
                        ),
                    )
                )

    if "nic" in kinds:
        for page in ec2.get_paginator("describe_network_interfaces").paginate(
            Filters=[{"Name": "status", "Values": ["available"]}]
        ):
            for nic in page.get("NetworkInterfaces", []):
                findings.append(
                    Finding(
                        cloud="aws",
                        kind="orphaned_nic",
                        resource_id=nic["NetworkInterfaceId"],
                        resource_name=(
                            nic.get("Description") or nic["NetworkInterfaceId"]
                        ),
                        location=region,
                        severity=Severity.LOW,
                        detected_at=now,
                        details={},
                        recommendation=(
                            f"ENI {nic['NetworkInterfaceId']} in {region} is "
                            "available (detached). Delete if unused."
                        ),
                    )
                )

    return findings


def find_idle(
    session: boto3.Session,
    scope: str | None,
    kinds: list[str],
) -> list[Finding]:
    """Scan idle EBS, EIPs, snapshots, ENIs across all enabled regions.

    `scope` is currently informational for AWS; idle scans always run in the
    current identity's account. Per-region errors are silently skipped so a
    single opt-in region failure doesn't break the whole scan.
    """
    kinds_set = set(kinds)
    regions = _enabled_regions(session)
    all_findings: list[Finding] = []

    with ThreadPoolExecutor(max_workers=min(12, max(1, len(regions)))) as pool:
        futures = {
            pool.submit(_scan_region, session, region, kinds_set): region
            for region in regions
        }
        for fut in as_completed(futures):
            try:
                all_findings.extend(fut.result())
            except Exception:
                continue

    return all_findings
