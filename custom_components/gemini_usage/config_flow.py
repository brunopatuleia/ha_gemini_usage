"""
Config flow for Gemini Usage Monitor.

This module implements the UI-driven setup and options flow that Home Assistant
displays when a user adds or configures the integration.

Setup flow (``GeminiUsageConfigFlow``)
--------------------------------------
The flow is split into three sequential steps to keep each screen focused:

    Step 1 — ``async_step_user`` (required)
        Collects the Gemini API key and validates it immediately by making a
        live ``GET /v1beta/models`` call.  The flow will not advance until the
        key is accepted by Google's API, preventing silent misconfiguration.

    Step 2 — ``async_step_gcp`` (optional)
        Collects the GCP project ID and service account JSON.  Both fields are
        optional — leaving them blank disables the Cloud Monitoring sensors.
        If a JSON string is provided its syntax is checked before advancing;
        credential validity is not verified here (that would require a network
        call that is deferred to the coordinator).

    Step 3 — ``async_step_local`` (optional)
        Collects the path to the Antigravity brain directory for local token
        tracking.  The directory is auto-detected from common Windows paths.
        Leaving the field blank disables local sensors.

Options flow (``GeminiUsageOptionsFlow``)
-----------------------------------------
Allows all fields to be updated post-setup without removing and re-adding the
integration.  Saving the form triggers ``async_reload_entry`` in ``__init__.py``
so new values take effect immediately.

Error handling
--------------
Every user-facing error is mapped to a string key defined in ``strings.json``
and its ``translations/en.json`` mirror.  This keeps all user-visible text in
one place and makes localisation straightforward.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
from typing import Any

import aiohttp
import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import HomeAssistant, callback
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import (
    CONF_API_KEY,
    CONF_DISPLAY_MODE,
    CONF_GCP_INTERVAL,
    CONF_GCP_PROJECT_ID,
    CONF_GCP_SERVICE_ACCOUNT_JSON,
    CONF_LOCAL_DB_PATH,
    CONF_LOCAL_INTERVAL,
    CONF_RATE_LIMIT_INTERVAL,
    DEFAULT_DISPLAY_MODE,
    DEFAULT_GCP_INTERVAL,
    DEFAULT_LOCAL_INTERVAL,
    DEFAULT_RATE_LIMIT_INTERVAL,
    DISPLAY_MODE_REMAINING,
    DISPLAY_MODE_USED,
    DOMAIN,
    GEMINI_MODELS_ENDPOINT,
)


_LOGGER = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _default_local_paths() -> list[str]:
    """Return a list of Antigravity brain directories that exist on this machine.

    Expands the standard Windows environment-variable templates defined in
    ``const.ANTIGRAVITY_BRAIN_PATH_TEMPLATES`` and filters to those that are
    present on disk.  The result is ordered (``%USERPROFILE%`` checked first)
    so the first element is always the most-likely correct path.

    Returns:
        A (possibly empty) list of absolute directory paths.
    """
    templates = [
        r"%USERPROFILE%\.gemini\antigravity\brain",
        r"%APPDATA%\.gemini\antigravity\brain",
    ]
    found = []
    for tmpl in templates:
        expanded = os.path.expandvars(tmpl)
        if os.path.isdir(expanded):
            found.append(expanded)
    return found


async def _validate_api_key(api_key: str) -> tuple[bool, str]:
    """Verify a Gemini API key by calling the models list endpoint.

    Makes a live network request to ``GET /v1beta/models?pageSize=1`` which
    is the cheapest call available — it returns one model record and costs no
    quota.  A ``200 OK`` response indicates the key is valid.

    Args:
        api_key: The raw API key string entered by the user.

    Returns:
        A ``(success, error_message)`` tuple.  ``error_message`` is an empty
        string when ``success`` is ``True``.
    """
    # pageSize=1 minimises response payload — we only need the HTTP status.
    url = f"{GEMINI_MODELS_ENDPOINT}?pageSize=1"
    headers = {"x-goog-api-key": api_key}
    try:
        session = async_get_clientsession(hass)
        async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=10)) as resp:
            if resp.status == 200:
                return True, ""
            # Surface the first 200 chars of the error body to the log;
            # the UI shows a generic error string from strings.json.
            body = await resp.text()
            return False, f"HTTP {resp.status}: {body[:200]}"
    except aiohttp.ClientError as exc:
        return False, str(exc)


# ---------------------------------------------------------------------------
# Config flow
# ---------------------------------------------------------------------------


class GeminiUsageConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a multi-step config flow for Gemini Usage Monitor.

    HA instantiates this class when the user clicks "Add integration" and
    selects "Gemini Usage Monitor".  The ``domain`` class argument ties the
    flow to the integration and is used by HA to look up the matching
    ``strings.json`` keys.

    Instance attributes collect data across the three steps; ``_create_entry``
    combines them into the final ``ConfigEntry.data`` dict.
    """

    VERSION = 1
    """Schema version for the config entry.  Increment when adding required
    fields so that HA can run ``async_migrate_entry`` on older entries."""

    def __init__(self) -> None:
        """Initialise inter-step state containers.

        These are set by each step and read by ``_create_entry`` at the end of
        the flow.  Starting them as empty strings avoids ``None`` type-checks
        downstream and matches the default values shown in the options flow.
        """
        self._api_key: str = ""
        self._gcp_project_id: str = ""
        self._gcp_sa_json: str = ""
        self._local_path: str = ""

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Step 1: collect and validate the Gemini API key.

        This is the entry point for all user-initiated flows.  The step name
        ``"user"`` is the HA convention for the initial step.

        Validation is performed synchronously within the flow (not deferred to
        coordinator startup) so that users receive immediate feedback on an
        invalid key rather than discovering the problem after setup completes.

        Args:
            user_input: ``None`` on the first call (show the form); populated
                dict on subsequent calls (user submitted the form).

        Returns:
            A ``FlowResult`` — either ``show_form`` to re-display the form
            with errors, or the result of the next step.
        """
        errors: dict[str, str] = {}

        if user_input is not None:
            api_key = user_input.get(CONF_API_KEY, "").strip()

            # Prevent duplicate instances of identical configuration
            unique_id = hashlib.sha256(api_key.encode()).hexdigest()[:16] if api_key else "antigravity_local"
            await self.async_set_unique_id(unique_id)
            self._abort_if_unique_id_configured()

            if not api_key:
                # Key is optional: users can proceed with Antigravity compute quotas only
                self._api_key = ""
                return await self.async_step_gcp()

            valid, error_msg = await _validate_api_key(self.hass, api_key)
            if valid:
                self._api_key = api_key
                # Advance to the (optional) GCP step.
                return await self.async_step_gcp()
            # Map the failure to the generic "base" error bucket so HA
            # displays it below the form header.
            errors["base"] = "invalid_api_key"
            _LOGGER.error("API key validation failed: %s", error_msg)

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Optional(CONF_API_KEY, default=""): str,
                }
            ),

            errors=errors,
            # Injected into the step description template in strings.json
            # so the UI can display a clickable link to AI Studio.
            description_placeholders={
                "docs_url": "https://aistudio.google.com/app/apikey"
            },
        )

    async def async_step_gcp(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Step 2: collect optional GCP credentials for Cloud Monitoring.

        Both fields are ``vol.Optional`` so the user can skip this step by
        leaving them blank.  If a service account JSON is provided, its
        syntax is validated here; credential validity (IAM permissions, etc.)
        is checked lazily during the first coordinator refresh.

        Args:
            user_input: ``None`` on first call; submitted form data thereafter.

        Returns:
            A ``FlowResult`` advancing to step 3 on success, or re-showing
            the form with an error if the JSON is malformed.
        """
        errors: dict[str, str] = {}

        if user_input is not None:
            project_id = user_input.get(CONF_GCP_PROJECT_ID, "").strip()
            sa_json = user_input.get(CONF_GCP_SERVICE_ACCOUNT_JSON, "").strip()

            # Only validate JSON syntax if the user actually provided something.
            # An empty string means "skip GCP" and is always accepted.
            if sa_json:
                try:
                    json.loads(sa_json)
                except json.JSONDecodeError:
                    errors["base"] = "invalid_sa_json"

            if not errors:
                self._gcp_project_id = project_id
                self._gcp_sa_json = sa_json
                return await self.async_step_local()

        return self.async_show_form(
            step_id="gcp",
            data_schema=vol.Schema(
                {
                    vol.Optional(CONF_GCP_PROJECT_ID, default=""): str,
                    vol.Optional(CONF_GCP_SERVICE_ACCOUNT_JSON, default=""): str,
                }
            ),
            errors=errors,
            description_placeholders={
                "docs_url": "https://console.cloud.google.com/iam-admin/serviceaccounts"
            },
        )

    async def async_step_local(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Step 3: collect the optional local Antigravity brain directory.

        Auto-detects the default path at the time this step is rendered so
        the field is pre-filled for most users who use Antigravity with its
        default install location.

        Validation: if a path is entered it must already exist on disk.  An
        empty value is accepted and disables local tracking.

        Args:
            user_input: ``None`` on first call; submitted form data thereafter.

        Returns:
            A ``FlowResult`` creating the entry on success, or re-showing the
            form with an error if the directory does not exist.
        """
        errors: dict[str, str] = {}
        # Detect at render time so a freshly-installed Antigravity is found.
        auto_paths = _default_local_paths()
        default_path = auto_paths[0] if auto_paths else ""

        if user_input is not None:
            local_path = user_input.get(CONF_LOCAL_DB_PATH, "").strip()
            # Only validate if the user entered something — blank = disabled.
            if local_path and not os.path.isdir(local_path):
                errors["base"] = "invalid_local_path"

            if not errors:
                self._local_path = local_path
                return self._create_entry()

        return self.async_show_form(
            step_id="local",
            data_schema=vol.Schema(
                {
                    vol.Optional(CONF_LOCAL_DB_PATH, default=default_path): str,
                }
            ),
            errors=errors,
            description_placeholders={
                # Shown in the step description so users know what was found.
                "detected": default_path or "None detected",
            },
        )

    def _create_entry(self) -> FlowResult:
        """Finalise the flow by creating the config entry.

        Collects all data gathered across the three steps into a single dict
        and passes it to ``async_create_entry``.  HA persists this dict in
        ``.storage/core.config_entries`` and makes it available as
        ``ConfigEntry.data`` throughout the integration's lifetime.

        Returns:
            A ``FlowResult`` that triggers HA to call ``async_setup_entry``.
        """
        return self.async_create_entry(
            title="Gemini Usage Monitor",
            data={
                CONF_API_KEY: self._api_key,
                CONF_GCP_PROJECT_ID: self._gcp_project_id,
                CONF_GCP_SERVICE_ACCOUNT_JSON: self._gcp_sa_json,
                CONF_LOCAL_DB_PATH: self._local_path,
            },
        )

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> "GeminiUsageOptionsFlow":
        """Return the options flow handler for this config entry.

        HA calls this static method when the user opens the integration's
        "Configure" dialog.  The ``@callback`` decorator marks it as a
        synchronous method safe to call from the event loop.

        Args:
            config_entry: The existing config entry whose options are being
                edited.

        Returns:
            An initialised ``GeminiUsageOptionsFlow`` instance.
        """
        return GeminiUsageOptionsFlow(config_entry)


# ---------------------------------------------------------------------------
# Options flow
# ---------------------------------------------------------------------------


class GeminiUsageOptionsFlow(config_entries.OptionsFlow):
    """Handle post-setup options for Gemini Usage Monitor.

    Presents a single form that lets the user update:
    - API key and GCP credentials (credential rotation without removal)
    - Local transcript directory path
    - Individual poll intervals for each data source

    On submit, HA stores the new values in ``ConfigEntry.options`` and calls
    ``async_reload_entry``, which triggers a full integration reload.
    """

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        """Store a reference to the existing config entry.

        Args:
            config_entry: The entry whose options are being edited.  Used to
                pre-fill the form with current values.
        """
        self._config_entry = config_entry

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Present the options form and process the submission.

        Because all changes trigger a full reload (via the listener registered
        in ``__init__.async_setup_entry``), a single consolidated form is
        simpler than splitting options into sub-steps.

        Interval constraints:
        - Rate-limit probe: 30 – 3 600 s  (min avoids spamming the API)
        - GCP Monitoring:   60 – 3 600 s  (Cloud Monitoring data is 60-s granular)
        - Local scan:       30 – 3 600 s

        Args:
            user_input: ``None`` on first call; submitted form data on save.

        Returns:
            A ``FlowResult`` — either ``create_entry`` (save & reload) or
            ``show_form`` to display the pre-filled form.
        """
        if user_input is not None:
            # Persist new options; HA calls async_reload_entry automatically.
            return self.async_create_entry(title="", data=user_input)

        # Pre-fill from current options, falling back to the integration
        # defaults so the form always shows meaningful values.
        current = self._config_entry.options

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    # --- Quota Display Mode ---

                    vol.Optional(
                        CONF_DISPLAY_MODE,
                        default=current.get(
                            CONF_DISPLAY_MODE,
                            self._config_entry.data.get(CONF_DISPLAY_MODE, DEFAULT_DISPLAY_MODE),
                        ),
                    ): vol.In(
                        {
                            DISPLAY_MODE_USED: "Increasing (0% → 100% Used)",
                            DISPLAY_MODE_REMAINING: "Decreasing (100% → 0% Remaining)",
                        }
                    ),
                    # --- Credentials (can be rotated without re-setup) ---
                    vol.Optional(
                        CONF_API_KEY,
                        default=self._config_entry.data.get(CONF_API_KEY, ""),
                    ): str,
                    vol.Optional(
                        CONF_GCP_PROJECT_ID,
                        default=self._config_entry.data.get(CONF_GCP_PROJECT_ID, ""),
                    ): str,
                    vol.Optional(
                        CONF_GCP_SERVICE_ACCOUNT_JSON,
                        default=self._config_entry.data.get(CONF_GCP_SERVICE_ACCOUNT_JSON, ""),
                    ): str,
                    vol.Optional(
                        CONF_LOCAL_DB_PATH,
                        default=self._config_entry.data.get(CONF_LOCAL_DB_PATH, ""),
                    ): str,
                    # --- Poll intervals ---
                    vol.Optional(
                        CONF_RATE_LIMIT_INTERVAL,
                        default=current.get(CONF_RATE_LIMIT_INTERVAL, DEFAULT_RATE_LIMIT_INTERVAL),
                    ): vol.All(int, vol.Range(min=30, max=3600)),
                    vol.Optional(
                        CONF_GCP_INTERVAL,
                        default=current.get(CONF_GCP_INTERVAL, DEFAULT_GCP_INTERVAL),
                    ): vol.All(int, vol.Range(min=60, max=3600)),
                    vol.Optional(
                        CONF_LOCAL_INTERVAL,
                        default=current.get(CONF_LOCAL_INTERVAL, DEFAULT_LOCAL_INTERVAL),
                    ): vol.All(int, vol.Range(min=30, max=3600)),
                }
            ),
        )

