"""
Constants for the Gemini Usage Monitor integration.

This module is the single source of truth for every magic string, URL,
default value, and sensor definition used across the integration.
"""

# ---------------------------------------------------------------------------
# Integration identity
# ---------------------------------------------------------------------------

DOMAIN = "gemini_usage"
"""Unique domain identifier for this integration (must be all lowercase)."""

PLATFORMS = ["sensor"]
"""HA platforms that this integration registers entities on."""

# ---------------------------------------------------------------------------
# Config-entry data / options keys
# ---------------------------------------------------------------------------

CONF_API_KEY = "api_key"
"""Gemini API key — required; used for model-list retrieval and API health."""

CONF_GCP_PROJECT_ID = "gcp_project_id"
"""Google Cloud project ID — required only when GCP Cloud Monitoring is enabled."""

CONF_GCP_SERVICE_ACCOUNT_JSON = "gcp_service_account_json"
"""Full JSON content of a GCP service account key file."""

CONF_LOCAL_DB_PATH = "local_db_path"
"""Path to the local Antigravity brain directory for transcript tracking."""

CONF_RATE_LIMIT_INTERVAL = "rate_limit_interval"
"""Seconds between successive API health & rate-limit checks."""

CONF_GCP_INTERVAL = "gcp_interval"
"""Seconds between successive GCP Cloud Monitoring queries."""

CONF_LOCAL_INTERVAL = "local_interval"
"""Seconds between successive local transcript scans."""

# ---------------------------------------------------------------------------
# Default poll intervals (seconds)
# ---------------------------------------------------------------------------

DEFAULT_RATE_LIMIT_INTERVAL = 60       # Live API health & probe
DEFAULT_GCP_INTERVAL = 300             # Cloud Monitoring (5 min)
DEFAULT_LOCAL_INTERVAL = 120           # Local transcript scan (2 min)
DEFAULT_MODEL_LIST_INTERVAL = 3600     # Model list refresh (1 hour)

# ---------------------------------------------------------------------------
# Gemini REST API endpoints
# ---------------------------------------------------------------------------

GEMINI_API_BASE = "https://generativelanguage.googleapis.com/v1beta"
GEMINI_MODELS_ENDPOINT = f"{GEMINI_API_BASE}/models"

# ---------------------------------------------------------------------------
# GCP Cloud Monitoring API
# ---------------------------------------------------------------------------

GCP_MONITORING_BASE = "https://monitoring.googleapis.com/v3"
GEMINI_GCP_SERVICE = "generativelanguage.googleapis.com"
GCP_MONITORING_SCOPES = ["https://www.googleapis.com/auth/monitoring.read"]

METRIC_QUOTA_RATE_USAGE = "serviceruntime.googleapis.com/quota/rate/net_usage"
METRIC_QUOTA_LIMIT = "serviceruntime.googleapis.com/quota/limit"
METRIC_QUOTA_EXCEEDED = "serviceruntime.googleapis.com/quota/exceeded"

# ---------------------------------------------------------------------------
# Local Antigravity paths
# ---------------------------------------------------------------------------

ANTIGRAVITY_BRAIN_PATH_TEMPLATES = [
    r"%USERPROFILE%\.gemini\antigravity\brain",
    r"%APPDATA%\.gemini\antigravity\brain",
]

# ---------------------------------------------------------------------------
# Model slug helpers
# ---------------------------------------------------------------------------

MODEL_SLUG_REPLACE = {"-": "_", ".": "_", "/": "_", ":": "_"}

# ---------------------------------------------------------------------------
# Per-model sensor definitions
# ---------------------------------------------------------------------------

SENSOR_DEFS: dict[str, dict] = {
    # Core model capacity & limits (always available from /models API)
    "input_token_limit": {
        "name": "Input Token Limit",
        "unit": "tokens",
        "icon": "mdi:arrow-up-bold-box-outline",
        "source": "model_metadata",
    },
    "output_token_limit": {
        "name": "Output Token Limit",
        "unit": "tokens",
        "icon": "mdi:arrow-down-bold-box-outline",
        "source": "model_metadata",
    },
    # Live Rate-limit response headers (created if returned by API)
    "rpm_remaining": {
        "name": "Req/Min Remaining",
        "unit": "requests",
        "icon": "mdi:counter",
        "source": "api_headers",
    },
    "rpm_limit": {
        "name": "Req/Min Limit",
        "unit": "requests",
        "icon": "mdi:speedometer",
        "source": "api_headers",
    },
    "tpm_remaining": {
        "name": "Tokens/Min Remaining",
        "unit": "tokens",
        "icon": "mdi:counter",
        "source": "api_headers",
    },
    "tpm_limit": {
        "name": "Tokens/Min Limit",
        "unit": "tokens",
        "icon": "mdi:speedometer",
        "source": "api_headers",
    },
    "rpd_remaining": {
        "name": "Req/Day Remaining",
        "unit": "requests",
        "icon": "mdi:counter",
        "source": "api_headers",
    },
    "rpd_limit": {
        "name": "Req/Day Limit",
        "unit": "requests",
        "icon": "mdi:speedometer",
        "source": "api_headers",
    },
    # GCP Cloud Monitoring (created only if GCP is configured)
    "tokens_input_1h": {
        "name": "Input Tokens (1h)",
        "unit": "tokens",
        "icon": "mdi:upload",
        "source": "gcp",
    },
    "tokens_output_1h": {
        "name": "Output Tokens (1h)",
        "unit": "tokens",
        "icon": "mdi:download",
        "source": "gcp",
    },
    "quota_used_pct": {
        "name": "Quota Used %",
        "unit": "%",
        "icon": "mdi:percent",
        "source": "gcp",
    },
    # Local Antigravity scan (created only if local path is configured)
    "tokens_today": {
        "name": "Tokens Today (Local)",
        "unit": "tokens",
        "icon": "mdi:calendar-today",
        "source": "local",
    },
    "tokens_this_month": {
        "name": "Tokens This Month (Local)",
        "unit": "tokens",
        "icon": "mdi:calendar-month",
        "source": "local",
    },
}

# ---------------------------------------------------------------------------
# Global / Aggregate sensor definitions
# ---------------------------------------------------------------------------

AGGREGATE_SENSOR_DEFS: dict[str, dict] = {
    "model_count": {
        "name": "Available Model Count",
        "unit": "models",
        "icon": "mdi:robot-outline",
        "source": "api_core",
    },
    "api_status": {
        "name": "API Status",
        "unit": None,
        "icon": "mdi:api",
        "source": "api_core",
    },
    "api_latency": {
        "name": "API Latency",
        "unit": "ms",
        "icon": "mdi:timer-outline",
        "source": "api_core",
    },
    # Local aggregates (only created if local tracking is active)
    "total_tokens_today": {
        "name": "Total Tokens Today",
        "unit": "tokens",
        "icon": "mdi:sigma",
        "source": "local",
    },
    "total_tokens_this_month": {
        "name": "Total Tokens This Month",
        "unit": "tokens",
        "icon": "mdi:sigma",
        "source": "local",
    },
}
