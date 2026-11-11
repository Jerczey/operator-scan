"""Web routes blueprint."""

import csv
import io
import json
import os

from flask import (
    Blueprint, Response, current_app, render_template, request, send_from_directory,
)

web_bp = Blueprint("web", __name__)


@web_bp.route("/")
def index():
    """Dashboard with list of past reports."""
    reports = _list_reports()
    catalog_meta = {}
    try:
        from app.api import get_catalog
        catalog_meta = get_catalog().metadata
    except Exception:
        pass
    return render_template("dashboard.html", reports=reports, catalog=catalog_meta)


@web_bp.route("/reports/<report_id>")
def view_report(report_id: str):
    """View a specific report."""
    report_dir = current_app.config["REPORT_DIR"]
    filepath = os.path.join(report_dir, f"{report_id}.json")
    if not os.path.isfile(filepath):
        return render_template("error.html", message="Report not found."), 404
    with open(filepath) as f:
        data = json.load(f)
    return render_template("report.html", report=data, report_id=report_id)


@web_bp.route("/reports/<report_id>/download")
def download_report(report_id: str):
    """Download report JSON."""
    report_dir = current_app.config["REPORT_DIR"]
    filename = f"{report_id}.json"
    if not os.path.isfile(os.path.join(report_dir, filename)):
        return render_template("error.html", message="Report not found."), 404
    return send_from_directory(
        report_dir, filename, as_attachment=True,
        download_name=f"operator-lifecycle-report-{report_id}.json",
    )


@web_bp.route("/reports/<report_id>/csv")
def download_csv(report_id: str):
    """Download report as CSV.

    Query params:
      phase - comma-separated lifecycle phases to include
    """
    report_dir = current_app.config["REPORT_DIR"]
    filepath = os.path.join(report_dir, f"{report_id}.json")
    if not os.path.isfile(filepath):
        return render_template("error.html", message="Report not found."), 404
    with open(filepath) as f:
        data = json.load(f)

    phase_filter = request.args.get("phase", "")
    allowed = {s.strip() for s in phase_filter.split(",") if s.strip()} if phase_filter else None

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "Operator", "Package", "Version", "Channel", "Namespace",
        "Lifecycle Phase", "Tier", "Full Support Ends", "Maintenance Ends",
        "EUS Ends", "ELS Ends", "ELS Eligible", "Extended Support Kind",
        "Lifecycle Note", "CSV Name", "CSV Phase", "Catalog Source", "Provider",
        "Matched Product", "Lifecycle URL",
    ])

    for op in data.get("operators", []):
        if allowed and op.get("lifecycle_phase") not in allowed:
            continue
        writer.writerow([
            op.get("display_name", ""),
            op.get("package", ""),
            op.get("version", ""),
            op.get("channel", ""),
            op.get("namespace", ""),
            op.get("lifecycle_phase", ""),
            op.get("tier", ""),
            op.get("full_support_ends", ""),
            op.get("maintenance_ends", ""),
            op.get("eus_ends", ""),
            op.get("els_ends", ""),
            "yes" if op.get("els_eligible") else "no",
            op.get("extended_support_kind", ""),
            op.get("lifecycle_note", ""),
            op.get("csv_name", ""),
            op.get("csv_phase", ""),
            op.get("catalog_source", ""),
            op.get("provider", ""),
            op.get("matched_product", ""),
            op.get("lifecycle_url", ""),
        ])

    return Response(
        output.getvalue(),
        mimetype="text/csv",
        headers={
            "Content-Disposition": f"attachment; filename=operator-lifecycle-report-{report_id}.csv",
        },
    )


def _list_reports() -> list:
    report_dir = current_app.config["REPORT_DIR"]
    reports = []
    if not os.path.isdir(report_dir):
        return reports
    for filename in os.listdir(report_dir):
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
                "cluster_info": data.get("cluster_info", {}),
            })
        except (json.JSONDecodeError, IOError):
            continue
    reports.sort(key=lambda r: r["generated_at"], reverse=True)
    return reports
