from __future__ import annotations

from datetime import datetime, timezone

from azure.mgmt.compute import ComputeManagementClient
from azure.mgmt.network import NetworkManagementClient

from ...models import Finding, Severity


def _subscription_from_scope(scope: str) -> str:
    if scope.startswith("/subscriptions/"):
        return scope.split("/")[2]
    return scope


def find_idle(credential, scope: str, kinds: list[str]) -> list[Finding]:
    sub = _subscription_from_scope(scope)
    kinds_set = set(kinds)
    findings: list[Finding] = []
    now = datetime.now(timezone.utc)

    compute: ComputeManagementClient | None = None
    network: NetworkManagementClient | None = None

    def _compute() -> ComputeManagementClient:
        nonlocal compute
        if compute is None:
            compute = ComputeManagementClient(credential, sub)
        return compute

    def _network() -> NetworkManagementClient:
        nonlocal network
        if network is None:
            network = NetworkManagementClient(credential, sub)
        return network

    if "disk" in kinds_set:
        for disk in _compute().disks.list():
            state = (getattr(disk, "disk_state", "") or "").lower()
            if disk.managed_by is None and state in {"unattached", ""}:
                findings.append(
                    Finding(
                        cloud="azure",
                        kind="idle_disk",
                        resource_id=disk.id,
                        resource_name=disk.name,
                        location=disk.location,
                        severity=Severity.MEDIUM,
                        detected_at=now,
                        details={
                            "size_gb": disk.disk_size_gb,
                            "sku": disk.sku.name if disk.sku else None,
                            "created_at": (
                                disk.time_created.isoformat()
                                if disk.time_created
                                else None
                            ),
                        },
                        recommendation=(
                            f"Managed disk '{disk.name}' ({disk.disk_size_gb} GB, "
                            f"{disk.sku.name if disk.sku else 'unknown SKU'}) is unattached. "
                            "Snapshot and delete if unused."
                        ),
                    )
                )

    if "public_ip" in kinds_set:
        for ip in _network().public_ip_addresses.list_all():
            if ip.ip_configuration is None and ip.nat_gateway is None:
                findings.append(
                    Finding(
                        cloud="azure",
                        kind="idle_public_ip",
                        resource_id=ip.id,
                        resource_name=ip.name,
                        location=ip.location,
                        severity=Severity.LOW,
                        detected_at=now,
                        details={
                            "sku": ip.sku.name if ip.sku else None,
                            "allocation": ip.public_ip_allocation_method,
                        },
                        recommendation=(
                            f"Public IP '{ip.name}' has no association. "
                            "Standard-SKU public IPs bill even when unassociated."
                        ),
                    )
                )

    if "nic" in kinds_set:
        for nic in _network().network_interfaces.list_all():
            if nic.virtual_machine is None:
                findings.append(
                    Finding(
                        cloud="azure",
                        kind="orphaned_nic",
                        resource_id=nic.id,
                        resource_name=nic.name,
                        location=nic.location,
                        severity=Severity.LOW,
                        detected_at=now,
                        details={},
                        recommendation=(
                            f"NIC '{nic.name}' is not attached to any VM. "
                            "Usually safe to delete after a quick audit."
                        ),
                    )
                )

    if "snapshot" in kinds_set:
        for snap in _compute().snapshots.list():
            created = snap.time_created
            age_days = (now - created).days if created else None
            if age_days is not None and age_days > 90:
                findings.append(
                    Finding(
                        cloud="azure",
                        kind="old_snapshot",
                        resource_id=snap.id,
                        resource_name=snap.name,
                        location=snap.location,
                        severity=Severity.LOW,
                        detected_at=now,
                        details={
                            "age_days": age_days,
                            "size_gb": snap.disk_size_gb,
                        },
                        recommendation=(
                            f"Snapshot '{snap.name}' is {age_days} days old "
                            f"({snap.disk_size_gb} GB). Review retention."
                        ),
                    )
                )

    return findings
