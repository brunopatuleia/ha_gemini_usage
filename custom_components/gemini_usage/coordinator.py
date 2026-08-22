"""
Data update coordinator for Gemini Usage Monitor.

Architecture
------------
``GeminiUsageCoordinator`` extends HA's ``DataUpdateCoordinator`` to manage
polling across three independent data sources, each with its own refresh rate:

    Source 1 — Gemini REST API rate-limit headers
        ``_fetch_model_list``  builds the list of all available models
            (paginated, cached for ``DEFAULT_MODEL_LIST_INTERVAL`` seconds).
        ``_fetch_rate_limits`` fires a single-token ``generateContent`` POST
            per model and reads the ``x-ratelimit-*`` response headers.
            A semaphore limits concurrency to 3 simultaneous probes so that
            the probing itself does not exhaust rate limits.

    Source 2 — GCP Cloud Monitoring API  (optional)
        ``_fetch_gcp_metrics``  uses a GCP service-account credential
            (obtained in the executor thread via ``google-auth``) to call
            ``monitoring.googleapis.com/v3/.../timeSeries`` for the
            ``serviceruntime`` metric domain and aggregates the last hour
            of input/output token usage per model.  A second call fetches
            the quota limits so ``quota_used_pct`` can be computed.

    Source 3 — Local Antigravity transcript scan  (optional)
        ``_scan_transcripts`` and ``_parse_transcript`` walk the per-
        conversation ``transcript.jsonl`` JSONL files stored on disk.
        Token counts are extracted with a regex that matches the human-
        readable checkpoint text ("Thought for 3s, 286 tokens") and
        attributed to model slugs detected in the same line.  This runs
        in an executor thread to avoid blocking the event loop.

Polling strategy
----------------
The coordinator's ``update_interval`` (set by ``__init__``) is the minimum
of the three source intervals — the highest common factor of when any source
needs to run.  Inside ``_async_update_data``, each source is guarded by a
monotonic-clock comparison against its own last-fetch timestamp, so a fast
source (e.g. 60-second rate-limit probe) does not drag the slow GCP source
(5-minute default) into unnecessary API calls.

Data shape
----------
All fetched data is stored in four internal caches and assembled into a
single ``dict`` by ``_build_data_dict``::

    {
        "models":      list[dict],        # raw model objects from the API
        "rate_limits": dict[slug, dict],  # rate-limit header values per model
        "gcp":         dict[slug, dict],  # Cloud Monitoring aggregates per model
        "local":       dict[slug, dict],  # transcript token tallies per model
    }

Sensor entities receive this dict as ``coordinator.data`` and index into it
using the model slug and sensor-key defined in ``const.SENSOR_DEFS``.

Thread safety
-------------
The blocking operations (GCP token refresh, disk scan) are offloaded to
HA's thread pool via ``hass.async_add_executor_job``.  All cached attributes
(``_models``, ``_rate_limit_data``, etc.) are only written from within the
coordinator's update coroutine, which HA serialises — they are never mutated
from the executor thread directly.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
from datetime import datetime, timezone
from typing import Any

import aiohttp
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
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

# ---------------------------------------------------------------------------
# Module-level compiled regexes
# Compiled once at import time for efficiency — these are applied to every
# line of every transcript file on each local scan.
# ---------------------------------------------------------------------------

_TOKEN_RE = re.compile(r"(\d[\d,]*)\s+tokens?", re.IGNORECASE)
"""Matches strings like ``"286 tokens"``, ``"1,024 token"``, ``"3 Tokens"``.

The leading digit group captures the numeric count (with optional comma
separators for large numbers).  Used to extract token mentions from the
human-readable checkpoint summaries written into transcript.jsonl."""

_MODEL_RE = re.compile(r"gemini[-/][\w.\-]+", re.IGNORECASE)
"""Matches Gemini model name fragments such as ``gemini-2.0-flash``,
``gemini/pro``, or ``Gemini-1.5-flash-exp``.

Intentionally broad so it catches model references in checkpoint text,
tool-call JSON, and conversation content without needing to enumerate every
known model variant."""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _model_slug(name: str) -> str:
    """Convert a Gemini model name into a Home Assistant-safe entity-ID segment.

    HA entity IDs must match ``[a-z0-9_]+``.  This function:

    1. Lower-cases the entire string.
    2. Replaces ``-``, ``.``, ``/``, and ``:`` with ``_``.
    3. Strips the ``models_`` prefix that appears when the API returns a
       fully-qualified resource name such as ``models/gemini-2.0-flash``.

    Examples::

        >>> _model_slug("models/gemini-2.0-flash")
        "gemini_2_0_flash"
        >>> _model_slug("gemini-1.5-pro-exp-0827")
        "gemini_1_5_pro_exp_0827"

    Args:
        name: Raw model name as returned by the Gemini API (may include the
            ``models/`` resource prefix) or as detected in a transcript.

    Returns:
        A lowercase string containing only ``[a-z0-9_]`` characters, suitable
        for use in a HA entity unique ID or entity ID suffix.
    """
    slug = name.lower()
    for ch, repl in {"-": "_", ".": "_", "/": "_", ":": "_"}.items():
        slug = slug.replace(ch, repl)
    # The REST API returns model names as "models/gemini-xxx"; after the
    # slash replacement above this becomes "models_gemini_xxx".
    if slug.startswith("models_"):
        slug = slug[len("models_"):]
    return slug


# ---------------------------------------------------------------------------
# Coordinator
# ---------------------------------------------------------------------------


class GeminiUsageCoordinator(DataUpdateCoordinator):
    """Central coordinator that fetches Gemini usage data from all configured sources.

    Inherits from ``DataUpdateCoordinator`` which handles:
    - Scheduling ``_async_update_data`` on the ``update_interval`` timer.
    - Broadcasting data changes to all registered ``CoordinatorEntity`` listeners.
    - Surfacing update errors to HA's repair/notification system.

    Each data source has its own interval tracked via a monotonic timestamp,
    allowing fine-grained control over polling frequency per source without
    requiring multiple coordinator instances.
    """

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        update_interval,
        rate_limit_interval: int = DEFAULT_RATE_LIMIT_INTERVAL,
        gcp_interval: int = DEFAULT_GCP_INTERVAL,
        local_interval: int = DEFAULT_LOCAL_INTERVAL,
    ) -> None:
        """Initialise the coordinator and extract config from the entry.

        Args:
            hass:                 The Home Assistant instance.
            entry:                The config entry supplying credentials and paths.
            update_interval:      ``timedelta`` passed to the parent coordinator.
                                  Should be the minimum of the three source intervals.
            rate_limit_interval:  Seconds between Gemini API header probes.
            gcp_interval:         Seconds between GCP Cloud Monitoring queries.
            local_interval:       Seconds between local transcript scans.
        """
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=update_interval,
        )
        self._entry = entry

        # Credentials extracted from the config entry at construction time.
        # If the user rotates credentials (options flow), the entry is
        # reloaded and a new coordinator instance is created.
        self._api_key: str = entry.data.get(CONF_API_KEY, "")
        self._gcp_project_id: str = entry.data.get(CONF_GCP_PROJECT_ID, "")
        self._gcp_sa_json_str: str = entry.data.get(CONF_GCP_SERVICE_ACCOUNT_JSON, "")
        self._local_path: str = entry.data.get(CONF_LOCAL_DB_PATH, "")

        # Per-source poll intervals (seconds)
        self._rate_limit_interval = rate_limit_interval
        self._gcp_interval = gcp_interval
        self._local_interval = local_interval

        # Monotonic timestamps of the most recent successful fetch per source.
        # Initialised to 0 so every source runs on the very first update tick.
        self._last_rate_limit_fetch: float = 0.0
        self._last_gcp_fetch: float = 0.0
        self._last_local_fetch: float = 0.0
        self._last_model_list_fetch: float = 0.0

        # In-memory caches for the last successful result from each source.
        # Written only inside _async_update_data (serialised by HA's loop);
        # read from sensor entity properties on the main thread.
        self._models: list[dict] = []
        self._rate_limit_data: dict[str, dict] = {}
        self._gcp_data: dict[str, dict] = {}
        self._local_data: dict[str, dict] = {}

    # ------------------------------------------------------------------
    # Main update entry point (called by DataUpdateCoordinator on timer)
    # ------------------------------------------------------------------

    async def _async_update_data(self) -> dict[str, Any]:
        """Coordinate all data sources and return the merged data dict.

        Called by the parent ``DataUpdateCoordinator`` on every tick of
        ``update_interval``.  Each source is guarded by an elapsed-time check
        so it only runs when its own interval has passed.

        Sources are launched as concurrent tasks (``asyncio.gather``) so a
        slow network call to one source does not block the others.  Exceptions
        from individual sources are captured by ``return_exceptions=True`` and
        logged at DEBUG level; stale cached data from the previous cycle is
        returned instead so sensors remain available.

        Returns:
            The merged data dict produced by ``_build_data_dict``.
        """
        now = time.monotonic()

        # Model list is refreshed independently at a fixed hourly cadence.
        # It must run before _fetch_rate_limits (which needs the model list)
        # so it is awaited here rather than gathered with the other tasks.
        if now - self._last_model_list_fetch >= DEFAULT_MODEL_LIST_INTERVAL:
            await self._fetch_model_list()
            self._last_model_list_fetch = now

        # Build the list of source-fetch coroutines that are due to run.
        tasks = []

        if now - self._last_rate_limit_fetch >= self._rate_limit_interval:
            tasks.append(self._fetch_rate_limits())

        # Only schedule GCP fetch if credentials are present.
        if self._gcp_project_id and self._gcp_sa_json_str and (
            now - self._last_gcp_fetch >= self._gcp_interval
        ):
            tasks.append(self._fetch_gcp_metrics())

        # Only schedule local scan if a brain path is configured.
        if self._local_path and (now - self._last_local_fetch >= self._local_interval):
            tasks.append(self._fetch_local_usage())

        if tasks:
            # return_exceptions=True ensures one failing source doesn't cancel others.
            await asyncio.gather(*tasks, return_exceptions=True)

        return self._build_data_dict()

    def _build_data_dict(self) -> dict[str, Any]:
        """Assemble the four caches into the single dict consumed by sensor entities.

        Returns:
            Dict with keys ``"models"``, ``"rate_limits"``, ``"gcp"``, ``"local"``.
            Sensor entities access their data via ``coordinator.data[<key>][<slug>]``.
        """
        return {
            "models": self._models,
            "rate_limits": self._rate_limit_data,
            "gcp": self._gcp_data,
            "local": self._local_data,
        }

    # ------------------------------------------------------------------
    # Source 0: Model list (shared prerequisite for other sources)
    # ------------------------------------------------------------------

    async def _fetch_model_list(self) -> None:
        """Fetch the complete list of Gemini models available to this API key.

        Handles pagination automatically using the ``nextPageToken`` field.
        Each page requests up to 50 models.  The full list is written to
        ``self._models`` atomically only after all pages are fetched so
        sensors never see a partial list.

        On failure (network error or non-200 response) the existing cached
        list is preserved and a warning is logged.  This means sensor entities
        based on previously-discovered models remain available.
        """
        models: list[dict] = []
        page_token = ""
        url_base = f"{GEMINI_MODELS_ENDPOINT}?key={self._api_key}&pageSize=50"
        try:
            async with aiohttp.ClientSession() as session:
                while True:
                    # Append the page token only after the first page.
                    url = url_base + (f"&pageToken={page_token}" if page_token else "")
                    async with session.get(url, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                        if resp.status != 200:
                            body = await resp.text()
                            _LOGGER.warning(
                                "Failed to fetch model list: HTTP %s — %s",
                                resp.status, body[:200]
                            )
                            return  # Keep existing _models cache intact
                        data = await resp.json()
                        models.extend(data.get("models", []))
                        page_token = data.get("nextPageToken", "")
                        if not page_token:
                            break  # No more pages
            # Atomic update: only replace once all pages are collected.
            self._models = models
            _LOGGER.debug("Fetched %d Gemini models", len(models))
        except aiohttp.ClientError as exc:
            _LOGGER.warning("Error fetching model list: %s", exc)

    # ------------------------------------------------------------------
    # Source 1: Rate-limit headers (per model)
    # ------------------------------------------------------------------

    async def _fetch_rate_limits(self) -> None:
        """Probe each generateContent-capable model and collect rate-limit headers.

        Design decisions
        ~~~~~~~~~~~~~~~~
        **Why probe at all?**  The Gemini API does not expose a dedicated
        "remaining quota" endpoint.  Rate-limit data is only available in the
        response headers of an actual API call, so a minimal ``generateContent``
        request with ``maxOutputTokens=1`` is the only way to retrieve it.

        **Which models are probed?**  Only models that support
        ``generateContent`` (as declared in ``supportedGenerationMethods``).
        Embedding and AQA models do not expose rate-limit headers so they are
        filtered out.  If the filter returns an empty list (e.g. the API
        returns methods in an unexpected format) all models are probed as a
        fallback.

        **Concurrency limiting**  A semaphore (max 3) prevents the integration
        from blasting every model simultaneously and consuming its own RPM
        quota in the process.

        **Error isolation**  If one model probe fails, the others continue.
        The failing model's entry in ``new_data`` records the error string
        rather than rate-limit values; sensors for that model return
        ``None`` (unavailable) until the next successful probe.
        """
        # Ensure the model list is populated before we try to probe.
        if not self._models:
            await self._fetch_model_list()

        # Filter to models that can generate content.  The API may return
        # supportedGenerationMethods as a list of strings OR list of dicts
        # depending on the API version, so both formats are handled.
        probe_models = [
            m for m in self._models
            if any(
                "generateContent" in method.get("name", "")
                for method in m.get("supportedGenerationMethods", [])
                if isinstance(method, dict)
            )
            or any(
                "generateContent" in method
                for method in m.get("supportedGenerationMethods", [])
                if isinstance(method, str)
            )
        ]

        if not probe_models:
            # Fallback: probe everything (better than producing no data).
            probe_models = self._models

        new_data: dict[str, dict] = {}

        # Limit to 3 concurrent probes to avoid self-inflicted rate limiting.
        semaphore = asyncio.Semaphore(3)

        async def probe_one(model_info: dict) -> None:
            """Send a minimal generateContent request and harvest headers.

            Args:
                model_info: A single model object from the ``/models`` list.
            """
            model_name = model_info.get("name", "")
            if not model_name:
                return
            slug = _model_slug(model_name)

            # Full resource path is already part of the model name
            # (e.g. "models/gemini-2.0-flash"), so we embed it directly.
            url = f"{GEMINI_API_BASE}/{model_name}:generateContent?key={self._api_key}"
            body = {
                # Single-word prompt — smallest possible valid request.
                "contents": [{"parts": [{"text": "hi"}]}],
                # Cap output at 1 token to minimise cost and latency.
                "generationConfig": {"maxOutputTokens": 1},
            }

            async with semaphore:
                try:
                    async with aiohttp.ClientSession() as session:
                        async with session.post(
                            url,
                            json=body,
                            timeout=aiohttp.ClientTimeout(total=15),
                        ) as resp:
                            headers = dict(resp.headers)

                            def h(key: str) -> int | None:
                                """Extract an integer rate-limit header value.

                                The Gemini API may send headers in any
                                casing; check both the original key and its
                                lower-cased variant.

                                Args:
                                    key: Header name (case-insensitive).

                                Returns:
                                    Integer value, or ``None`` if the header
                                    is absent or non-numeric.
                                """
                                val = headers.get(key) or headers.get(key.lower())
                                try:
                                    return int(val) if val is not None else None
                                except (ValueError, TypeError):
                                    return None

                            new_data[slug] = {
                                "model_name": model_name,
                                "display_name": model_info.get("displayName", model_name),
                                # Context window sizes from the model list — useful
                                # for dashboard cards but not a rate-limit metric.
                                "input_token_limit": model_info.get("inputTokenLimit"),
                                "output_token_limit": model_info.get("outputTokenLimit"),
                                # Sliding-window (per-minute) counters
                                "rpm_remaining": h("x-ratelimit-remaining-requests"),
                                "rpm_limit":     h("x-ratelimit-limit-requests"),
                                "tpm_remaining": h("x-ratelimit-remaining-tokens"),
                                "tpm_limit":     h("x-ratelimit-limit-tokens"),
                                # Daily (fixed 24-hour window) counters
                                "rpd_remaining": h("x-ratelimit-remaining-requests-day"),
                                "rpd_limit":     h("x-ratelimit-limit-requests-day"),
                                # HTTP status is stored for debugging via attributes.
                                "http_status": resp.status,
                            }

                except aiohttp.ClientError as exc:
                    _LOGGER.debug("Rate-limit probe failed for %s: %s", model_name, exc)
                    # Store a partial record so the sensor knows this model
                    # exists but is currently unreachable.
                    new_data[slug] = {
                        "model_name": model_name,
                        "display_name": model_info.get("displayName", model_name),
                        "error": str(exc),
                    }

        # Fan out all probes concurrently (semaphore limits to 3 at a time).
        await asyncio.gather(*[probe_one(m) for m in probe_models])
        self._rate_limit_data = new_data
        self._last_rate_limit_fetch = time.monotonic()
        _LOGGER.debug("Rate-limit data fetched for %d models", len(new_data))

    # ------------------------------------------------------------------
    # Source 2: GCP Cloud Monitoring
    # ------------------------------------------------------------------

    async def _fetch_gcp_metrics(self) -> None:
        """Query GCP Cloud Monitoring for quota usage and limits per model.

        Flow
        ~~~~
        1. Parse the stored service account JSON.
        2. Obtain a short-lived OAuth2 bearer token in an executor thread
           (the ``google-auth`` library is synchronous).
        3. Issue a ``timeSeries.list`` request filtered to Gemini usage metrics
           for the last hour, aggregated into a single 3600-second bucket.
        4. Parse the response: each time-series represents one
           (model, quota_metric) combination.  Token counts are summed across
           all data points in the window.
        5. Issue a second request for quota limits.  Combine with the usage
           totals to compute ``quota_used_pct``.

        Data attribution
        ~~~~~~~~~~~~~~~~
        The Cloud Monitoring API returns ``resource.labels.model`` and
        ``metric.labels.quota_metric`` to identify each series.  The
        ``quota_metric`` label contains strings like ``"generate-requests-per-
        minute-per-model-gemini-2.0-flash"`` — "input" / "output" substrings
        are used to classify tokens into the right bucket.

        GCP availability
        ~~~~~~~~~~~~~~~~
        If the service account JSON is missing or invalid, this method returns
        immediately without updating ``_gcp_data``.  The existing cache (empty
        on first run) is kept; GCP-sourced sensors show as unavailable.
        """
        # --- Step 1: Parse service account JSON ---
        try:
            sa_info = json.loads(self._gcp_sa_json_str)
        except json.JSONDecodeError:
            _LOGGER.error("Invalid service account JSON — skipping GCP metrics")
            return

        # --- Step 2: Obtain bearer token (synchronous library → executor) ---
        try:
            token = await self.hass.async_add_executor_job(
                self._get_gcp_access_token, sa_info
            )
        except Exception as exc:  # pylint: disable=broad-except
            _LOGGER.warning("Failed to get GCP access token: %s", exc)
            return

        # --- Step 3: Build query parameters ---
        now_iso = datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        one_hour_ago = datetime.fromtimestamp(
            time.time() - 3600, tz=timezone.utc
        ).strftime("%Y-%m-%dT%H:%M:%SZ")

        params = {
            # Filter to Gemini quota net-usage metrics only.
            "filter": (
                f'metric.type="{METRIC_QUOTA_RATE_USAGE}"'
                f' AND resource.labels.service="{GEMINI_GCP_SERVICE}"'
            ),
            "interval.startTime": one_hour_ago,
            "interval.endTime": now_iso,
            # Collapse the entire hour into a single data point per series.
            "aggregation.alignmentPeriod": "3600s",
            "aggregation.perSeriesAligner": "ALIGN_SUM",
        }

        url = f"{GCP_MONITORING_BASE}/projects/{self._gcp_project_id}/timeSeries"
        headers = {"Authorization": f"Bearer {token}"}

        # --- Step 4: Fetch usage time-series ---
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    url,
                    params=params,
                    headers=headers,
                    timeout=aiohttp.ClientTimeout(total=20),
                ) as resp:
                    if resp.status != 200:
                        body = await resp.text()
                        _LOGGER.warning(
                            "GCP Monitoring returned %s: %s", resp.status, body[:300]
                        )
                        return
                    data = await resp.json()
        except aiohttp.ClientError as exc:
            _LOGGER.warning("GCP Monitoring request failed: %s", exc)
            return

        new_data: dict[str, dict] = {}

        for series in data.get("timeSeries", []):
            metric_labels = series.get("metric", {}).get("labels", {})
            resource_labels = series.get("resource", {}).get("labels", {})
            quota_metric = metric_labels.get("quota_metric", "")

            # Model name may appear in either resource or metric labels
            # depending on the quota dimension.
            model = resource_labels.get("model", "") or metric_labels.get("model", "")
            if not model:
                continue
            slug = _model_slug(model)

            # Sum all data points in the 1-hour window.
            points = series.get("points", [])
            total_usage = sum(
                int(p.get("value", {}).get("int64Value", 0) or 0)
                for p in points
            )

            # Use setdefault so multiple series for the same model accumulate
            # into the same dict without overwriting each other.
            entry = new_data.setdefault(slug, {"model": model})

            # Classify by quota_metric label substring.
            if "input" in quota_metric:
                entry["tokens_input_1h"] = total_usage
            elif "output" in quota_metric:
                entry["tokens_output_1h"] = total_usage
            else:
                # Catch-all for undifferentiated usage metrics.
                entry["usage_1h"] = total_usage

        # --- Step 5: Fetch quota limits and compute usage % ---
        params_limit = dict(params)
        params_limit["filter"] = (
            f'metric.type="{METRIC_QUOTA_LIMIT}"'
            f' AND resource.labels.service="{GEMINI_GCP_SERVICE}"'
        )
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    url,
                    params=params_limit,
                    headers=headers,
                    timeout=aiohttp.ClientTimeout(total=20),
                ) as resp:
                    if resp.status == 200:
                        limit_data = await resp.json()
                        for series in limit_data.get("timeSeries", []):
                            resource_labels = series.get("resource", {}).get("labels", {})
                            model = resource_labels.get("model", "")
                            if not model:
                                continue
                            slug = _model_slug(model)
                            points = series.get("points", [])
                            if points:
                                # Quota limits are static; the first point is sufficient.
                                limit_val = int(
                                    points[0].get("value", {}).get("int64Value", 0) or 0
                                )
                                entry = new_data.setdefault(slug, {"model": model})
                                entry["quota_limit"] = limit_val
                                # Compute combined token usage as percentage of limit.
                                usage = (
                                    entry.get("tokens_input_1h", 0)
                                    + entry.get("tokens_output_1h", 0)
                                )
                                if limit_val > 0:
                                    entry["quota_used_pct"] = round(
                                        usage / limit_val * 100, 1
                                    )
        except aiohttp.ClientError as exc:
            # Non-fatal: usage metrics already populated; limits are optional.
            _LOGGER.debug("Failed to fetch GCP quota limits: %s", exc)

        self._gcp_data = new_data
        self._last_gcp_fetch = time.monotonic()
        _LOGGER.debug("GCP metrics fetched for %d model series", len(new_data))

    def _get_gcp_access_token(self, sa_info: dict) -> str:
        """Exchange a service account JSON for a short-lived OAuth2 bearer token.

        This method is **synchronous** and must be called from an executor
        thread (via ``hass.async_add_executor_job``) because ``google-auth``
        performs blocking I/O when refreshing credentials.

        The resulting token is valid for approximately 1 hour.  The
        coordinator does not cache it — a new token is obtained on each GCP
        poll cycle.  This is intentional: token caching would require
        thread-safe expiry logic, and the overhead of a token refresh is
        negligible compared to the 5-minute poll interval.

        Args:
            sa_info: Parsed service account JSON dict (from
                ``json.loads(self._gcp_sa_json_str)``).

        Returns:
            A string bearer token suitable for use in an ``Authorization``
            header.

        Raises:
            RuntimeError: If the ``google-auth`` package is not installed.
            google.auth.exceptions.TransportError: On network failures.
            google.auth.exceptions.GoogleAuthError: On credential errors.
        """
        try:
            # Import here (not at module top) because google-auth is an optional
            # dependency — the integration works without it when GCP is not configured.
            import google.auth.transport.requests as ga_requests
            import google.oauth2.service_account as sa_module

            credentials = sa_module.Credentials.from_service_account_info(
                sa_info, scopes=GCP_MONITORING_SCOPES
            )
            request = ga_requests.Request()
            # refresh() populates credentials.token with the bearer value.
            credentials.refresh(request)
            return credentials.token
        except ImportError:
            raise RuntimeError(
                "google-auth is not installed. "
                "Add it to the integration requirements."
            )

    # ------------------------------------------------------------------
    # Source 3: Local Antigravity transcript scanning
    # ------------------------------------------------------------------

    async def _fetch_local_usage(self) -> None:
        """Offload the blocking transcript scan to a thread and cache the result.

        The scan reads files from disk, which is blocking I/O.  Running it
        in the executor avoids stalling HA's event loop.
        """
        result = await self.hass.async_add_executor_job(self._scan_transcripts)
        self._local_data = result
        self._last_local_fetch = time.monotonic()

    def _scan_transcripts(self) -> dict[str, dict]:
        """Walk the brain directory and tally token counts across all conversations.

        Directory layout expected::

            <brain_path>/
                <conversation-uuid>/
                    .system_generated/
                        logs/
                            transcript.jsonl   ← parsed by _parse_transcript

        Conversations whose ``transcript.jsonl`` does not exist (e.g. very
        short conversations) are silently skipped.  Parse errors for any
        individual file are caught and logged at DEBUG level so one corrupt
        file does not abort the whole scan.

        Returns:
            A dict mapping model slugs to ``{"today": int, "this_month": int}``.
            Returns an empty dict if the brain path is not configured or not
            found on disk.
        """
        brain_path = self._local_path
        if not brain_path or not os.path.isdir(brain_path):
            return {}

        # Compute date strings once per scan so all files are compared against
        # the same reference point (avoids midnight edge-cases within a scan).
        today = datetime.now().strftime("%Y-%m-%d")
        this_month = datetime.now().strftime("%Y-%m")

        # Mutable accumulator: slug → {today: int, this_month: int}
        totals: dict[str, dict[str, int]] = {}

        for conv_dir in os.scandir(brain_path):
            if not conv_dir.is_dir():
                continue  # Skip regular files (e.g. metadata files)
            logs_dir = os.path.join(conv_dir.path, ".system_generated", "logs")
            transcript = os.path.join(logs_dir, "transcript.jsonl")
            if not os.path.isfile(transcript):
                continue  # Conversation has no transcript yet

            try:
                self._parse_transcript(transcript, today, this_month, totals)
            except Exception as exc:  # pylint: disable=broad-except
                _LOGGER.debug("Error parsing transcript %s: %s", transcript, exc)

        return totals

    def _parse_transcript(
        self,
        transcript_path: str,
        today: str,
        this_month: str,
        totals: dict,
    ) -> None:
        """Parse a single transcript JSONL file and accumulate token counts.

        Each line of the file is a JSON object representing one agent step.
        This method:

        1. Skips lines whose ``created_at`` timestamp does not fall within
           today or the current month (no point counting old usage here).
        2. Serialises non-string ``content`` fields to JSON so the regexes
           can match across tool-call arguments and other structured data.
        3. Runs ``_TOKEN_RE`` to extract all numeric token mentions from the
           content string.
        4. Runs ``_MODEL_RE`` to find any Gemini model name referenced in the
           same line and attributes the token count to that model slug.
           Falls back to ``"unknown"`` when no model is mentioned.

        **Counting accuracy**  Token counts in transcripts come from checkpoint
        summary text ("Thought for 3s, 286 tokens") and are an approximation.
        The same tokens may be mentioned multiple times in a heavily-summarised
        checkpoint, leading to over-counting.  These sensors are labelled
        "(Local)" to indicate they are estimates.

        Args:
            transcript_path: Absolute path to the ``transcript.jsonl`` file.
            today:           ISO date string ``"YYYY-MM-DD"`` for today.
            this_month:      ISO year-month string ``"YYYY-MM"`` for this month.
            totals:          Mutable accumulator dict modified in-place.
        """
        with open(transcript_path, "r", encoding="utf-8", errors="ignore") as fh:
            for raw_line in fh:
                raw_line = raw_line.strip()
                if not raw_line:
                    continue

                try:
                    obj = json.loads(raw_line)
                except json.JSONDecodeError:
                    continue  # Malformed line — skip silently

                created_at = obj.get("created_at", "")

                # Cheap prefix checks: comparing ISO strings is faster than
                # parsing datetime objects for every line.
                is_today = created_at.startswith(today)
                is_this_month = created_at.startswith(this_month)

                if not is_today and not is_this_month:
                    continue  # Outside the relevant date window

                # Flatten structured content to a searchable string.
                content = obj.get("content", "")
                if not isinstance(content, str):
                    content = json.dumps(content)

                # Identify which models are referenced in this log line.
                models_in_line = _MODEL_RE.findall(content)

                # Sum all numeric token mentions in the line.
                token_matches = _TOKEN_RE.findall(content)
                token_count = sum(
                    int(t.replace(",", "")) for t in token_matches
                )

                if token_count == 0:
                    continue  # Nothing to attribute

                # Deduplicate model references (a model might appear multiple
                # times in one line) and use "unknown" as a fallback bucket.
                target_models = list({m.lower() for m in models_in_line}) or ["unknown"]

                for model_raw in target_models:
                    slug = _model_slug(model_raw)
                    # setdefault avoids a separate existence check each iteration.
                    entry = totals.setdefault(slug, {"today": 0, "this_month": 0})
                    if is_today:
                        entry["today"] += token_count
                    if is_this_month:
                        entry["this_month"] += token_count
