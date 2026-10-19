"""
Constants for the Gemini Usage Monitor integration.

This module defines all constants, configuration keys, default polling
intervals, Google API endpoints, and sensor schema definitions for the
Gemini Usage Monitor Home Assistant integration.

Architecture & Dynamic Entity Model:
-----------------------------------
1. **Core / Metadata Sensors**:
   - `model_count`: Total number of models discovered via `GET /v1beta/models`.
   - `api_status`: Live status of the Gemini API endpoint ('Online' / 'Offline').
   - `api_latency`: Round-trip HTTP response time in milliseconds.
   - `input_token_limit`: Maximum input context window per model (e.g. 1M / 2M tokens).
   - `output_token_limit`: Maximum generated token budget per model.

2. **Conditional Source Sensors**:
   - **GCP Cloud Monitoring**: Created only when GCP credentials are configured.
   - **Local Antigravity Logs**: Created only when a valid local path is provided.
   - **Live Rate Limits**: Created if sliding-window rate limit headers are returned.
"""

# ---------------------------------------------------------------------------
# Integration identity
# ---------------------------------------------------------------------------

DOMAIN = "gemini_usage"
"""Unique domain identifier for this integration (must match custom_components directory name)."""

VERSION = "1.1.3"
"""Current integration and daemon release version."""

PLATFORMS = ["sensor", "binary_sensor"]
"""Home Assistant entity platforms supported by this integration."""



# ---------------------------------------------------------------------------
# Config entry data / options keys
# ---------------------------------------------------------------------------

CONF_API_KEY = "api_key"
"""Gemini API key — required; obtained from Google AI Studio."""

CONF_GCP_PROJECT_ID = "gcp_project_id"
"""Google Cloud Project ID — optional; required only for Cloud Monitoring."""

CONF_GCP_SERVICE_ACCOUNT_JSON = "gcp_service_account_json"
"""GCP Service Account JSON key content — optional; requires roles/monitoring.viewer."""

CONF_LOCAL_DB_PATH = "local_db_path"
"""Path to Antigravity brain directory — optional; for local transcript log tracking."""

CONF_LOCAL_INTERVAL = "local_interval"
"""Poll interval in seconds for local transcript logs."""

CONF_RATE_LIMIT_INTERVAL = "rate_limit_interval"
"""Poll interval in seconds for API health and live rate limit checks."""

CONF_GCP_INTERVAL = "gcp_interval"
"""Poll interval in seconds for GCP Cloud Monitoring time-series."""

CONF_DISPLAY_MODE = "display_mode"
"""Display mode for Antigravity quotas: 'used' (Increasing) or 'remaining' (Decreasing)."""

DISPLAY_MODE_USED = "used"
"""Quota Used: 0% -> 100% (fills up like CPU/RAM usage)."""

DISPLAY_MODE_REMAINING = "remaining"
"""Quota Remaining: 100% -> 0% (depletes like battery/fuel)."""

DEFAULT_DISPLAY_MODE = DISPLAY_MODE_USED
"""Default display mode: Quota Used (Increasing)."""

CONF_WEBHOOK_ID = "webhook_id"
"""Webhook identifier for receiving Antigravity quota push updates."""

# ---------------------------------------------------------------------------
# Default poll intervals (in seconds)
# ---------------------------------------------------------------------------

DEFAULT_RATE_LIMIT_INTERVAL = 60
"""Default interval for API health checks and latency measurements."""

DEFAULT_GCP_INTERVAL = 300
"""Default interval for GCP Cloud Monitoring (5 minutes to respect quota limits)."""

DEFAULT_LOCAL_INTERVAL = 120
"""Default interval for scanning local conversation transcripts (2 minutes)."""

DEFAULT_MODEL_LIST_INTERVAL = 3600
"""Default interval for refreshing the full model catalogue (1 hour)."""

# ---------------------------------------------------------------------------
# Gemini REST API endpoints
# ---------------------------------------------------------------------------

GEMINI_API_BASE = "https://generativelanguage.googleapis.com/v1beta"
"""Base REST endpoint for Google Generative Language v1beta API."""

GEMINI_MODELS_ENDPOINT = f"{GEMINI_API_BASE}/models"
"""Endpoint to list all accessible Gemini models with their parameters and context limits."""

# ---------------------------------------------------------------------------
# GCP Cloud Monitoring API
# ---------------------------------------------------------------------------

GCP_MONITORING_BASE = "https://monitoring.googleapis.com/v3"
"""Base endpoint for Google Cloud Monitoring REST API v3."""

GEMINI_GCP_SERVICE = "generativelanguage.googleapis.com"
"""Monitored resource service label for Google Gemini API."""

GCP_MONITORING_SCOPES = ["https://www.googleapis.com/auth/monitoring.read"]
"""OAuth2 scope for read-only access to Cloud Monitoring metrics."""

METRIC_QUOTA_RATE_USAGE = "serviceruntime.googleapis.com/quota/rate/net_usage"
"""Metric type for quota consumption rate."""

METRIC_QUOTA_LIMIT = "serviceruntime.googleapis.com/quota/limit"
"""Metric type for quota allocation ceiling."""

METRIC_QUOTA_EXCEEDED = "serviceruntime.googleapis.com/quota/exceeded"
"""Metric type for quota exhaustion count."""

# ---------------------------------------------------------------------------
# Local Antigravity paths
# ---------------------------------------------------------------------------

ANTIGRAVITY_BRAIN_PATH_TEMPLATES = [
    r"%USERPROFILE%\.gemini\antigravity\brain",
    r"%APPDATA%\.gemini\antigravity\brain",
]
"""Windows environment templates for auto-detecting local Antigravity brain logs."""

# ---------------------------------------------------------------------------
# Model slug helpers
# ---------------------------------------------------------------------------

MODEL_SLUG_REPLACE = {"-": "_", ".": "_", "/": "_", ":": "_"}
"""Character mapping to sanitize model names into Home Assistant-safe entity slugs."""

# ---------------------------------------------------------------------------
# Per-model sensor definitions
# ---------------------------------------------------------------------------

SENSOR_DEFS: dict[str, dict] = {
    # Core model capacity & limits (always populated from GET /v1beta/models)
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
    # Live Rate-limit response headers (registered if returned by API)
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
    # GCP Cloud Monitoring (registered only when GCP credentials are configured)
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
    # Local Antigravity transcript scan (registered only when local path exists)
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
    # Local aggregates (only created when local tracking is active)
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

WEBHOOK_ID_ANTIGRAVITY = "gemini_usage_antigravity_quota"
"""Default webhook ID for receiving Antigravity quota push updates."""

# ---------------------------------------------------------------------------
# Antigravity Compute Quota sensor definitions
# ---------------------------------------------------------------------------

ANTIGRAVITY_SENSOR_DEFS: dict[str, dict] = {
    # 1. Primary Configurable Entities (dynamic: flips based on Options Flow display_mode)
    "antigravity_gemini_weekly": {
        "name_used": "Gemini Models — Weekly Quota Used",
        "name_remaining": "Gemini Models — Weekly Limit Remaining",
        "unit": "%",
        "state_class": "measurement",
        "icon": "mdi:google",
        "bucket_id": "gemini-weekly",
        "group": "Gemini Models",
        "type": "dynamic",
    },
    "antigravity_gemini_5h": {
        "name_used": "Gemini Models — Five Hour Quota Used",
        "name_remaining": "Gemini Models — Five Hour Limit Remaining",
        "unit": "%",
        "state_class": "measurement",
        "icon": "mdi:clock-fast",
        "bucket_id": "gemini-5h",
        "group": "Gemini Models",
        "type": "dynamic",
    },
    "antigravity_claude_gpt_weekly": {
        "name_used": "Claude & GPT — Weekly Quota Used",
        "name_remaining": "Claude & GPT — Weekly Limit Remaining",
        "unit": "%",
        "state_class": "measurement",
        "icon": "mdi:creation-outline",
        "bucket_id": "3p-weekly",
        "group": "Claude and GPT models",
        "type": "dynamic",
    },
    "antigravity_claude_gpt_5h": {
        "name_used": "Claude & GPT — Five Hour Quota Used",
        "name_remaining": "Claude & GPT — Five Hour Limit Remaining",
        "unit": "%",
        "state_class": "measurement",
        "icon": "mdi:clock-fast",
        "bucket_id": "3p-5h",
        "group": "Claude and GPT models",
        "type": "dynamic",
    },
    "antigravity_gemini_argon_weekly": {
        "name_used": "Gemini 4 Argon — Weekly Quota Used",
        "name_remaining": "Gemini 4 Argon — Weekly Limit Remaining",
        "unit": "%",
        "state_class": "measurement",
        "icon": "mdi:atom-variant",
        "bucket_id": "gemini-argon-wtu-weekly",
        "group": "Gemini 4 Argon",
        "type": "dynamic",
    },
    # 2. Dedicated Used % Entities (Always Increasing: 0% -> 100%)
    "antigravity_gemini_weekly_used": {
        "name": "Gemini Models — Weekly Quota Used",
        "unit": "%",
        "state_class": "measurement",
        "icon": "mdi:google",
        "bucket_id": "gemini-weekly",
        "group": "Gemini Models",
        "type": "used",
    },
    "antigravity_gemini_5h_used": {
        "name": "Gemini Models — Five Hour Quota Used",
        "unit": "%",
        "state_class": "measurement",
        "icon": "mdi:clock-fast",
        "bucket_id": "gemini-5h",
        "group": "Gemini Models",
        "type": "used",
    },
    "antigravity_claude_gpt_weekly_used": {
        "name": "Claude & GPT — Weekly Quota Used",
        "unit": "%",
        "state_class": "measurement",
        "icon": "mdi:creation-outline",
        "bucket_id": "3p-weekly",
        "group": "Claude and GPT models",
        "type": "used",
    },
    "antigravity_claude_gpt_5h_used": {
        "name": "Claude & GPT — Five Hour Quota Used",
        "unit": "%",
        "state_class": "measurement",
        "icon": "mdi:clock-fast",
        "bucket_id": "3p-5h",
        "group": "Claude and GPT models",
        "type": "used",
    },
    "antigravity_gemini_argon_weekly_used": {
        "name": "Gemini 4 Argon — Weekly Quota Used",
        "unit": "%",
        "state_class": "measurement",
        "icon": "mdi:atom-variant",
        "bucket_id": "gemini-argon-wtu-weekly",
        "group": "Gemini 4 Argon",
        "type": "used",
    },
    # 3. Dedicated Remaining % Entities (Always Decreasing: 100% -> 0%)
    "antigravity_gemini_weekly_remaining": {
        "name": "Gemini Models — Weekly Limit Remaining",
        "unit": "%",
        "state_class": "measurement",
        "icon": "mdi:google",
        "bucket_id": "gemini-weekly",
        "group": "Gemini Models",
        "type": "remaining",
    },
    "antigravity_gemini_5h_remaining": {
        "name": "Gemini Models — Five Hour Limit Remaining",
        "unit": "%",
        "state_class": "measurement",
        "icon": "mdi:clock-fast",
        "bucket_id": "gemini-5h",
        "group": "Gemini Models",
        "type": "remaining",
    },
    "antigravity_claude_gpt_weekly_remaining": {
        "name": "Claude & GPT — Weekly Limit Remaining",
        "unit": "%",
        "state_class": "measurement",
        "icon": "mdi:creation-outline",
        "bucket_id": "3p-weekly",
        "group": "Claude and GPT models",
        "type": "remaining",
    },
    "antigravity_claude_gpt_5h_remaining": {
        "name": "Claude & GPT — Five Hour Limit Remaining",
        "unit": "%",
        "state_class": "measurement",
        "icon": "mdi:clock-fast",
        "bucket_id": "3p-5h",
        "group": "Claude and GPT models",
        "type": "remaining",
    },
    "antigravity_gemini_argon_weekly_remaining": {
        "name": "Gemini 4 Argon — Weekly Limit Remaining",
        "unit": "%",
        "state_class": "measurement",
        "icon": "mdi:atom-variant",
        "bucket_id": "gemini-argon-wtu-weekly",
        "group": "Gemini 4 Argon",
        "type": "remaining",
    },
    # 4. Explicit countdown sensors
    "antigravity_gemini_weekly_resets_in": {
        "name": "Gemini Models — Weekly Reset In",
        "unit": None,
        "icon": "mdi:timer-sand",
        "bucket_id": "gemini-weekly",
        "group": "Gemini Models",
        "type": "resets_in",
    },
    "antigravity_gemini_5h_resets_in": {
        "name": "Gemini Models — 5-Hour Reset In",
        "unit": None,
        "icon": "mdi:timer-sand",
        "bucket_id": "gemini-5h",
        "group": "Gemini Models",
        "type": "resets_in",
    },
    "antigravity_claude_gpt_weekly_resets_in": {
        "name": "Claude & GPT — Weekly Reset In",
        "unit": None,
        "icon": "mdi:timer-sand",
        "bucket_id": "3p-weekly",
        "group": "Claude and GPT models",
        "type": "resets_in",
    },
    "antigravity_claude_gpt_5h_resets_in": {
        "name": "Claude & GPT — 5-Hour Reset In",
        "unit": None,
        "icon": "mdi:timer-sand",
        "bucket_id": "3p-5h",
        "group": "Claude and GPT models",
        "type": "resets_in",
    },
    "antigravity_gemini_argon_weekly_resets_in": {
        "name": "Gemini 4 Argon — Weekly Reset In",
        "unit": None,
        "icon": "mdi:timer-sand",
        "bucket_id": "gemini-argon-wtu-weekly",
        "group": "Gemini 4 Argon",
        "type": "resets_in",
    },
    # 5. Overall Sync Status
    "antigravity_quota_status": {
        "name": "Antigravity Quota Status",
        "unit": None,
        "icon": "mdi:cloud-check",
        "type": "status",
    },
    # 6. Live Burn Rate Sensors (% / hour)
    "antigravity_gemini_5h_burn_rate": {
        "name": "Gemini Models — 5-Hour Burn Rate",
        "unit": "%/h",
        "state_class": "measurement",
        "icon": "mdi:fire",
        "bucket_id": "gemini-5h",
        "group": "Gemini Models",
        "type": "burn_rate",
    },
    "antigravity_gemini_weekly_burn_rate": {
        "name": "Gemini Models — Weekly Burn Rate",
        "unit": "%/h",
        "state_class": "measurement",
        "icon": "mdi:fire",
        "bucket_id": "gemini-weekly",
        "group": "Gemini Models",
        "type": "burn_rate",
    },
    "antigravity_claude_gpt_5h_burn_rate": {
        "name": "Claude & GPT — 5-Hour Burn Rate",
        "unit": "%/h",
        "state_class": "measurement",
        "icon": "mdi:fire",
        "bucket_id": "3p-5h",
        "group": "Claude and GPT models",
        "type": "burn_rate",
    },
    "antigravity_claude_gpt_weekly_burn_rate": {
        "name": "Claude & GPT — Weekly Burn Rate",
        "unit": "%/h",
        "state_class": "measurement",
        "icon": "mdi:fire",
        "bucket_id": "3p-weekly",
        "group": "Claude and GPT models",
        "type": "burn_rate",
    },
    "antigravity_gemini_argon_weekly_burn_rate": {
        "name": "Gemini 4 Argon — Weekly Burn Rate",
        "unit": "%/h",
        "state_class": "measurement",
        "icon": "mdi:fire",
        "bucket_id": "gemini-argon-wtu-weekly",
        "group": "Gemini 4 Argon",
        "type": "burn_rate",
    },
    # 7. Projected Usage at Reset Time (%)
    "antigravity_gemini_5h_projected": {
        "name": "Gemini Models — 5-Hour Projected Usage",
        "unit": "%",
        "state_class": "measurement",
        "icon": "mdi:chart-bell-curve-cumulative",
        "bucket_id": "gemini-5h",
        "group": "Gemini Models",
        "type": "projected",
    },
    "antigravity_gemini_weekly_projected": {
        "name": "Gemini Models — Weekly Projected Usage",
        "unit": "%",
        "state_class": "measurement",
        "icon": "mdi:chart-bell-curve-cumulative",
        "bucket_id": "gemini-weekly",
        "group": "Gemini Models",
        "type": "projected",
    },
    "antigravity_claude_gpt_5h_projected": {
        "name": "Claude & GPT — 5-Hour Projected Usage",
        "unit": "%",
        "state_class": "measurement",
        "icon": "mdi:chart-bell-curve-cumulative",
        "bucket_id": "3p-5h",
        "group": "Claude and GPT models",
        "type": "projected",
    },
    "antigravity_claude_gpt_weekly_projected": {
        "name": "Claude & GPT — Weekly Projected Usage",
        "unit": "%",
        "state_class": "measurement",
        "icon": "mdi:chart-bell-curve-cumulative",
        "bucket_id": "3p-weekly",
        "group": "Claude and GPT models",
        "type": "projected",
    },
    "antigravity_gemini_argon_weekly_projected": {
        "name": "Gemini 4 Argon — Weekly Projected Usage",
        "unit": "%",
        "state_class": "measurement",
        "icon": "mdi:chart-bell-curve-cumulative",
        "bucket_id": "gemini-argon-wtu-weekly",
        "group": "Gemini 4 Argon",
        "type": "projected",
    },
    # 8. Quota Runway (Hours left before 100% exhaustion)
    "antigravity_gemini_5h_runway": {
        "name": "Gemini Models — 5-Hour Runway",
        "unit": None,
        "icon": "mdi:airplane-takeoff",
        "bucket_id": "gemini-5h",
        "group": "Gemini Models",
        "type": "runway",
    },
    "antigravity_gemini_weekly_runway": {
        "name": "Gemini Models — Weekly Runway",
        "unit": None,
        "icon": "mdi:airplane-takeoff",
        "bucket_id": "gemini-weekly",
        "group": "Gemini Models",
        "type": "runway",
    },
    "antigravity_claude_gpt_5h_runway": {
        "name": "Claude & GPT — 5-Hour Runway",
        "unit": None,
        "icon": "mdi:airplane-takeoff",
        "bucket_id": "3p-5h",
        "group": "Claude and GPT models",
        "type": "runway",
    },
    "antigravity_claude_gpt_weekly_runway": {
        "name": "Claude & GPT — Weekly Runway",
        "unit": None,
        "icon": "mdi:airplane-takeoff",
        "bucket_id": "3p-weekly",
        "group": "Claude and GPT models",
        "type": "runway",
    },
    "antigravity_gemini_argon_weekly_runway": {
        "name": "Gemini 4 Argon — Weekly Runway",
        "unit": None,
        "icon": "mdi:airplane-takeoff",
        "bucket_id": "gemini-argon-wtu-weekly",
        "group": "Gemini 4 Argon",
        "type": "runway",
    },
    # 9. Pace Status
    "antigravity_gemini_5h_pace": {
        "name": "Gemini Models — 5-Hour Pace Status",
        "unit": None,
        "icon": "mdi:speedometer",
        "bucket_id": "gemini-5h",
        "group": "Gemini Models",
        "type": "pace",
    },
    "antigravity_gemini_weekly_pace": {
        "name": "Gemini Models — Weekly Pace Status",
        "unit": None,
        "icon": "mdi:speedometer",
        "bucket_id": "gemini-weekly",
        "group": "Gemini Models",
        "type": "pace",
    },
    "antigravity_claude_gpt_5h_pace": {
        "name": "Claude & GPT — 5-Hour Pace Status",
        "unit": None,
        "icon": "mdi:speedometer",
        "bucket_id": "3p-5h",
        "group": "Claude and GPT models",
        "type": "pace",
    },
    "antigravity_claude_gpt_weekly_pace": {
        "name": "Claude & GPT — Weekly Pace Status",
        "unit": None,
        "icon": "mdi:speedometer",
        "bucket_id": "3p-weekly",
        "group": "Claude and GPT models",
        "type": "pace",
    },
    "antigravity_gemini_argon_weekly_pace": {
        "name": "Gemini 4 Argon — Weekly Pace Status",
        "unit": None,
        "icon": "mdi:speedometer",
        "bucket_id": "gemini-argon-wtu-weekly",
        "group": "Gemini 4 Argon",
        "type": "pace",
    },
    # 10. Integration Version & Diagnostics
    "antigravity_version": {
        "name": "Antigravity Integration Version",
        "unit": None,
        "icon": "mdi:tag-outline",
        "type": "version",
    },
}


# ---------------------------------------------------------------------------
# Antigravity Binary Sensor definitions (Over-quota risk)
# ---------------------------------------------------------------------------

ANTIGRAVITY_BINARY_SENSOR_DEFS: dict[str, dict] = {
    "antigravity_gemini_5h_over_quota_risk": {
        "name": "Gemini Models — 5-Hour Over-Quota Risk",
        "icon": "mdi:alert-circle-outline",
        "bucket_id": "gemini-5h",
        "group": "Gemini Models",
    },
    "antigravity_gemini_weekly_over_quota_risk": {
        "name": "Gemini Models — Weekly Over-Quota Risk",
        "icon": "mdi:alert-circle-outline",
        "bucket_id": "gemini-weekly",
        "group": "Gemini Models",
    },
    "antigravity_claude_gpt_5h_over_quota_risk": {
        "name": "Claude & GPT — 5-Hour Over-Quota Risk",
        "icon": "mdi:alert-circle-outline",
        "bucket_id": "3p-5h",
        "group": "Claude and GPT models",
    },
    "antigravity_claude_gpt_weekly_over_quota_risk": {
        "name": "Claude & GPT — Weekly Over-Quota Risk",
        "icon": "mdi:alert-circle-outline",
        "bucket_id": "3p-weekly",
        "group": "Claude and GPT models",
    },
    "antigravity_gemini_argon_weekly_over_quota_risk": {
        "name": "Gemini 4 Argon — Weekly Over-Quota Risk",
        "icon": "mdi:alert-circle-outline",
        "bucket_id": "gemini-argon-wtu-weekly",
        "group": "Gemini 4 Argon",
    },
}



