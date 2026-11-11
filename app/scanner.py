"""
Operator Lifecycle Scanner
==========================
Scans an OpenShift cluster for installed OLM operators (Subscriptions /
ClusterServiceVersions) and reports their Red Hat support lifecycle phase.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Dict, List, Optional

try:
    from kubernetes import client, config
    from kubernetes.client.rest import ApiException
except ImportError:
    raise ImportError("Package 'kubernetes' not found. Install with: pip install kubernetes")

from app.lifecycle import (
    PHASE_FULL,
    PHASE_MAINTENANCE,
    PHASE_ORDER,
    PHASE_OUT,
    PHASE_UNKNOWN,
    LifecycleCatalog,
    _normalize_version,
)

logger = logging.getLogger(__name__)

OLM_GROUP = "operators.coreos.com"
OLM_VERSION = "v1alpha1"


@dataclass
class OperatorReport:
    """Report for a single installed OLM operator."""

    display_name: str
    package: str
    version: str
    channel: str
    namespace: str
    csv_name: str
    csv_phase: str
    catalog_source: str
    provider: str
    lifecycle_phase: str = PHASE_UNKNOWN
    tier: str = ""
    full_support_ends: str = ""
    maintenance_ends: str = ""
    eus_ends: str = ""
    lifecycle_url: str = ""
    matched_product: str = ""

    @property
    def phase_sort_key(self) -> int:
        return PHASE_ORDER.get(self.lifecycle_phase, 99)

    def to_dict(self) -> dict:
        return {
            "display_name": self.display_name,
            "package": self.package,
            "version": self.version,
            "channel": self.channel,
            "namespace": self.namespace,
            "csv_name": self.csv_name,
            "csv_phase": self.csv_phase,
            "catalog_source": self.catalog_source,
            "provider": self.provider,
            "lifecycle_phase": self.lifecycle_phase,
            "tier": self.tier,
            "full_support_ends": self.full_support_ends,
            "maintenance_ends": self.maintenance_ends,
            "eus_ends": self.eus_ends,
            "lifecycle_url": self.lifecycle_url,
            "matched_product": self.matched_product,
        }


class OperatorLifecycleScanner:
    """Scans installed OLM operators and maps them to lifecycle phases."""

    _K8S_PAGE_SIZE = 500

    def __init__(
        self,
        catalog: LifecycleCatalog,
        namespaces: Optional[List[str]] = None,
        exclude_namespaces: Optional[List[str]] = None,
        namespace_patterns: Optional[List[str]] = None,
        exclude_patterns: Optional[List[str]] = None,
        progress_callback=None,
    ):
        self.catalog = catalog
        self.target_namespaces = namespaces
        self.exclude_namespaces = set(exclude_namespaces or [])
        self._ns_include_patterns = self._compile_patterns(namespace_patterns)
        self._ns_exclude_patterns = self._compile_patterns(exclude_patterns)
        self.progress_callback = progress_callback

        self.operators: List[OperatorReport] = []
        self.cluster_info: Dict[str, str] = {}
        self.custom: Optional[client.CustomObjectsApi] = None
        self.v1: Optional[client.CoreV1Api] = None

    @staticmethod
    def _compile_patterns(patterns: Optional[List[str]]) -> List[re.Pattern]:
        compiled = []
        for p in (patterns or []):
            p = p.strip()
            if not p:
                continue
            try:
                compiled.append(re.compile(p))
            except re.error as e:
                logger.warning("Invalid regex pattern '%s': %s", p, e)
        return compiled

    def _namespace_included(self, ns: str) -> bool:
        if not self.target_namespaces and not self._ns_include_patterns:
            return True
        if self.target_namespaces and ns in self.target_namespaces:
            return True
        if self._ns_include_patterns and any(p.search(ns) for p in self._ns_include_patterns):
            return True
        return False

    def _namespace_excluded(self, ns: str) -> bool:
        if ns in self.exclude_namespaces:
            return True
        return any(p.search(ns) for p in self._ns_exclude_patterns)

    def _accept_namespace(self, ns: str) -> bool:
        if self._namespace_excluded(ns):
            return False
        return self._namespace_included(ns)

    def _report_progress(self, stage: str, current: int, total: int, detail: str = ""):
        if self.progress_callback:
            self.progress_callback(stage, current, total, detail)

    def connect(self):
        """Connect to the OpenShift / Kubernetes cluster."""
        try:
            config.load_incluster_config()
            logger.info("Using in-cluster configuration (ServiceAccount).")
        except config.ConfigException:
            config.load_kube_config()
            logger.info("Using local kubeconfig.")

        self.v1 = client.CoreV1Api()
        self.custom = client.CustomObjectsApi()

        version_api = client.VersionApi()
        version = version_api.get_code()
        self.cluster_info["kubernetes_version"] = version.git_version
        logger.info("Connected: Kubernetes %s", version.git_version)

        self._check_openshift()

    def _check_openshift(self):
        try:
            cv = self.custom.get_cluster_custom_object(
                "config.openshift.io", "v1", "clusterversions", "version",
            )
            ver = cv.get("status", {}).get("desired", {}).get("version", "unknown")
            self.cluster_info["openshift_version"] = ver
            logger.info("OpenShift %s detected.", ver)
        except ApiException:
            self.cluster_info["openshift_version"] = "N/A (not OpenShift?)"

    def _list_namespaced_custom(self, plural: str) -> List[dict]:
        """List a namespaced OLM CR across all namespaces with pagination."""
        items: List[dict] = []
        continue_token = None
        while True:
            kwargs = {"limit": self._K8S_PAGE_SIZE}
            if continue_token:
                kwargs["_continue"] = continue_token
            try:
                result = self.custom.list_cluster_custom_object(
                    OLM_GROUP, OLM_VERSION, plural, **kwargs,
                )
            except ApiException as e:
                raise RuntimeError(f"Failed to list {plural}: {e}") from e
            items.extend(result.get("items") or [])
            continue_token = result.get("metadata", {}).get("continue")
            if not continue_token:
                break
        return items

    def collect_operators(self):
        """Collect installed operators from Subscriptions and CSVs."""
        self._report_progress("collecting", 0, 2, "Listing Subscriptions...")
        subscriptions = self._list_namespaced_custom("subscriptions")
        self._report_progress("collecting", 1, 2, "Listing ClusterServiceVersions...")
        csvs = self._list_namespaced_custom("clusterserviceversions")

        csv_by_key: Dict[str, dict] = {}
        for csv in csvs:
            meta = csv.get("metadata") or {}
            ns = meta.get("namespace", "")
            name = meta.get("name", "")
            if ns and name:
                csv_by_key[f"{ns}/{name}"] = csv

        operators: List[OperatorReport] = []
        seen = set()

        for sub in subscriptions:
            meta = sub.get("metadata") or {}
            ns = meta.get("namespace", "")
            if not self._accept_namespace(ns):
                continue

            spec = sub.get("spec") or {}
            status = sub.get("status") or {}
            package = spec.get("name") or ""
            channel = spec.get("channel") or ""
            source = spec.get("source") or ""
            installed_csv = status.get("installedCSV") or ""
            current_csv = status.get("currentCSV") or installed_csv

            csv = csv_by_key.get(f"{ns}/{installed_csv}") or csv_by_key.get(f"{ns}/{current_csv}")
            display_name, version, csv_phase, provider = self._csv_fields(csv, package, installed_csv or current_csv)

            key = f"{ns}/{package}/{version}/{installed_csv or current_csv}"
            if key in seen:
                continue
            seen.add(key)

            operators.append(OperatorReport(
                display_name=display_name,
                package=package,
                version=version,
                channel=channel,
                namespace=ns,
                csv_name=installed_csv or current_csv,
                csv_phase=csv_phase,
                catalog_source=source,
                provider=provider,
            ))

        # Inventory is Subscription-driven on purpose.
        # AllNamespaces operators copy their CSV into every namespace; counting
        # orphan CSVs would explode the report with duplicates.

        self.operators = operators
        self._report_progress("collecting", 2, 2, f"Found {len(operators)} installed operator(s)")
        logger.info("Collected %s installed operators", len(operators))

    @staticmethod
    def _csv_fields(csv: Optional[dict], package: str, csv_name: str):
        if not csv:
            version = _normalize_version(csv_name)
            return package or csv_name, version, "", ""

        spec = csv.get("spec") or {}
        status = csv.get("status") or {}
        display_name = spec.get("displayName") or package or csv_name
        version = spec.get("version") or ""
        if not version:
            version = _normalize_version(csv_name)
        else:
            # Keep full version string for report; matching normalizes
            version = str(version)
        csv_phase = status.get("phase") or ""
        provider = ""
        provider_obj = spec.get("provider")
        if isinstance(provider_obj, dict):
            provider = provider_obj.get("name") or ""
        elif isinstance(provider_obj, str):
            provider = provider_obj
        return display_name, version, csv_phase, provider

    def analyze(self):
        """Match collected operators against the lifecycle catalog."""
        total = len(self.operators)
        self._report_progress("analyzing", 0, max(total, 1), "Matching lifecycle catalog...")

        for i, op in enumerate(self.operators):
            phase, entry, version_row = self.catalog.match(
                package=op.package,
                display_name=op.display_name,
                version=op.version,
                channel=op.channel,
            )
            op.lifecycle_phase = phase
            if entry:
                op.matched_product = entry.get("display_name") or entry.get("product_api_name") or ""
                op.tier = (version_row or {}).get("tier") or entry.get("tier") or ""
                op.lifecycle_url = entry.get("lifecycle_url") or ""
            if version_row:
                op.full_support_ends = version_row.get("full_support_ends") or ""
                op.maintenance_ends = version_row.get("maintenance_ends") or ""
                op.eus_ends = version_row.get("eus_ends") or version_row.get("els_ends") or ""
            self._report_progress(
                "analyzing", i + 1, max(total, 1),
                f"{op.display_name} → {op.lifecycle_phase}",
            )

    def get_summary(self) -> dict:
        by_phase = {
            PHASE_FULL: 0,
            PHASE_MAINTENANCE: 0,
            PHASE_OUT: 0,
            PHASE_UNKNOWN: 0,
        }
        for op in self.operators:
            by_phase[op.lifecycle_phase] = by_phase.get(op.lifecycle_phase, 0) + 1

        return {
            "generated_at": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
            "cluster_info": dict(self.cluster_info),
            "total_operators": len(self.operators),
            "by_phase": by_phase,
            **self.catalog.metadata,
        }

    def get_full_report(self) -> dict:
        summary = self.get_summary()
        sorted_ops = sorted(self.operators, key=lambda o: (o.phase_sort_key, o.display_name.lower()))
        summary["operators"] = [o.to_dict() for o in sorted_ops]
        return summary
