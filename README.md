# Operator Lifecycle Scanner for OpenShift

Web application that scans an OpenShift cluster for **installed OLM operators** and reports their Red Hat support lifecycle phase: Full Support, Maintenance Support, or Out of maintenance.

Forked from the cgroups v2 checker UI/runtime pattern, adapted for Operator Lifecycle Manager (Subscriptions / ClusterServiceVersions).

| [English](README.en.md) | [Português](README.pt-br.md) |
|---|---|

## Features

- Lists installed OLM operators (Subscriptions + ClusterServiceVersions)
- Matches package/version against a bundled Red Hat lifecycle catalog
- Optional catalog refresh from the [Product Life Cycle Data API](https://access.redhat.com/product-life-cycles/api/v2/products)
- Clickable phase summary cards and filterable report table
- CSV and JSON report download
- Background scan with progress reporting
- PatternFly 6 dark theme (OpenShift Console style)

## How it works

1. Connect to the cluster (in-cluster ServiceAccount or local kubeconfig).
2. Collect Subscriptions and ClusterServiceVersions.
3. Match each operator package + major.minor version to the lifecycle catalog.
4. Compute the current phase from today’s date vs full-support / maintenance end dates.
5. Save a JSON report under `REPORT_DIR`.

Phases:

| Phase | Meaning |
|---|---|
| Full Support | Before full-support end date |
| Maintenance Support | After full support, before maintenance end |
| Out of maintenance | Past maintenance end (or EOL) |
| Unknown | No catalog match, Product Aligned without dates, or community operator |

## Architecture

```
Browser  -->  Flask (Gunicorn, PatternFly v6 dark theme)
                |
                +--> Kubernetes API (Subscriptions, CSVs, ClusterVersion)
                +--> Bundled / refreshed lifecycle catalog JSON
                +--> JSON reports saved to REPORT_DIR
```

## File structure

```
app/
  app.py             Flask application factory
  config.py          Configuration via environment variables
  scanner.py         OLM operator collector + lifecycle matching
  lifecycle.py       Catalog loader, matcher, API refresh
  routes.py          Web routes (dashboard, reports)
  api.py             REST API (scan, reports, catalog refresh)
  data/              Bundled operator_lifecycle_catalog.json
  templates/         Jinja2 + PatternFly
  static/            CSS / JS
openshift/           Deployment manifests
Containerfile        UBI 9 Python image
```

## Deploy to OpenShift

### 1. Create namespace and RBAC

```bash
oc apply -f openshift/namespace.yaml
oc apply -f openshift/rbac.yaml
```

### 2. Build the image

```bash
oc new-build --binary --strategy=docker --name=operator-scan -n operator-scan
oc start-build operator-scan --from-dir=. --follow -n operator-scan
```

### 3. Deploy

```bash
oc apply -f openshift/deployment.yaml
oc get route operator-scan -n operator-scan
```

## Local development

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
export REPORT_DIR=./data/reports
mkdir -p "$REPORT_DIR"
# Uses local kubeconfig (oc login / CRC)
python run.py
```

Open http://127.0.0.1:8080

## Catalog refresh

Use **Update catalog** on the dashboard (or `POST /api/lifecycle-catalog/refresh`) when the pod can reach `access.redhat.com`. On failure the bundled catalog remains in use.

Disable with `LIFECYCLE_REFRESH_ENABLED=false`.

## Policy references

- [OpenShift Operator Life Cycles](https://access.redhat.com/support/policy/updates/openshift_operators)
- [Product Life Cycle Data API](https://docs.redhat.com/en/documentation/red_hat_product_life_cycle_data_api/1.0)
