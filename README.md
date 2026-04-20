# finops-mcp

> The FinOps MCP that **cuts** your cloud bill — cross-cloud, agent-native, with draft-PR remediation.

Most FinOps tools show you a dashboard. `finops-mcp` lets your AI assistant read the bill, find the waste, and draft the fix — all in one loop.

```
You:    "What can I delete in Azure this week to save the most money?"
Claude: Scans subscription. Finds 12 unattached disks, 3 idle public IPs,
        7 orphaned NICs. Est. savings: $340/mo. Drafts Terraform.
```

## Why another FinOps tool?

| Tool                       | What it does                                  | The gap                            |
|----------------------------|-----------------------------------------------|------------------------------------|
| Infracost                  | Pre-deploy cost estimation                    | Doesn't see running spend          |
| OpenCost / Kubecost        | Kubernetes cost breakdown                     | k8s-only, dashboard-first          |
| Vantage / CloudZero        | Multi-cloud cost dashboards                   | Commercial, SaaS-only              |
| Cloud-vendor cost MCPs     | Single-cloud queries                          | Per-cloud, no remediation          |
| **finops-mcp**             | **Cross-cloud, in the agent loop, drafts fixes** | —                                |

The wedge: FinOps that lives inside the loop your agent is already running, not another tab to check.

## Status

| Cloud      | Status   | Tools                                              |
|------------|----------|----------------------------------------------------|
| Azure      | ✅ v0.1  | `get_cost_summary`, `find_idle_resources`          |
| AWS        | 🚧 next  | Cost Explorer + idle EBS / EIP / NAT               |
| GCP        | 🚧 next  | BigQuery billing export + idle compute             |
| Kubernetes | 🚧 next  | OpenCost-backed cost allocation + rightsizing      |

## Install

```bash
# from source (v0.1)
git clone https://github.com/YOUR_ORG/finops-mcp
cd finops-mcp
uv pip install -e .      # or: pip install -e .
```

## Azure setup

`finops-mcp` uses `DefaultAzureCredential`, so any of these just works:

- `az login` (easiest for local use)
- Service principal via `AZURE_CLIENT_ID` / `AZURE_CLIENT_SECRET` / `AZURE_TENANT_ID`
- Managed identity (Azure-hosted agents)

Required roles on the target subscription:
- **Cost Management Reader** (for cost queries)
- **Reader** (for idle-resource enumeration)

## Use with Claude Desktop / Claude Code

Add to your MCP config:

```json
{
  "mcpServers": {
    "finops": {
      "command": "finops-mcp",
      "env": {
        "AZURE_SUBSCRIPTION_ID": "<your-sub-id>"
      }
    }
  }
}
```

Then ask your agent:
- *"What's my Azure spend grouped by service for the last 14 days?"*
- *"Find idle resources in my subscription."*
- *"Which resource groups cost the most?"*

## Tools (v0.1)

### `get_cost_summary`
Actual spend over the last N days, grouped by service / resource group / location / subscription.

### `find_idle_resources`
Scans for unattached managed disks, unassociated public IPs, orphaned NICs, and snapshots older than 90 days. Returns findings with human-readable recommendations.

## Roadmap

- **v0.2** — AWS provider (Cost Explorer, Compute Optimizer, idle EBS/EIP/NAT)
- **v0.3** — GCP provider (BigQuery billing export, Recommender idle VM findings)
- **v0.4** — Kubernetes via OpenCost (namespace cost, pod rightsizing, zombie workloads)
- **v0.5** — Write tools (gated by `FINOPS_MCP_ALLOW_WRITES=1`):
  - `draft_terraform_remediation` — HCL to delete/downsize a finding
  - `draft_github_issue` — PR/issue with savings estimate
- **v0.6** — Anomaly detection, commitment coverage, cross-cloud rollups

## Design

Cross-cloud is the point. Every tool accepts a `cloud` parameter and dispatches to a `Provider` implementation:

```
finops_mcp/
├── server.py            # MCP entrypoint, tool surface
├── models.py            # CostSummary, Finding — cloud-agnostic
└── providers/
    ├── base.py          # Provider protocol
    ├── azure/           # ✅ v0.1
    ├── aws/             # 🚧 v0.2
    └── gcp/             # 🚧 v0.3
```

Adding a cloud is additive, never a rewrite.

## License

MIT
