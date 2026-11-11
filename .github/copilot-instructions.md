# Operator Lifecycle Scanner

Flask + PatternFly 6 app that scans installed OLM operators and reports Red Hat lifecycle phases.

- Scanner: `app/scanner.py` (Subscriptions + CSVs)
- Catalog: `app/lifecycle.py` + `app/data/operator_lifecycle_catalog.json`
- Prefer Red Hat / UBI / PatternFly conventions
- Do not add skopeo or registry credential flows — this fork does not inspect images
