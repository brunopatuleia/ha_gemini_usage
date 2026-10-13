"""
Data update coordinator for Gemini Usage Monitor.

This module handles asynchronous data polling, model catalog discovery,
live API health checks, and optional metrics from Google Cloud Monitoring
and local conversation transcript logs.

Data Flow & Lifecycle:
----------------------
1. `_fetch_model_list`: Calls `GET /v1beta/models` with pagination support to build
   the list of all models accessible to the user's API key. Extracts metadata such as
   `inputTokenLimit` and `outputTokenLimit`.
2. `_check_api_health_and_probe`: Runs lightweight requests to determine API availability
   and measure round-trip HTTP latency in milliseconds.
3. `_fetch_gcp_metrics` (Optional): Authenticates using a GCP Service Account to fetch
   1-hour rolling quota time-series metrics from Google Cloud Monitoring.
4. `_fetch_local_usage` (Optional): Scans local Antigravity transcript JSONL files to
   compute daily and monthly token consumption tallies.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import shutil
import subprocess
import time
from datetime import datetime, timezone
from typing import Any

import aiohttp
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed


from .const import (
    CONF_API_KEY,
    CONF_GCP_PROJECT_ID,
    CONF_GCP_SERVICE_ACCOUNT_JSON,
    CONF_LOCAL_DB_PATH,
    DEFAULT_GCP_INTERVAL,
    DEFAULT_LOCAL_INTERVAL,
    DEFAULT_MODEL_LIST_INTERVAL,
    DEFAULT_RATE_LIMIT_INTERVAL,
    DOMAIN,
    GCP_MONITORING_BASE,
    GCP_MONITORING_SCOPES,
    GEMINI_API_BASE,
    GEMINI_GCP_SERVICE,
    GEMINI_MODELS_ENDPOINT,
    METRIC_QUOTA_LIMIT,
    METRIC_QUOTA_RATE_USAGE,
)

_LOGGER = logging.getLogger(__name__)

# Regular expressions for local transcript parsing
_TOKEN_RE = re.compile(r"(\d[\d,]*)\s+tokens?", re.IGNORECASE)
_MODEL_RE = re.compile(r"gemini[-/][\w.\-]+", re.IGNORECASE)


def _model_slug(name: str) -> str:
    """Convert a Gemini model name into a Home Assistant-safe entity-ID segment.

    Examples:
        'models/gemini-2.5-flash' -> 'gemini_2_5_flash'
        'models/gemini-3.7-flash' -> 'gemini_3_7_flash'
    """
    slug = name.lower()
    for ch, repl in {"-": "_", ".": "_", "/": "_", ":": "_"}.items():
        slug = slug.replace(ch, repl)
    if slug.startswith("models_"):
        slug = slug[len("models_"):]
    return slug


def _format_resets_in(reset_time_iso: str) -> str:
    """Compute human-friendly relative reset duration like '2d 19h' or '3h 22m'."""
    try:
        target = datetime.fromisoformat(reset_time_iso.replace("Z", "+00:00"))
        if target.tzinfo is None:
            target = target.replace(tzinfo=timezone.utc)
        now = datetime.now(timezone.utc)
        diff = target - now
        total_seconds = int(diff.total_seconds())
        if total_seconds <= 0:
            return "Now"
        days = total_seconds // 86400
        hours = (total_seconds % 86400) // 3600
        minutes = (total_seconds % 3600) // 60
        if days > 0:
            return f"{days}d {hours}h"
        if hours > 0:
            return f"{hours}h {minutes}m"
        return f"{minutes}m"
    except Exception:
        return "Unknown"


def parse_antigravity_data(data: dict[str, Any]) -> dict[str, Any]:
    """Parse raw JSON from agy /quota or sync script into structured bucket dict."""
    result: dict[str, Any] = {
        "status": "Online",
        "last_updated": datetime.now(timezone.utc).isoformat(),
        "buckets": {},
    }

    groups = []
    if "command" in data and isinstance(data["command"], dict):
        cmd_data = data["command"].get("data", {})
        groups = cmd_data.get("groups", [])
    elif "groups" in data:
        groups = data["groups"]
    elif "buckets" in data and isinstance(data["buckets"], dict):
        result["buckets"] = data["buckets"]
        return result

    for group in groups:
        group_name = group.get("name", "")
        for bucket in group.get("buckets", []):
            b_id = bucket.get("id")
            if not b_id:
                continue
            rem_frac_raw = bucket.get("remaining_fraction")
            if rem_frac_raw is None:
                continue
            try:
                rem_frac = max(0.0, min(1.0, float(rem_frac_raw)))
            except (ValueError, TypeError):
                continue

            used_frac = max(0.0, min(1.0, 1.0 - rem_frac))
            rem_pct = round(rem_frac * 100.0, 1)
            used_pct = round(used_frac * 100.0, 1)
            reset_time = bucket.get("reset_time", "")
            resets_in = _format_resets_in(reset_time) if reset_time else "Unknown"

            # Calculate Predictive Burn Rate, Projected Usage, Runway, and Risk
            window_str = bucket.get("window", "")
            window_seconds = 5 * 3600 if "5h" in window_str or "5h" in b_id else 7 * 86400  # 5h or 7d

            time_to_reset_seconds: int | None = None
            if reset_time:
                try:
                    target_dt = datetime.fromisoformat(reset_time.replace("Z", "+00:00"))
                    if target_dt.tzinfo is None:
                        target_dt = target_dt.replace(tzinfo=timezone.utc)
                    now_dt = datetime.now(timezone.utc)
                    time_to_reset_seconds = max(0, int((target_dt - now_dt).total_seconds()))
                except Exception:
                    time_to_reset_seconds = None

            if time_to_reset_seconds is not None:
                elapsed_seconds = max(0, window_seconds - time_to_reset_seconds)
                elapsed_hours = elapsed_seconds / 3600.0
                hours_to_reset = time_to_reset_seconds / 3600.0

                # Minimum observation window: at least 15m or 2% of total window to prevent early spikes
                min_obs_hours = max(0.25, (window_seconds * 0.02) / 3600.0)
                if elapsed_hours >= min_obs_hours and used_pct > 0:
                    burn_rate_pct_per_h = round(used_pct / elapsed_hours, 2)
                else:
                    burn_rate_pct_per_h = 0.0
            else:
                elapsed_hours = 0.0
                hours_to_reset = 0.0
                burn_rate_pct_per_h = 0.0

            # Projected total usage at reset time (%)
            if time_to_reset_seconds is not None and hours_to_reset > 0 and burn_rate_pct_per_h > 0:
                projected_pct = round(min(500.0, used_pct + (burn_rate_pct_per_h * hours_to_reset)), 1)
            else:
                projected_pct = used_pct

            # Runway (hours until 100% exhaustion)
            if burn_rate_pct_per_h > 0 and rem_frac > 0:
                runway_hours = rem_pct / burn_rate_pct_per_h
                total_runway_sec = int(runway_hours * 3600)
                r_days = total_runway_sec // 86400
                r_hrs = (total_runway_sec % 86400) // 3600
                r_mins = (total_runway_sec % 3600) // 60
                if r_days > 0:
                    runway_str = f"{r_days}d {r_hrs}h"
                elif r_hrs > 0:
                    runway_str = f"{r_hrs}h {r_mins}m"
                else:
                    runway_str = f"{r_mins}m"
            elif rem_frac <= 0 or used_pct >= 100:
                runway_hours = 0.0
                runway_str = "0m (Exhausted)"
            else:
                runway_hours = 999.0
                runway_str = "Sufficient"

            # Over-quota risk & pace status
            if rem_frac <= 0 or used_pct >= 100:
                pace_status = "Exhausted"
                is_over_quota_risk = True
            elif time_to_reset_seconds is not None and time_to_reset_seconds > 0 and projected_pct > 100.0:
                pace_status = "High Risk (Over Quota)"
                is_over_quota_risk = True
            elif projected_pct > 80.0:
                pace_status = "Elevated"
                is_over_quota_risk = False
            else:
                pace_status = "Safe"
                is_over_quota_risk = False

            # Safe pace target with reasonable bounds
            if hours_to_reset >= 0.05:
                safe_pace_pct_per_h = round(min(100.0, rem_pct / hours_to_reset), 2)
            elif rem_frac <= 0:
                safe_pace_pct_per_h = 0.0
            else:
                safe_pace_pct_per_h = 100.0

            result["buckets"][b_id] = {
                "id": b_id,
                "name": bucket.get("name", b_id),
                "group": group_name,
                "description": bucket.get("description", ""),
                "window": bucket.get("window", ""),
                "remaining_fraction": rem_frac,
                "used_fraction": round(used_frac, 4),
                "remaining_pct": rem_pct,
                "used_pct": used_pct,
                "reset_time": reset_time,
                "resets_in": resets_in,
                "burn_rate_pct_per_h": burn_rate_pct_per_h,
                "projected_pct": projected_pct,
                "runway": runway_str,
                "runway_hours": round(runway_hours, 2),
                "pace_status": pace_status,
                "is_over_quota_risk": is_over_quota_risk,
                "safe_pace_pct_per_h": safe_pace_pct_per_h,
            }

    return result



class GeminiUsageCoordinator(DataUpdateCoordinator):
    """Central coordinator that manages data polling for Gemini Usage Monitor."""


    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        update_interval,
        rate_limit_interval: int = DEFAULT_RATE_LIMIT_INTERVAL,
        gcp_interval: int = DEFAULT_GCP_INTERVAL,
        local_interval: int = DEFAULT_LOCAL_INTERVAL,
    ) -> None:
        """Initialise coordinator with user settings and default intervals."""
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=update_interval,
        )
        self._entry = entry
        # Read options with fallback to entry.data so Options Flow edits take immediate effect
        self._api_key: str = entry.options.get(CONF_API_KEY, entry.data.get(CONF_API_KEY, ""))
        self._gcp_project_id: str = entry.options.get(CONF_GCP_PROJECT_ID, entry.data.get(CONF_GCP_PROJECT_ID, ""))
        self._gcp_sa_json_str: str = entry.options.get(CONF_GCP_SERVICE_ACCOUNT_JSON, entry.data.get(CONF_GCP_SERVICE_ACCOUNT_JSON, ""))
        self._local_path: str = entry.options.get(CONF_LOCAL_DB_PATH, entry.data.get(CONF_LOCAL_DB_PATH, ""))

        self._rate_limit_interval = rate_limit_interval
        self._gcp_interval = gcp_interval
        self._local_interval = local_interval

        # Check agy CLI presence once at startup to avoid repeated disk traversals in event loop
        self._agy_available = shutil.which("agy") is not None

        # Timestamps for tracking individual poll intervals
        self._last_api_health_fetch: float = 0.0
        self._last_gcp_fetch: float = 0.0
        self._last_local_fetch: float = 0.0
        self._last_model_list_fetch: float = 0.0

        # State cache
        self._api_status: str = "Unknown"
        self._api_latency_ms: float | None = None
        self._models: list[dict] = []
        self._model_metadata: dict[str, dict] = {}
        self._rate_limit_data: dict[str, dict] = {}
        self._gcp_data: dict[str, dict] = {}
        self._local_data: dict[str, dict] = {}
        self._antigravity_data: dict[str, Any] = {}

    @property
    def is_gcp_configured(self) -> bool:
        """Return True if GCP project ID and Service Account JSON are present."""
        return bool(self._gcp_project_id and self._gcp_sa_json_str)

    @property
    def is_local_configured(self) -> bool:
        """Return True if local transcript directory path is configured."""
        return bool(self._local_path)

    async def _async_update_data(self) -> dict[str, Any]:
        """Fetch data from active sources respecting individual intervals."""
        now = time.monotonic()

        # Refresh model catalogue hourly (or on initial start)
        if (
            not self._models
            or (now - self._last_model_list_fetch >= DEFAULT_MODEL_LIST_INTERVAL)
        ):
            await self._fetch_model_list()
            self._last_model_list_fetch = now

        # Execute scheduled polling tasks concurrently
        tasks = []
        if now - self._last_api_health_fetch >= self._rate_limit_interval:
            tasks.append(self._check_api_health_and_probe())

        if self.is_gcp_configured and (now - self._last_gcp_fetch >= self._gcp_interval):
            tasks.append(self._fetch_gcp_metrics())

        if self.is_local_configured and (now - self._last_local_fetch >= self._local_interval):
            tasks.append(self._fetch_local_usage())

        # Check local agy CLI if available on the system
        if self._agy_available:
            agy_data = await self.hass.async_add_executor_job(self._fetch_local_agy_quota)
            if agy_data:
                parsed = parse_antigravity_data(agy_data)
                if parsed.get("buckets"):
                    self._antigravity_data = parsed

        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

        return self._build_data_dict()

    def _build_data_dict(self) -> dict[str, Any]:
        """Merge all cached data into a single payload for sensor entities."""
        return {
            "api_status": self._api_status,
            "api_latency": self._api_latency_ms,
            "models": self._models,
            "model_metadata": self._model_metadata,
            "rate_limits": self._rate_limit_data,
            "gcp": self._gcp_data,
            "local": self._local_data,
            "antigravity": self._antigravity_data,
        }

    async def async_update_antigravity_data(self, raw_data: dict[str, Any]) -> None:
        """Process incoming Antigravity quota payload (from webhook or external push)."""
        parsed = parse_antigravity_data(raw_data)
        if parsed.get("buckets"):
            self._antigravity_data = parsed
            self.async_set_updated_data(self._build_data_dict())

    def _fetch_local_agy_quota(self) -> dict[str, Any] | None:
        """Run local agy CLI to fetch live quota JSON."""
        try:
            res = subprocess.run(
                ["agy", "--print", "/quota", "--output-format", "json"],
                capture_output=True,
                text=True,
                timeout=12,
                check=True,
            )
            return json.loads(res.stdout)
        except Exception as exc:
            _LOGGER.debug("Local agy CLI execution failed or timed out: %s", exc)
            return None


    async def _fetch_model_list(self) -> None:
        """Fetch full model catalogue from Gemini API and extract context limits."""
        if not self._api_key:
            self._api_status = "Ready (Antigravity Quotas)"
            return

        models = []
        page_token = ""
        url_base = f"{GEMINI_MODELS_ENDPOINT}?pageSize=50"
        headers = {"x-goog-api-key": self._api_key}
        start_time = time.monotonic()
        session = async_get_clientsession(self.hass)

        try:
            while True:
                url = url_base + (f"&pageToken={page_token}" if page_token else "")
                async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                    if resp.status != 200:
                        body = await resp.text()
                        _LOGGER.warning("Failed to fetch model list: HTTP %s — %s", resp.status, body[:200])
                        self._api_status = f"HTTP {resp.status}"
                        if not self._models:
                            raise UpdateFailed(f"HTTP {resp.status} fetching model list: {body[:100]}")
                        return

                    data = await resp.json()
                    models.extend(data.get("models", []))
                    page_token = data.get("nextPageToken", "")
                    if not page_token:
                        break

            self._models = models
            self._api_status = "Online"
            self._api_latency_ms = round((time.monotonic() - start_time) * 1000, 1)

            # Build metadata lookup per model slug
            meta_map = {}
            for m in models:
                raw_name = m.get("name", "")
                if not raw_name:
                    continue
                slug = _model_slug(raw_name)
                meta_map[slug] = {
                    "input_token_limit": m.get("inputTokenLimit"),
                    "output_token_limit": m.get("outputTokenLimit"),
                    "display_name": m.get("displayName") or raw_name,
                    "description": m.get("description", ""),
                    "supported_generation_methods": m.get("supportedGenerationMethods", []),
                }
            self._model_metadata = meta_map
            _LOGGER.debug("Discovered %d Gemini models", len(models))

        except aiohttp.ClientError as exc:
            self._api_status = "Offline"
            _LOGGER.warning("Error connecting to Gemini API: %s", exc)
            if not self._models:
                raise UpdateFailed(f"Connection error to Gemini API: {exc}") from exc

    async def _check_api_health_and_probe(self) -> None:
        """Measure API availability and response latency."""
        if not self._api_key:
            self._api_status = "Ready (Antigravity Quotas)"
            self._last_api_health_fetch = time.monotonic()
            return

        url = f"{GEMINI_MODELS_ENDPOINT}?pageSize=1"
        headers = {"x-goog-api-key": self._api_key}
        start_time = time.monotonic()
        session = async_get_clientsession(self.hass)
        try:
            async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                latency = round((time.monotonic() - start_time) * 1000, 1)
                if resp.status == 200:
                    self._api_status = "Online"
                    self._api_latency_ms = latency
                else:
                    self._api_status = f"HTTP {resp.status}"
                    self._api_latency_ms = latency
        except aiohttp.ClientError as exc:
            self._api_status = "Offline"
            self._api_latency_ms = None
            _LOGGER.debug("API health check connection error: %s", exc)

        self._last_api_health_fetch = time.monotonic()


    async def _fetch_gcp_metrics(self) -> None:
        """Query Google Cloud Monitoring for quota usage and ceilings."""
        if not self.is_gcp_configured:
            return

        try:
            sa_info = json.loads(self._gcp_sa_json_str)
            token = await self.hass.async_add_executor_job(self._get_gcp_access_token, sa_info)
        except Exception as exc:
            _LOGGER.warning("GCP authentication error: %s", exc)
            return

        now_iso = datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        one_hour_ago = datetime.fromtimestamp(time.time() - 3600, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

        params = {
            "filter": f'metric.type="{METRIC_QUOTA_RATE_USAGE}" AND resource.labels.service="{GEMINI_GCP_SERVICE}"',
            "interval.startTime": one_hour_ago,
            "interval.endTime": now_iso,
            "aggregation.alignmentPeriod": "3600s",
            "aggregation.perSeriesAligner": "ALIGN_SUM",
        }
        url = f"{GCP_MONITORING_BASE}/projects/{self._gcp_project_id}/timeSeries"
        headers = {"Authorization": f"Bearer {token}"}

        try:
            session = async_get_clientsession(self.hass)
            async with session.get(url, params=params, headers=headers, timeout=aiohttp.ClientTimeout(total=20)) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        new_data = {}
                        for series in data.get("timeSeries", []):
                            metric_labels = series.get("metric", {}).get("labels", {})
                            resource_labels = series.get("resource", {}).get("labels", {})
                            quota_metric = metric_labels.get("quota_metric", "")
                            model = resource_labels.get("model", "") or metric_labels.get("model", "")
                            if not model:
                                continue
                            slug = _model_slug(model)
                            points = series.get("points", [])
                            total_usage = sum(int(p.get("value", {}).get("int64Value", 0) or 0) for p in points)
                            entry = new_data.setdefault(slug, {"model": model})
                            if "input" in quota_metric:
                                entry["tokens_input_1h"] = total_usage
                            elif "output" in quota_metric:
                                entry["tokens_output_1h"] = total_usage
                            else:
                                entry["usage_1h"] = total_usage
                        self._gcp_data = new_data
        except aiohttp.ClientError as exc:
            _LOGGER.debug("GCP metric query failed: %s", exc)

        self._last_gcp_fetch = time.monotonic()

    def _get_gcp_access_token(self, sa_info: dict) -> str:
        """Obtain a short-lived GCP OAuth2 access token via service account credentials."""
        import google.auth.transport.requests as ga_requests
        import google.oauth2.service_account as sa_module

        credentials = sa_module.Credentials.from_service_account_info(
            sa_info, scopes=GCP_MONITORING_SCOPES
        )
        credentials.refresh(ga_requests.Request())
        return credentials.token

    async def _fetch_local_usage(self) -> None:
        """Scan local Antigravity conversation transcript files in executor thread."""
        if not self.is_local_configured:
            return
        result = await self.hass.async_add_executor_job(self._scan_transcripts)
        self._local_data = result
        self._last_local_fetch = time.monotonic()

    def _scan_transcripts(self) -> dict[str, dict]:
        """Walk brain directory and aggregate token counts by date and model."""
        brain_path = self._local_path
        if not brain_path or not os.path.isdir(brain_path):
            return {}

        today = datetime.now().strftime("%Y-%m-%d")
        this_month = datetime.now().strftime("%Y-%m")
        totals = {}

        for conv_dir in os.scandir(brain_path):
            if not conv_dir.is_dir():
                continue
            transcript = os.path.join(conv_dir.path, ".system_generated", "logs", "transcript.jsonl")
            if not os.path.isfile(transcript):
                continue
            try:
                self._parse_transcript(transcript, today, this_month, totals)
            except Exception as exc:
                _LOGGER.debug("Transcript parse error for %s: %s", transcript, exc)

        return totals

    def _parse_transcript(self, transcript_path: str, today: str, this_month: str, totals: dict) -> None:
        """Parse individual transcript file for token metrics."""
        with open(transcript_path, "r", encoding="utf-8", errors="ignore") as fh:
            for raw_line in fh:
                raw_line = raw_line.strip()
                if not raw_line:
                    continue
                try:
                    obj = json.loads(raw_line)
                except json.JSONDecodeError:
                    continue

                created_at = obj.get("created_at", "")
                is_today = created_at.startswith(today)
                is_this_month = created_at.startswith(this_month)
                if not is_today and not is_this_month:
                    continue

                content = obj.get("content", "")
                if not isinstance(content, str):
                    content = json.dumps(content)

                models_in_line = _MODEL_RE.findall(content)
                token_matches = _TOKEN_RE.findall(content)
                token_count = sum(int(t.replace(",", "")) for t in token_matches)
                if token_count == 0:
                    continue

                target_models = list({m.lower() for m in models_in_line}) or ["unknown"]
                for model_raw in target_models:
                    slug = _model_slug(model_raw)
                    entry = totals.setdefault(slug, {"today": 0, "this_month": 0})
                    if is_today:
                        entry["today"] += token_count
                    if is_this_month:
                        entry["this_month"] += token_count
