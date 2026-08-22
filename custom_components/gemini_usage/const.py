"""
Constants for the Gemini Usage Monitor integration.

This module is the single source of truth for every magic string, URL,
default value, and sensor definition used across the integration.  Keeping
them here avoids duplication and makes tuning (e.g. changing a default poll
interval) a one-line change.

Architecture overview
---------------------
The integration has **three independent data sources**, each with its own
poll interval and cache:

    Source 1 — Gemini REST API (rate-limit headers)
        Every model capable of ``generateContent`` is probed with a
        single-token request.  The ``x-ratelimit-*`` response headers expose
        the current RPM / TPM / RPD remaining and limit values.

    Source 2 — GCP Cloud Monitoring API (quota metrics)
        Uses a GCP service account (optional) to query the
        ``serviceruntime.googleapis.com`` metric domain via the v3
        timeSeries endpoint.  Returns aggregated token usage and quota
        limits per model for the last hour.

    Source 3 — Local Antigravity transcript scan (approximate usage)
        Reads the JSONL ``transcript.jsonl`` files written by the
        Antigravity IDE into the user's brain directory.  Token counts
        are extracted with a regex and attributed to models detected in
        the same log line.

All three sources contribute to per-model ``SensorEntity`` instances that
are created dynamically as models are discovered.  Three aggregate sensors
(totals + model count) are always present regardless of which sources are
configured.
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
# These strings are the keys stored in ``ConfigEntry.data`` and
# ``ConfigEntry.options`` respectively.
# ---------------------------------------------------------------------------

CONF_API_KEY = "api_key"
"""Gemini API key — required; used for model-list retrieval and rate-limit probing."""

CONF_GCP_PROJECT_ID = "gcp_project_id"
"""Google Cloud project ID — required only when GCP Cloud Monitoring is enabled."""

CONF_GCP_SERVICE_ACCOUNT_JSON = "gcp_service_account_json"
"""Full JSON content of a GCP service account key file.
The account needs the ``roles/monitoring.viewer`` IAM role."""

CONF_LOCAL_DB_PATH = "local_db_path"
"""Absolute path to the Antigravity ``brain`` directory that contains per-
conversation sub-directories, each with a ``.system_generated/logs/transcript.jsonl``
file.  Leave empty to disable local usage tracking."""

CONF_RATE_LIMIT_INTERVAL = "rate_limit_interval"
"""Seconds between successive rate-limit header probes (Source 1)."""

CONF_GCP_INTERVAL = "gcp_interval"
"""Seconds between successive GCP Cloud Monitoring queries (Source 2)."""

CONF_LOCAL_INTERVAL = "local_interval"
"""Seconds between successive local transcript scans (Source 3)."""

# ---------------------------------------------------------------------------
# Default poll intervals  (all values are in seconds)
# ---------------------------------------------------------------------------

DEFAULT_RATE_LIMIT_INTERVAL = 60
"""Probe the Gemini API every 60 s to capture rate-limit headers.
Keeps live RPM/TPM/RPD sensors reasonably fresh without inflating quota spend."""

DEFAULT_GCP_INTERVAL = 300
"""Query GCP Cloud Monitoring every 5 minutes.
The Monitoring API aggregates data in 60-second buckets, so more frequent
polls would return the same data and waste quota."""

DEFAULT_LOCAL_INTERVAL = 120
"""Re-scan local transcripts every 2 minutes.
Transcripts are only written while Antigravity is active, so a short interval
ensures sensors reflect recent activity without excessive disk I/O."""

DEFAULT_MODEL_LIST_INTERVAL = 3600
"""Refresh the list of available models hourly.
Google occasionally adds or deprecates model versions; an hourly refresh means
new models get sensors within an hour of their appearance, and stale entities
stop receiving data without cluttering the registry."""

# ---------------------------------------------------------------------------
# Gemini REST API endpoints
# ---------------------------------------------------------------------------

GEMINI_API_BASE = "https://generativelanguage.googleapis.com/v1beta"
"""Base URL for the Gemini Developer API (v1beta).  All model operations
(listing, generating content, counting tokens) are under this path."""

GEMINI_MODELS_ENDPOINT = f"{GEMINI_API_BASE}/models"
"""Endpoint for ``GET /v1beta/models`` — returns the paginated list of
all models visible to the provided API key."""

# ---------------------------------------------------------------------------
# GCP Cloud Monitoring API
# ---------------------------------------------------------------------------

GCP_MONITORING_BASE = "https://monitoring.googleapis.com/v3"
"""Base URL for the Cloud Monitoring REST API v3."""

GEMINI_GCP_SERVICE = "generativelanguage.googleapis.com"
"""The ``service`` label value used when filtering Cloud Monitoring time-series
to only Gemini API quota metrics."""

GCP_MONITORING_SCOPES = ["https://www.googleapis.com/auth/monitoring.read"]
"""OAuth2 scope required by the service account to call the Monitoring API.
Read-only access is sufficient — the integration never writes metrics."""

# ---------------------------------------------------------------------------
# GCP Monitoring metric types
# These are the ``metric.type`` strings used in Cloud Monitoring filter
# expressions.  They live under the ``serviceruntime`` resource.
# ---------------------------------------------------------------------------

METRIC_QUOTA_RATE_USAGE = "serviceruntime.googleapis.com/quota/rate/net_usage"
"""Net rate of API requests counted against quota — used to derive the
token-per-hour figures shown in the ``tokens_input_1h`` / ``tokens_output_1h``
sensors."""

METRIC_QUOTA_LIMIT = "serviceruntime.googleapis.com/quota/limit"
"""The ceiling for a given quota dimension (e.g. tokens-per-minute).
Combined with ``METRIC_QUOTA_RATE_USAGE`` to compute ``quota_used_pct``."""

METRIC_QUOTA_EXCEEDED = "serviceruntime.googleapis.com/quota/exceeded"
"""Count of requests rejected because they exceeded quota.
Reserved for future use — not currently exposed as a sensor but defined
here for completeness."""

# ---------------------------------------------------------------------------
# Local Antigravity paths
# ---------------------------------------------------------------------------

ANTIGRAVITY_BRAIN_PATH_TEMPLATES = [
    r"%USERPROFILE%\.gemini\antigravity\brain",
    r"%APPDATA%\.gemini\antigravity\brain",
]
"""Ordered list of Windows environment-variable templates to expand when
auto-detecting the Antigravity brain directory.  The first path that exists
on disk is offered as the default during setup.

Each sub-directory of the brain directory corresponds to one conversation and
must contain ``.system_generated/logs/transcript.jsonl``."""

# ---------------------------------------------------------------------------
# Entity-ID slug helpers
# ---------------------------------------------------------------------------

MODEL_SLUG_REPLACE = {"-": "_", ".": "_", "/": "_", ":": "_"}
"""Character-replacement map used to convert a raw Gemini model name
(e.g. ``gemini-2.0-flash-exp``) into a HA-safe entity-ID segment.
All replaced characters become underscores so the slug matches HA's
``[a-z0-9_]+`` entity-ID convention."""

# ---------------------------------------------------------------------------
# Per-model sensor definitions
# ---------------------------------------------------------------------------

SENSOR_DEFS: dict[str, dict] = {
    # ------------------------------------------------------------------
    # Source 1 — Gemini API rate-limit response headers
    # Headers are returned by *every* generateContent response and expose
    # the current sliding-window counter state for this API key/project.
    # ------------------------------------------------------------------
    "rpm_remaining": {
        "name": "Req/Min Remaining",
        "unit": "requests",
        "icon": "mdi:counter",
        # ``api_headers`` → pulled from the ``rate_limits`` data bucket
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
        # Daily request budget — resets at midnight Pacific time
        "source": "api_headers",
    },
    "rpd_limit": {
        "name": "Req/Day Limit",
        "unit": "requests",
        "icon": "mdi:speedometer",
        "source": "api_headers",
    },
    # ------------------------------------------------------------------
    # Source 2 — GCP Cloud Monitoring
    # Only available when a GCP service account is configured.
    # Values are aggregated over the last 60-minute window.
    # ------------------------------------------------------------------
    "tokens_input_1h": {
        "name": "Input Tokens (1h)",
        "unit": "tokens",
        "icon": "mdi:upload",
        # ``gcp`` → pulled from the ``gcp`` data bucket
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
        # Derived: (input_1h + output_1h) / quota_limit * 100
        "source": "gcp",
    },
    # ------------------------------------------------------------------
    # Source 3 — Local Antigravity transcript scan
    # Only available when a brain directory is configured.
    # Token counts are approximate — extracted from human-readable
    # checkpoint text ("Thought for 3s, 286 tokens") using a regex.
    # ------------------------------------------------------------------
    "tokens_today": {
        "name": "Tokens Today (Local)",
        "unit": "tokens",
        "icon": "mdi:calendar-today",
        # ``local`` → pulled from the ``local`` data bucket
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
# Aggregate (non-per-model) sensor definitions
# These sensors are always created on integration setup, regardless of which
# data sources are active.  They summarise data across all models.
# ---------------------------------------------------------------------------

AGGREGATE_SENSOR_DEFS: dict[str, dict] = {
    "total_tokens_today": {
        "name": "Total Tokens Today",
        "unit": "tokens",
        "icon": "mdi:sigma",
        # Sum of all model ``today`` buckets from the local transcript scan
        "source": "local",
    },
    "total_tokens_this_month": {
        "name": "Total Tokens This Month",
        "unit": "tokens",
        "icon": "mdi:sigma",
        # Sum of all model ``this_month`` buckets from the local transcript scan
        "source": "local",
    },
    "model_count": {
        "name": "Available Model Count",
        "unit": "models",
        "icon": "mdi:robot-outline",
        # Length of the model list returned by ``GET /v1beta/models``
        "source": "api_headers",
    },
}
