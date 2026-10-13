"""
Gemini Usage Monitor — Home Assistant custom integration entry point.

This module is loaded by Home Assistant when the integration is first set up
and whenever a config entry is loaded at startup.  Its responsibilities are:

1. **Read poll intervals** from ``ConfigEntry.options`` (preferred) or
   ``ConfigEntry.data`` (set during initial config flow), falling back to the
   defaults defined in ``const.py``.

2. **Instantiate the coordinator** (``GeminiUsageCoordinator``) with the
   resolved intervals and perform the first data refresh.  HA waits for this
   refresh to complete before marking the integration as ready; if it fails
   the integration will be put into a retry loop rather than crashing.

3. **Store the coordinator** in ``hass.data[DOMAIN][entry.entry_id]`` so that
   platform modules (``sensor.py``) can retrieve it without circular imports.

4. **Forward platform setup** to the ``sensor`` platform, which registers all
   sensor entities.

5. **Register an options listener** so that changes made via the UI options
   flow (changing a poll interval, rotating an API key, etc.) automatically
   trigger a full reload of the integration — ensuring the new settings take
   effect immediately without a full HA restart.

Lifecycle
---------
- ``async_setup_entry``   called on startup / after options change
- ``async_unload_entry``  called on removal / before reload
- ``async_reload_entry``  registered as the options-change listener;
                          calls unload then setup
"""
from __future__ import annotations

import logging
from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import (
    CONF_GCP_INTERVAL,
    CONF_LOCAL_INTERVAL,
    CONF_RATE_LIMIT_INTERVAL,
    DEFAULT_GCP_INTERVAL,
    DEFAULT_LOCAL_INTERVAL,
    DEFAULT_RATE_LIMIT_INTERVAL,
    DOMAIN,
    PLATFORMS,
    WEBHOOK_ID_ANTIGRAVITY,
)
from .coordinator import GeminiUsageCoordinator


_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Gemini Usage Monitor from a config entry.

    Called by HA on initial setup and after each reload.

    The coordinator's ``update_interval`` (the frequency at which HA *wakes*
    the coordinator) is set to the **minimum** of the three source-specific
    intervals so that no source is starved.  Each source then tracks its own
    last-fetch timestamp and only runs when its own interval has elapsed.

    Args:
        hass:  The Home Assistant instance.
        entry: The ``ConfigEntry`` created by the config flow.

    Returns:
        ``True`` on success.  Raising ``ConfigEntryNotReady`` inside
        ``coordinator.async_config_entry_first_refresh()`` will cause HA to
        retry automatically.
    """
    # Options take priority over the original data values so that the user
    # can change intervals at runtime without re-running the config flow.
    rate_limit_interval: int = entry.options.get(
        CONF_RATE_LIMIT_INTERVAL,
        entry.data.get(CONF_RATE_LIMIT_INTERVAL, DEFAULT_RATE_LIMIT_INTERVAL),
    )
    gcp_interval: int = entry.options.get(
        CONF_GCP_INTERVAL,
        entry.data.get(CONF_GCP_INTERVAL, DEFAULT_GCP_INTERVAL),
    )
    local_interval: int = entry.options.get(
        CONF_LOCAL_INTERVAL,
        entry.data.get(CONF_LOCAL_INTERVAL, DEFAULT_LOCAL_INTERVAL),
    )

    coordinator = GeminiUsageCoordinator(
        hass=hass,
        entry=entry,
        # Wake the coordinator as often as the fastest source needs it.
        update_interval=timedelta(seconds=min(rate_limit_interval, gcp_interval, local_interval)),
        rate_limit_interval=rate_limit_interval,
        gcp_interval=gcp_interval,
        local_interval=local_interval,
    )

    # Block until the first fetch completes (or raises ConfigEntryNotReady).
    # This populates coordinator.data so that sensor entities can read their
    # initial state synchronously during platform setup.
    await coordinator.async_config_entry_first_refresh()

    # Store under the entry ID so multiple Gemini accounts can coexist.
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator

    # Tell HA to set up our sensor platform.  HA calls sensor.async_setup_entry
    # for each platform listed here.
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Register Webhook for Antigravity Quota push updates
    async def async_handle_antigravity_webhook(hass: HomeAssistant, webhook_id: str, request) -> None:
        """Handle incoming Antigravity quota updates via webhook."""
        try:
            data = await request.json()
        except Exception as exc:
            _LOGGER.warning("Invalid JSON received on Antigravity webhook: %s", exc)
            return

        target_coordinator: GeminiUsageCoordinator | None = hass.data.get(DOMAIN, {}).get(entry.entry_id)
        if target_coordinator:
            await target_coordinator.async_update_antigravity_data(data)

    try:
        from homeassistant.components import webhook
        webhook.async_register(
            hass,
            DOMAIN,
            "Antigravity Quotas",
            WEBHOOK_ID_ANTIGRAVITY,
            async_handle_antigravity_webhook,
        )
    except Exception as exc:
        _LOGGER.debug("Webhook registration note: %s", exc)

    # Register async_reload_entry as the options-change listener.  HA calls
    # it whenever the user saves changes in the options flow UI.
    entry.async_on_unload(entry.add_update_listener(async_reload_entry))

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry and clean up all associated resources.

    Called by HA when:
    - The user removes the integration via the UI.
    - A reload is triggered (by ``async_reload_entry``).

    Forwards the unload to each registered platform so entity state is
    cleaned up, then removes the coordinator from ``hass.data``.

    Args:
        hass:  The Home Assistant instance.
        entry: The config entry being unloaded.

    Returns:
        ``True`` if all platforms unloaded successfully.
    """
    try:
        from homeassistant.components import webhook
        webhook.async_unregister(hass, WEBHOOK_ID_ANTIGRAVITY)
    except Exception:
        pass

    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        # Only remove from hass.data if platforms unloaded cleanly; leaving
        # the coordinator in place on partial failure allows the next retry
        # attempt to start from a known-good state.
        hass.data[DOMAIN].pop(entry.entry_id)
    return unload_ok



async def async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload the integration after an options change.

    Registered as the update listener in ``async_setup_entry``.  HA invokes
    this callback whenever the user submits the options flow form.  The
    simplest correct approach is a full unload + re-setup, which guarantees
    that new interval values, API keys, and path changes all take effect.

    Args:
        hass:  The Home Assistant instance.
        entry: The config entry with updated options.
    """
    await async_unload_entry(hass, entry)
    await async_setup_entry(hass, entry)
