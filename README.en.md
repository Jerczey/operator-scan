# Operator Lifecycle Scanner - Documentation

Web application that scans OpenShift clusters for installed OLM operators and reports Red Hat support lifecycle phases (Full Support, Maintenance Support, Out of maintenance).

## Context

Each Red Hat operator follows a lifecycle classification (Platform Aligned, Platform Agnostic, Rolling Stream, or Product Aligned). Cluster administrators need a clear view of which installed operator versions are still in Full Support or Maintenance Support.

This tool lists Subscriptions / ClusterServiceVersions and matches them against a bundled catalog derived from Red Hat Operator Life Cycles, with an optional live refresh via the Product Life Cycle Data API.

## How it works

1. Connect via in-cluster config or kubeconfig.
2. List `subscriptions` and `clusterserviceversions` (`operators.coreos.com`).
3. Match package + version to the lifecycle catalog.
4. Compute phase from catalog end dates.
5. Persist JSON/CSV reports.

## Deploy

See [README.md](README.md) for OpenShift build and deploy steps.

## API

| Method | Path | Description |
|---|---|---|
| POST | `/api/scan` | Start background scan |
| GET | `/api/scan/<id>` | Scan progress |
| GET | `/api/reports` | List reports |
| GET | `/api/reports/<id>` | Get report JSON |
| DELETE | `/api/reports/<id>` | Delete report |
| GET | `/api/lifecycle-catalog` | Catalog metadata |
| POST | `/api/lifecycle-catalog/refresh` | Refresh catalog from Red Hat API |

## Environment variables

| Variable | Default | Description |
|---|---|---|
| `REPORT_DIR` | `/app/data/reports` | Report storage directory |
| `LIFECYCLE_REFRESH_ENABLED` | `true` | Allow catalog refresh |
| `LIFECYCLE_API_BASE` | `https://access.redhat.com/product-life-cycles/api/v2` | Lifecycle API base |
| `LIFECYCLE_CATALOG_BUNDLED` | `app/data/operator_lifecycle_catalog.json` | Bundled catalog path |
| `LIFECYCLE_CATALOG_OVERLAY` | `<parent of REPORT_DIR>/lifecycle_catalog.json` | Writable refreshed catalog |
