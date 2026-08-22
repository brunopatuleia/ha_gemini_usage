"""
Sensor platform for Gemini Usage Monitor.

This module registers Home Assistant sensor entities based on the active
configuration and data discovered by the GeminiUsageCoordinator.

Registration Architecture:
--------------------------
- **Core Aggregate Entities**:
  * `sensor.gemini_model_count`: Count of accessible models.
  * `sensor.gemini_api_status`: 'Online' / 'Offline' status of the API endpoint.
  * `sensor.gemini_api_latency`: Round-trip HTTP response time in ms.
  * `sensor.gemini_total_tokens_today` / `this_month`: Created only when local tracking is active.

- **Per-Model Entities**:
  * `sensor.gemini_{model}_input_token_limit`: Max context window (e.g. 1,048,576 tokens).
  * `sensor.gemini_{model}_output_token_limit`: Max generated tokens (e.g. 8,192 tokens).
  * GCP quota sensors and local token sensors are only registered if their respective
    data sources are enabled, preventing 'Unavailable' ghost entities.
"""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.sensor import SensorEntity, SensorStateClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    AGGREGATE_SENSOR_DEFS,
    DOMAIN,
    SENSOR_DEFS,
)
from .coordinator import GeminiUsageCoordinator, _model_slug

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Gemini Usage sensor entities from a config entry."""
    coordinator: GeminiUsageCoordinator = hass.data[DOMAIN][entry.entry_id]

    entity_registry: dict[str, GeminiModelSensor] = {}
    aggregate_registry: dict[str, GeminiAggregateSensor] = {}

    # Register aggregate and core health sensors
    aggregate_entities: list[GeminiAggregateSensor] = []
    for sensor_key, sensor_def in AGGREGATE_SENSOR_DEFS.items():
        source = sensor_def.get("source", "")
        # Omit local aggregate sensors if local tracking is not configured
        if source == "local" and not coordinator.is_local_configured:
            continue

        sensor = GeminiAggregateSensor(
            coordinator=coordinator,
            entry=entry,
            sensor_key=sensor_key,
            sensor_def=sensor_def,
        )
        aggregate_registry[sensor_key] = sensor
        aggregate_entities.append(sensor)

    async_add_entities(aggregate_entities)

    @callback
    def _handle_coordinator_update() -> None:
        """Dynamically create model sensors only for active and populated sources."""
        data = coordinator.data or {}
        model_metadata: dict = data.get("model_metadata", {})
        rate_limits: dict = data.get("rate_limits", {})
        gcp: dict = data.get("gcp", {})
        local: dict = data.get("local", {})

        new_entities: list[GeminiModelSensor] = []

        # 1. Model capacity & limits (always available from model catalogue)
        for slug in model_metadata:
            for key in ("input_token_limit", "output_token_limit"):
                if key in SENSOR_DEFS:
                    entity_id_key = f"{slug}__{key}"
                    if entity_id_key not in entity_registry:
                        sensor = GeminiModelSensor(
                            coordinator=coordinator,
                            entry=entry,
                            model_slug=slug,
                            sensor_key=key,
                            sensor_def=SENSOR_DEFS[key],
                        )
                        entity_registry[entity_id_key] = sensor
                        new_entities.append(sensor)

        # 2. Live rate limit header sensors (registered if returned in response)
        for slug, r_data in rate_limits.items():
            for key in ("rpm_remaining", "rpm_limit", "tpm_remaining", "tpm_limit", "rpd_remaining", "rpd_limit"):
                if key in r_data and r_data[key] is not None:
                    entity_id_key = f"{slug}__{key}"
                    if entity_id_key not in entity_registry:
                        sensor = GeminiModelSensor(
                            coordinator=coordinator,
                            entry=entry,
                            model_slug=slug,
                            sensor_key=key,
                            sensor_def=SENSOR_DEFS[key],
                        )
                        entity_registry[entity_id_key] = sensor
                        new_entities.append(sensor)

        # 3. GCP Cloud Monitoring sensors (registered only if GCP credentials provided)
        if coordinator.is_gcp_configured:
            for slug in gcp:
                for key in ("tokens_input_1h", "tokens_output_1h", "quota_used_pct"):
                    if key in SENSOR_DEFS:
                        entity_id_key = f"{slug}__{key}"
                        if entity_id_key not in entity_registry:
                            sensor = GeminiModelSensor(
                                coordinator=coordinator,
                                entry=entry,
                                model_slug=slug,
                                sensor_key=key,
                                sensor_def=SENSOR_DEFS[key],
                            )
                            entity_registry[entity_id_key] = sensor
                            new_entities.append(sensor)

        # 4. Local transcript sensors (registered only if local directory configured)
        if coordinator.is_local_configured:
            for slug in local:
                for key in ("tokens_today", "tokens_this_month"):
                    if key in SENSOR_DEFS:
                        entity_id_key = f"{slug}__{key}"
                        if entity_id_key not in entity_registry:
                            sensor = GeminiModelSensor(
                                coordinator=coordinator,
                                entry=entry,
                                model_slug=slug,
                                sensor_key=key,
                                sensor_def=SENSOR_DEFS[key],
                            )
                            entity_registry[entity_id_key] = sensor
                            new_entities.append(sensor)

        if new_entities:
            _LOGGER.debug("Registering %d new Gemini sensors", len(new_entities))
            async_add_entities(new_entities)

    coordinator.async_add_listener(_handle_coordinator_update)
    _handle_coordinator_update()


def _device_info(entry: ConfigEntry) -> DeviceInfo:
    """Return unified DeviceInfo grouping all Gemini sensors."""
    return DeviceInfo(
        identifiers={(DOMAIN, entry.entry_id)},
        name="Gemini Usage Monitor",
        manufacturer="Google",
        model="Gemini API",
        sw_version="1.0.1",
        entry_type="service",
    )


class GeminiModelSensor(CoordinatorEntity, SensorEntity):
    """Sensor entity representing a capacity or usage metric for an individual Gemini model."""

    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: GeminiUsageCoordinator,
        entry: ConfigEntry,
        model_slug: str,
        sensor_key: str,
        sensor_def: dict,
    ) -> None:
        """Initialise per-model sensor."""
        super().__init__(coordinator)
        self._model_slug = model_slug
        self._sensor_key = sensor_key
        self._sensor_def = sensor_def
        self._entry = entry

        self._attr_unique_id = f"{entry.entry_id}_{model_slug}_{sensor_key}"
        self._attr_unit_of_measurement = sensor_def.get("unit")
        self._attr_icon = sensor_def.get("icon")
        self._attr_device_info = _device_info(entry)

    @property
    def name(self) -> str:
        """Return human-readable sensor label."""
        meta = (self.coordinator.data or {}).get("model_metadata", {}).get(self._model_slug, {})
        display_name = meta.get("display_name") or self._model_slug
        if display_name.startswith("models/"):
            display_name = display_name[len("models/"):]
        return f"{display_name} — {self._sensor_def['name']}"

    @property
    def native_value(self) -> int | float | str | None:
        """Return current sensor value."""
        data = self.coordinator.data or {}
        source = self._sensor_def.get("source")

        if source == "model_metadata":
            meta = data.get("model_metadata", {}).get(self._model_slug, {})
            return meta.get(self._sensor_key)

        if source == "api_headers":
            model_data = data.get("rate_limits", {}).get(self._model_slug, {})
            return model_data.get(self._sensor_key)

        if source == "gcp":
            model_data = data.get("gcp", {}).get(self._model_slug, {})
            return model_data.get(self._sensor_key)

        if source == "local":
            model_data = data.get("local", {}).get(self._model_slug, {})
            key_map = {"tokens_today": "today", "tokens_this_month": "this_month"}
            return model_data.get(key_map.get(self._sensor_key, self._sensor_key))

        return None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return additional attributes such as model description and methods."""
        data = self.coordinator.data or {}
        attrs: dict[str, Any] = {"model_slug": self._model_slug}

        meta = data.get("model_metadata", {}).get(self._model_slug, {})
        if meta:
            attrs.update(meta)

        return attrs

    @property
    def available(self) -> bool:
        """Entity availability status."""
        return self.coordinator.last_update_success and self.native_value is not None


class GeminiAggregateSensor(CoordinatorEntity, SensorEntity):
    """Sensor entity representing an aggregate metric or overall service health."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: GeminiUsageCoordinator,
        entry: ConfigEntry,
        sensor_key: str,
        sensor_def: dict,
    ) -> None:
        """Initialise aggregate sensor."""
        super().__init__(coordinator)
        self._sensor_key = sensor_key
        self._sensor_def = sensor_def
        self._entry = entry

        self._attr_unique_id = f"{entry.entry_id}_{sensor_key}"
        self._attr_name = sensor_def["name"]
        self._attr_unit_of_measurement = sensor_def.get("unit")
        self._attr_icon = sensor_def.get("icon")
        self._attr_device_info = _device_info(entry)

        if sensor_def.get("unit"):
            self._attr_state_class = SensorStateClass.MEASUREMENT

    @property
    def native_value(self) -> int | float | str | None:
        """Return aggregate value."""
        data = self.coordinator.data or {}

        if self._sensor_key == "model_count":
            return len(data.get("models", []))

        if self._sensor_key == "api_status":
            return data.get("api_status", "Unknown")

        if self._sensor_key == "api_latency":
            return data.get("api_latency")

        if self._sensor_key == "total_tokens_today":
            local = data.get("local", {})
            return sum(v.get("today", 0) for v in local.values())

        if self._sensor_key == "total_tokens_this_month":
            local = data.get("local", {})
            return sum(v.get("this_month", 0) for v in local.values())

        return None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return extra state attributes."""
        data = self.coordinator.data or {}
        attrs: dict[str, Any] = {}

        if self._sensor_key == "model_count":
            models = data.get("models", [])
            attrs["models"] = [
                {
                    "name": m.get("name"),
                    "displayName": m.get("displayName"),
                    "inputTokenLimit": m.get("inputTokenLimit"),
                    "outputTokenLimit": m.get("outputTokenLimit"),
                }
                for m in models
            ]

        if self._sensor_key == "api_status":
            attrs["latency_ms"] = data.get("api_latency")
            attrs["model_count"] = len(data.get("models", []))

        return attrs
