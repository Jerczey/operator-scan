"""Application configuration."""

import os
import secrets


def get_or_create_secret_key():
    """Get secret key from environment or generate one."""
    secret_key = os.environ.get("SECRET_KEY")
    if secret_key:
        return secret_key
    return secrets.token_hex(32)


class Config:
    """Flask application configuration."""

    SECRET_KEY = get_or_create_secret_key()

    # Report storage
    REPORT_DIR = os.environ.get("REPORT_DIR", "/app/data/reports")

    # Lifecycle catalog
    # Bundled catalog ships with the image; overlay path is writable for refreshes.
    LIFECYCLE_CATALOG_BUNDLED = os.environ.get(
        "LIFECYCLE_CATALOG_BUNDLED",
        os.path.join(os.path.dirname(__file__), "data", "operator_lifecycle_catalog.json"),
    )
    LIFECYCLE_CATALOG_OVERLAY = os.environ.get(
        "LIFECYCLE_CATALOG_OVERLAY",
        "",  # default resolved relative to REPORT_DIR at runtime
    )
    LIFECYCLE_REFRESH_ENABLED = os.environ.get("LIFECYCLE_REFRESH_ENABLED", "true").lower() == "true"
    LIFECYCLE_API_BASE = os.environ.get(
        "LIFECYCLE_API_BASE",
        "https://access.redhat.com/product-life-cycles/api/v2",
    )
    LIFECYCLE_API_TIMEOUT = int(os.environ.get("LIFECYCLE_API_TIMEOUT", "30"))
