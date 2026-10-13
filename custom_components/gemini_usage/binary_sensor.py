"""
Binary sensor platform for Gemini Usage Monitor.

Exposes predictive over-quota risk sensors for Google Antigravity compute limits.
When your current consumption burn rate indicates that your quota will exhaust
before the scheduled reset time, the binary sensor turns 'on' (Problem detected),
allowing instant automations, mobile push notifications, or smart RGB desk alerts.
"""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import ANTIGRAVITY_BINARY_SENSOR_DEFS, DOMAIN
from .coordinator import GeminiUsageCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Antigravity binary sensor entities from a config entry."""
    coordinator: GeminiUsageCoordinator = hass.data[DOMAIN][entry.entry_id]

    entities: list[AntigravityOverQuotaRiskBinarySensor] = []
    for sensor_key, sensor_def in ANTIGRAVITY_BINARY_SENSOR_DEFS.items():
        sensor = AntigravityOverQuotaRiskBinarySensor(
            coordinator=coordinator,
            entry=entry,
            sensor_key=sensor_key,
            sensor_def=sensor_def,
        )
        entities.append(sensor)

    async_add_entities(entities)


def _antigravity_device_info(entry: ConfigEntry) -> DeviceInfo:
    """Return DeviceInfo grouping all Google Antigravity sensors."""
    return DeviceInfo(
        identifiers={(DOMAIN, f"{entry.entry_id}_antigravity")},
        name="Google Antigravity",
        manufacturer="Google DeepMind",
        model="Antigravity 2.0",
        sw_version="2.0",
        entry_type="service",
    )


class AntigravityOverQuotaRiskBinarySensor(CoordinatorEntity, BinarySensorEntity):
    """Binary sensor indicating whether an Antigravity quota is at risk of being exhausted before reset."""

    _attr_has_entity_name = True
    _attr_device_class = BinarySensorDeviceClass.PROBLEM

    def __init__(
        self,
        coordinator: GeminiUsageCoordinator,
        entry: ConfigEntry,
        sensor_key: str,
        sensor_def: dict,
    ) -> None:
        """Initialise binary sensor."""
        super().__init__(coordinator)
        self._sensor_key = sensor_key
        self._sensor_def = sensor_def
        self._entry = entry

        self._attr_unique_id = f"{entry.entry_id}_{sensor_key}"
        self._attr_name = sensor_def["name"]
        self._attr_icon = sensor_def.get("icon", "mdi:alert-circle-outline")
        self._attr_device_info = _antigravity_device_info(entry)

    @property
    def is_on(self) -> bool:
        """Return True if usage burn rate predicts quota exhaustion before reset."""
        data = self.coordinator.data or {}
        ag_data = data.get("antigravity", {})
        bucket_id = self._sensor_def.get("bucket_id")
        bucket = ag_data.get("buckets", {}).get(bucket_id, {})
        return bool(bucket.get("is_over_quota_risk", False))

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return predictive analytics metadata."""
        data = self.coordinator.data or {}
        ag_data = data.get("antigravity", {})
        bucket_id = self._sensor_def.get("bucket_id")
        bucket = ag_data.get("buckets", {}).get(bucket_id, {})

        return {
            "bucket_id": bucket_id,
            "group": bucket.get("group", self._sensor_def.get("group")),
            "burn_rate_pct_per_h": bucket.get("burn_rate_pct_per_h"),
            "projected_pct": bucket.get("projected_pct"),
            "runway": bucket.get("runway"),
            "pace_status": bucket.get("pace_status"),
            "safe_pace_pct_per_h": bucket.get("safe_pace_pct_per_h"),
            "used_pct": bucket.get("used_pct"),
            "remaining_pct": bucket.get("remaining_pct"),
            "resets_in": bucket.get("resets_in"),
            "reset_time": bucket.get("reset_time"),
            "last_synced": ag_data.get("last_updated"),
        }
