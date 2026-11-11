"""Operator lifecycle catalog loader, matcher, and optional API refresh."""

from __future__ import annotations

import json
import logging
import os
import re
from copy import deepcopy
from datetime import date, datetime, timezone
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

PHASE_FULL = "Full Support"
PHASE_MAINTENANCE = "Maintenance Support"
PHASE_OUT = "Out of maintenance"
PHASE_UNKNOWN = "Unknown"

PHASE_ORDER = {
    PHASE_OUT: 0,
    PHASE_MAINTENANCE: 1,
    PHASE_FULL: 2,
    PHASE_UNKNOWN: 3,
}


def _parse_iso_date(value: Optional[str]) -> Optional[date]:
    if not value or value in ("N/A", "n/a", "None"):
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _normalize_version(version: str) -> str:
    """Normalize operator version to major.minor (drop patch / build metadata)."""
    if not version:
        return ""
    version = version.strip().lstrip("vV")
    # CSV names often look like package.v1.2.3
    m = re.search(r"(\d+\.\d+)(?:\.\d+)?", version)
    if m:
        return m.group(1)
    return version


def _channel_mapped_version(entry: dict, channel: str) -> str:
    """Resolve an OLM channel via explicit channel_mappings on the catalog entry."""
    if not channel:
        return ""
    mappings = entry.get("channel_mappings") or {}
    if channel in mappings:
        return str(mappings[channel] or "")
    ch_low = channel.lower()
    for key, value in mappings.items():
        if str(key).lower() == ch_low:
            return str(value or "")
    return ""


def _channel_pattern_version(entry: dict, channel: str) -> str:
    """Extract a product version from channel using entry channel_patterns."""
    if not channel:
        return ""
    for rule in entry.get("channel_patterns") or []:
        pattern = rule.get("pattern") or ""
        if not pattern:
            continue
        try:
            match = re.search(pattern, channel, re.IGNORECASE)
        except re.error:
            logger.warning("Invalid channel_patterns regex on %s: %s", entry.get("display_name"), pattern)
            continue
        if not match:
            continue
        group = rule.get("group", 1)
        try:
            return str(match.group(group) or "")
        except IndexError:
            continue
    return ""


def _version_keys_for_match(entry: dict, version: str = "", channel: str = "") -> List[str]:
    """Prefer channel→product mapping over raw operator/CSV versions."""
    keys: List[str] = []
    mapped = _normalize_version(_channel_mapped_version(entry, channel))
    if mapped:
        keys.append(mapped)
    patterned = _normalize_version(_channel_pattern_version(entry, channel))
    if patterned:
        keys.append(patterned)
    # Generic fallback: first X.Y token in the channel name
    if channel and not mapped and not patterned:
        generic = _normalize_version(channel)
        if generic:
            keys.append(generic)
    op_ver = _normalize_version(version)
    if op_ver:
        keys.append(op_ver)

    ordered: List[str] = []
    seen = set()
    for key in keys:
        if key and key not in seen:
            seen.add(key)
            ordered.append(key)
    return ordered


def _find_version_row(
    versions: list,
    ver_key: str,
    *,
    allow_major_fallback: bool = True,
) -> Optional[dict]:
    """Find a catalog version row by exact major.minor, optionally unique major."""
    if not ver_key:
        return None
    for row in versions or []:
        if _normalize_version(str(row.get("version", ""))) == ver_key:
            return row
    if not allow_major_fallback or "." not in ver_key:
        return None
    major = ver_key.split(".")[0]
    candidates = [
        r for r in (versions or [])
        if str(r.get("version", "")).startswith(major + ".")
        or str(r.get("version", "")) == major
    ]
    if len(candidates) == 1:
        return candidates[0]
    return None


def compute_lifecycle_phase(
    full_support_ends: Optional[str],
    maintenance_ends: Optional[str],
    today: Optional[date] = None,
) -> str:
    """Derive support phase from catalog end dates."""
    today = today or date.today()
    full_end = _parse_iso_date(full_support_ends)
    maint_end = _parse_iso_date(maintenance_ends)

    if not full_end and not maint_end:
        return PHASE_UNKNOWN

    if full_end and today <= full_end:
        return PHASE_FULL

    if maint_end:
        if today <= maint_end:
            return PHASE_MAINTENANCE
        return PHASE_OUT

    # Full support ended but no maintenance date known
    if full_end and today > full_end:
        return PHASE_OUT

    return PHASE_UNKNOWN


def _is_extended_api_type(api_type: Optional[str]) -> bool:
    if not api_type:
        return False
    low = api_type.lower()
    return (
        "extended support" in low
        or "extended life" in low
        or low in ("els", "eus")
    )


def extended_support_remark(
    phase: str,
    *,
    els_ends: Optional[str] = None,
    eus_ends: Optional[str] = None,
    api_type: Optional[str] = None,
    today: Optional[date] = None,
) -> Tuple[bool, str, str]:
    """Build an Important remark when out of maintenance but ELS/EUS remains.

    Returns (eligible, support_kind, note). ``support_kind`` is ``ELS``,
    ``EUS``, or ``ELS/EUS``. Notes are empty when not applicable.

    Also honors catalog ``api_type`` values such as ``Extended Support``
    (e.g. Red Hat Single Sign-On 7.6) when end dates are present or when
    PLCC marks the stream as extended support.
    """
    today = today or date.today()
    if phase != PHASE_OUT:
        return False, "", ""

    parts: List[str] = []
    kinds: List[str] = []
    els = _parse_iso_date(els_ends)
    eus = _parse_iso_date(eus_ends)

    if els and today <= els:
        parts.append(
            f"Extended Lifecycle Support (ELS) may still be available until {els.isoformat()}"
        )
        kinds.append("ELS")
    if eus and today <= eus:
        parts.append(
            f"Extended Update Support (EUS) may still be available until {eus.isoformat()}"
        )
        kinds.append("EUS")

    # Fallback: PLCC marks the version as Extended Support but ELS end date
    # was missing from the catalog row (common for older bundled snapshots).
    if not parts and _is_extended_api_type(api_type):
        parts.append(
            "Product is listed as Extended Support (ELS) in the Red Hat lifecycle catalog"
        )
        kinds.append("ELS")

    if not parts:
        return False, "", ""

    note = (
        ". ".join(parts)
        + ". This usually requires a separate subscription; confirm eligibility "
        "against the product lifecycle policy."
    )
    return True, "/".join(kinds), note


def default_overlay_path(report_dir: str) -> str:
    return os.path.join(os.path.dirname(report_dir), "lifecycle_catalog.json")


class LifecycleCatalog:
    """In-memory lifecycle catalog with optional disk overlay and API refresh."""

    def __init__(
        self,
        bundled_path: str,
        overlay_path: str = "",
        api_base: str = "https://access.redhat.com/product-life-cycles/api/v2",
        api_timeout: int = 30,
        refresh_enabled: bool = True,
    ):
        self.bundled_path = bundled_path
        self.overlay_path = overlay_path
        self.api_base = api_base.rstrip("/")
        self.api_timeout = api_timeout
        self.refresh_enabled = refresh_enabled
        self._data: dict = {}
        self.reload()

    def reload(self) -> None:
        path = self.overlay_path if self.overlay_path and os.path.isfile(self.overlay_path) else self.bundled_path
        with open(path) as f:
            self._data = json.load(f)
        source = "refreshed" if path == self.overlay_path else "bundled"
        if not self._data.get("source"):
            self._data["source"] = source
        logger.info(
            "Loaded lifecycle catalog from %s (%s operators, source=%s)",
            path,
            len(self._data.get("operators") or []),
            self._data.get("source"),
        )

    @property
    def metadata(self) -> dict:
        return {
            "catalog_version": self._data.get("catalog_version", ""),
            "catalog_updated_at": self._data.get("generated_at", ""),
            "catalog_source": self._data.get("source", "bundled"),
            "policy_url": self._data.get("policy_url", ""),
            "operator_entries": len(self._data.get("operators") or []),
        }

    def _package_index(self) -> Dict[str, dict]:
        index: Dict[str, dict] = {}
        for entry in self._data.get("operators") or []:
            for pkg in entry.get("packages") or []:
                index[pkg.lower()] = entry
            display = (entry.get("display_name") or "").lower()
            if display:
                index.setdefault(display, entry)
            api_name = (entry.get("product_api_name") or "").lower()
            if api_name:
                index.setdefault(api_name, entry)
        return index

    def match(
        self,
        package: str = "",
        display_name: str = "",
        version: str = "",
        channel: str = "",
    ) -> Tuple[str, Optional[dict], Optional[dict]]:
        """Match an installed operator to catalog entry + version row.

        Channel is preferred over raw operator/CSV version when the catalog
        entry defines channel_mappings / channel_patterns (e.g. 3scale
        ``threescale-2.13`` → product stream ``2.16``).

        Returns (lifecycle_phase, catalog_entry_or_None, version_row_or_None).
        """
        index = self._package_index()
        entry = None
        if package:
            entry = index.get(package.lower())
        if not entry and display_name:
            entry = index.get(display_name.lower())
            if not entry:
                # Fuzzy: substring match on display names
                dlow = display_name.lower()
                for op in self._data.get("operators") or []:
                    od = (op.get("display_name") or "").lower()
                    if od and (od in dlow or dlow in od):
                        entry = op
                        break

        if not entry:
            return PHASE_UNKNOWN, None, None

        if entry.get("tier") == "Product Aligned" and not entry.get("versions"):
            return PHASE_UNKNOWN, entry, None

        versions = entry.get("versions") or []
        version_row = None
        ver_keys = _version_keys_for_match(entry, version=version, channel=channel)
        allow_major = bool(entry.get("product_stream_fallback", True))
        for ver_key in ver_keys:
            version_row = _find_version_row(
                versions, ver_key, allow_major_fallback=allow_major
            )
            if version_row:
                break

        if not version_row:
            return PHASE_UNKNOWN, entry, None

        phase = compute_lifecycle_phase(
            version_row.get("full_support_ends"),
            version_row.get("maintenance_ends"),
        )
        # Prefer live API type when dates missing but type present
        if phase == PHASE_UNKNOWN and version_row.get("api_type"):
            api_type = version_row["api_type"]
            if api_type in (PHASE_FULL, "Full Support"):
                phase = PHASE_FULL
            elif api_type in (PHASE_MAINTENANCE, "Maintenance Support"):
                phase = PHASE_MAINTENANCE
            elif api_type.lower() in ("end of life", "eol", "retired"):
                phase = PHASE_OUT
            elif _is_extended_api_type(api_type):
                # e.g. RH-SSO 7.6 "Extended Support" — past maintenance, ELS window
                phase = PHASE_OUT
        return phase, entry, version_row

    def refresh(self) -> dict:
        """Refresh catalog entries from Red Hat Product Life Cycle API.

        Merges version dates into a writable overlay. On failure, keeps
        the current catalog and raises RuntimeError.
        """
        if not self.refresh_enabled:
            raise RuntimeError("Lifecycle catalog refresh is disabled.")

        try:
            import requests
        except ImportError as e:
            raise RuntimeError("Package 'requests' is required for catalog refresh.") from e

        base = deepcopy(self._data)
        updated = 0
        failed: List[str] = []
        headers = {"User-Agent": "operator-scan/1.0 (OpenShift lifecycle checker)"}

        for entry in base.get("operators") or []:
            product_name = entry.get("product_api_name")
            if not product_name:
                continue
            # Skip product-aligned with no expectation of operator table dates
            # unless they have a product_api_name we can query.
            url = f"{self.api_base}/products"
            try:
                resp = requests.get(
                    url,
                    params={"name": product_name},
                    headers=headers,
                    timeout=self.api_timeout,
                )
                if resp.status_code != 200:
                    # Try v1 fallback once per failure class
                    v1_base = self.api_base.replace("/api/v2", "/api/v1")
                    resp = requests.get(
                        v1_base + "/products",
                        params={"name": product_name},
                        headers=headers,
                        timeout=self.api_timeout,
                    )
                resp.raise_for_status()
                payload = resp.json()
                products = payload.get("data") or []
                if not products:
                    failed.append(product_name)
                    continue
                prod = products[0]
                entry["product_api_name"] = prod.get("name") or product_name
                if prod.get("package") and not entry.get("packages"):
                    entry["packages"] = [prod["package"]]
                if prod.get("link"):
                    # Prefer absolute customer-portal URLs when API returns a path.
                    link = prod["link"]
                    if link.startswith("/"):
                        link = "https://access.redhat.com" + link
                    entry["lifecycle_url"] = link
                policies = (prod.get("policies") or "").strip()
                if policies:
                    policy_path = policies.split()[0]
                    if policy_path.startswith("/"):
                        entry["policies_url"] = "https://access.redhat.com" + policy_path
                    elif policy_path.startswith("http"):
                        entry["policies_url"] = policy_path
                versions = []
                for v in prod.get("versions") or []:
                    phases = v.get("phases") or []
                    versions.append({
                        "version": v.get("name"),
                        "api_type": v.get("type"),
                        "tier": v.get("tier") or entry.get("tier"),
                        "ga": _phase_date(phases, ["General availability"], prefer="start"),
                        "full_support_ends": _phase_date(phases, ["Full support"], prefer="end"),
                        "maintenance_ends": _phase_date(
                            phases, ["Maintenance support", "Maintenance Support"], prefer="end"
                        ),
                        "eus_ends": _phase_date(
                            phases, ["Extended update support", "Extended Update Support"], prefer="end"
                        ),
                        "els_ends": _phase_date(
                            phases,
                            [
                                "Extended life cycle support (ELS) 1",
                                "Extended life cycle support",
                                "Extended Lifecycle Support",
                                "ELS",
                            ],
                            prefer="end",
                        ),
                        "openshift_compatibility": v.get("openshift_compatibility") or [],
                        "additional_text": v.get("additional_text") or "",
                    })
                if versions:
                    entry["versions"] = versions
                    updated += 1
            except Exception as e:
                logger.warning("Failed to refresh %s: %s", product_name, e)
                failed.append(product_name)

        if updated == 0 and failed:
            raise RuntimeError(
                f"Catalog refresh failed for all products. Examples: {', '.join(failed[:5])}"
            )

        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        base["generated_at"] = now
        base["source"] = "refreshed"
        base["last_refresh_at"] = now
        base["last_refresh_updated"] = updated
        base["last_refresh_failed"] = failed

        overlay = self.overlay_path
        if not overlay:
            raise RuntimeError("No overlay path configured for catalog refresh.")
        os.makedirs(os.path.dirname(overlay), exist_ok=True)
        with open(overlay, "w") as f:
            json.dump(base, f, indent=2)

        self._data = base
        logger.info("Catalog refreshed: %s products updated, %s failed", updated, len(failed))
        return {
            "updated": updated,
            "failed": failed,
            "generated_at": now,
            "source": "refreshed",
            "overlay_path": overlay,
        }


def _phase_date(phases: list, names: List[str], prefer: str = "end") -> Optional[str]:
    lowers = [n.lower() for n in names]
    for ph in phases or []:
        if ph.get("name", "").lower() in lowers:
            raw = ph.get("end_date") if prefer == "end" else ph.get("start_date")
            if not raw or raw in ("N/A", "n/a"):
                raw = ph.get("end_date") or ph.get("start_date")
            if raw and raw not in ("N/A", "n/a"):
                return str(raw)[:10]
    return None
