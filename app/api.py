"""REST API blueprint for scan operations and lifecycle catalog refresh."""

import json
import logging
import os
import threading
import uuid
from datetime import datetime
from typing import Dict, Optional

from flask import Blueprint, current_app, jsonify, request

from app.lifecycle import LifecycleCatalog, default_overlay_path
from app.scanner import OperatorLifecycleScanner

api_bp = Blueprint("api", __name__)
logger = logging.getLogger(__name__)

_scans: Dict[str, dict] = {}
_scan_lock = threading.Lock()
_catalog_lock = threading.Lock()
_catalog: Optional[LifecycleCatalog] = None

_SCAN_STATE_SUBDIR = "scan_state"


def _scan_state_dir(report_dir: str) -> str:
    d = os.path.join(os.path.dirname(report_dir), _SCAN_STATE_SUBDIR)
    os.makedirs(d, exist_ok=True)
    return d


def _persist_scan_state(report_dir: str, scan_id: str, state: dict) -> None:
    path = os.path.join(_scan_state_dir(report_dir), f"{scan_id}.json")
    try:
        with open(path, "w") as f:
            json.dump(state, f, default=str)
    except IOError:
        pass


def _load_scan_state(report_dir: str, scan_id: str):
    path = os.path.join(_scan_state_dir(report_dir), f"{scan_id}.json")
    if not os.path.isfile(path):
        return None
    try:
        with open(path) as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError):
        return None


def get_catalog() -> LifecycleCatalog:
    """Return the process-wide lifecycle catalog (lazy-init)."""
    global _catalog
    if _catalog is None:
        init_catalog(current_app)
    return _catalog


def init_catalog(app) -> LifecycleCatalog:
    """Initialize catalog from app config."""
    global _catalog
    report_dir = app.config["REPORT_DIR"]
    overlay = app.config.get("LIFECYCLE_CATALOG_OVERLAY") or default_overlay_path(report_dir)
    with _catalog_lock:
        _catalog = LifecycleCatalog(
            bundled_path=app.config["LIFECYCLE_CATALOG_BUNDLED"],
            overlay_path=overlay,
            api_base=app.config["LIFECYCLE_API_BASE"],
            api_timeout=app.config["LIFECYCLE_API_TIMEOUT"],
            refresh_enabled=app.config["LIFECYCLE_REFRESH_ENABLED"],
        )
    return _catalog


@api_bp.route("/scan", methods=["POST"])
def start_scan():
    """Start a new operator lifecycle scan."""
    with _scan_lock:
        running = [s for s in _scans.values() if s["status"] == "running"]
        if running:
            return jsonify({"error": "A scan is already running.", "scan_id": running[0]["id"]}), 409

    body = request.get_json(silent=True) or {}
    namespaces = body.get("namespaces")
    exclude_namespaces = body.get("exclude_namespaces")
    namespace_patterns = body.get("namespace_patterns")
    exclude_patterns = body.get("exclude_patterns")

    scan_id = datetime.now().strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:6]
    scan_state = {
        "id": scan_id,
        "status": "running",
        "stage": "initializing",
        "progress": 0,
        "total": 0,
        "detail": "",
        "started_at": datetime.now().isoformat(),
        "finished_at": None,
        "error": None,
    }
    with _scan_lock:
        _scans[scan_id] = scan_state

    _persist_scan_state(current_app.config["REPORT_DIR"], scan_id, scan_state)

    app = current_app._get_current_object()
    t = threading.Thread(
        target=_run_scan,
        args=(app, scan_id, namespaces, exclude_namespaces, namespace_patterns, exclude_patterns),
        daemon=True,
    )
    t.start()
    return jsonify({"scan_id": scan_id, "status": "running"}), 202


@api_bp.route("/scan/<scan_id>", methods=["GET"])
def scan_status(scan_id: str):
    with _scan_lock:
        state = _scans.get(scan_id)
    if state:
        return jsonify(state)
    state = _load_scan_state(current_app.config["REPORT_DIR"], scan_id)
    if state:
        return jsonify(state)
    return jsonify({"error": "Scan not found."}), 404


@api_bp.route("/reports", methods=["GET"])
def list_reports():
    report_dir = current_app.config["REPORT_DIR"]
    reports = []
    if not os.path.isdir(report_dir):
        return jsonify(reports)
    for filename in sorted(os.listdir(report_dir), reverse=True):
        if not filename.endswith(".json"):
            continue
        filepath = os.path.join(report_dir, filename)
        try:
            with open(filepath) as f:
                data = json.load(f)
            reports.append({
                "id": filename.replace(".json", ""),
                "generated_at": data.get("generated_at", ""),
                "total_operators": data.get("total_operators", 0),
                "by_phase": data.get("by_phase", {}),
            })
        except (json.JSONDecodeError, IOError):
            continue
    return jsonify(reports)


@api_bp.route("/reports/<report_id>", methods=["GET"])
def get_report(report_id: str):
    report_dir = current_app.config["REPORT_DIR"]
    filepath = os.path.join(report_dir, f"{report_id}.json")
    if not os.path.isfile(filepath):
        return jsonify({"error": "Report not found."}), 404
    with open(filepath) as f:
        data = json.load(f)
    return jsonify(data)


@api_bp.route("/reports/<report_id>", methods=["DELETE"])
def delete_report(report_id: str):
    report_dir = current_app.config["REPORT_DIR"]
    filepath = os.path.join(report_dir, f"{report_id}.json")
    if not os.path.isfile(filepath):
        return jsonify({"error": "Report not found."}), 404
    os.remove(filepath)
    return jsonify({"deleted": report_id})


@api_bp.route("/lifecycle-catalog", methods=["GET"])
def lifecycle_catalog_info():
    catalog = get_catalog()
    return jsonify(catalog.metadata)


@api_bp.route("/lifecycle-catalog/refresh", methods=["POST"])
def lifecycle_catalog_refresh():
    if not current_app.config["LIFECYCLE_REFRESH_ENABLED"]:
        return jsonify({"error": "Lifecycle catalog refresh is disabled."}), 403
    catalog = get_catalog()
    try:
        with _catalog_lock:
            result = catalog.refresh()
        return jsonify({"status": "ok", **result})
    except Exception as e:
        logger.exception("Catalog refresh failed")
        return jsonify({"error": str(e)}), 502


def _run_scan(app, scan_id, namespaces, exclude_namespaces, namespace_patterns, exclude_patterns):
    with app.app_context():
        state = _scans[scan_id]
        report_dir = app.config["REPORT_DIR"]

        def progress_cb(stage, current, total, detail):
            state["stage"] = stage
            state["progress"] = current
            state["total"] = total
            state["detail"] = detail
            _persist_scan_state(report_dir, scan_id, state)

        try:
            catalog = get_catalog()
            scanner = OperatorLifecycleScanner(
                catalog=catalog,
                namespaces=namespaces,
                exclude_namespaces=exclude_namespaces,
                namespace_patterns=namespace_patterns,
                exclude_patterns=exclude_patterns,
                progress_callback=progress_cb,
            )

            state["stage"] = "connecting"
            _persist_scan_state(report_dir, scan_id, state)
            scanner.connect()

            state["stage"] = "collecting"
            _persist_scan_state(report_dir, scan_id, state)
            scanner.collect_operators()

            state["stage"] = "analyzing"
            _persist_scan_state(report_dir, scan_id, state)
            scanner.analyze()

            state["stage"] = "saving"
            _persist_scan_state(report_dir, scan_id, state)
            report = scanner.get_full_report()

            filepath = os.path.join(report_dir, f"{scan_id}.json")
            with open(filepath, "w") as f:
                json.dump(report, f, indent=2, default=str)

            state["status"] = "completed"
            state["stage"] = "done"
            state["finished_at"] = datetime.now().isoformat()
            _persist_scan_state(report_dir, scan_id, state)
            logger.info(
                "Scan %s completed: %s operators analyzed.",
                scan_id, report["total_operators"],
            )
        except Exception as e:
            logger.exception("Scan %s failed: %s", scan_id, e)
            state["status"] = "failed"
            state["error"] = str(e)
            state["finished_at"] = datetime.now().isoformat()
            _persist_scan_state(report_dir, scan_id, state)
