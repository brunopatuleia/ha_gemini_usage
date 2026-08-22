"""
Sensor platform for Gemini Usage Monitor.
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
    """Set up sensor entities for Gemini Usage Monitor."""
    coordinator: GeminiUsageCoordinator = hass.data[DOMAIN][entry.entry_id]

    entity_registry: dict[str, GeminiModelSensor] = {}
    aggregate_registry: dict[str, GeminiAggregateSensor] = {}

    # Register aggregate / core sensors
    aggregate_entities: list[GeminiAggregateSensor] = []
    for sensor_key, sensor_def in AGGREGATE_SENSOR_DEFS.items():
        source = sensor_def.get("source", "")
        # Only add local aggregates if local is configured
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
        """Dynamically add model sensors based on active sources."""
        data = coordinator.data or {}
        model_metadata: dict = data.get("model_metadata", {})
        rate_limits: dict = data.get("rate_limits", {})
        gcp: dict = data.get("gcp", {})
        local: dict = data.get("local", {})

        new_entities: list[GeminiModelSensor] = []

        # 1. Core Model Metadata Sensors (input & output token limits)
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

        # 2. Rate-limit header sensors (only if present in rate_limits data)
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

        # 3. GCP Sensors (only if GCP is configured)
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

        # 4. Local Transcript Sensors (only if Local is configured)
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
    """Device info grouping all Gemini entities."""
    return DeviceInfo(
        identifiers={(DOMAIN, entry.entry_id)},
        name="Gemini Usage Monitor",
        manufacturer="Google",
        model="Gemini API",
        sw_version="1.0.1",
        entry_type="service",
    )


class GeminiModelSensor(CoordinatorEntity, SensorEntity):
    """Represents a specific capacity/usage metric for a Gemini model."""

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
        """Initialise the sensor."""
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
        """Return human-readable sensor name."""
        meta = (self.coordinator.data or {}).get("model_metadata", {}).get(self._model_slug, {})
        display_name = meta.get("display_name") or self._model_slug
        if display_name.startswith("models/"):
            display_name = display_name[len("models/"):]
        return f"{display_name} — {self._sensor_def['name']}"

    @property
    def native_value(self) -> int | float | str | None:
        """Return the sensor value."""
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
        """Return supplementary attributes."""
        data = self.coordinator.data or {}
        attrs: dict[str, Any] = {"model_slug": self._model_slug}

        meta = data.get("model_metadata", {}).get(self._model_slug, {})
        if meta:
            attrs.update(meta)

        return attrs

    @property
    def available(self) -> bool:
        """Entity availability."""
        return self.coordinator.last_update_success and self.native_value is not None


class GeminiAggregateSensor(CoordinatorEntity, SensorEntity):
    """Aggregate / Global status sensor."""

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
        """Return the aggregate reading."""
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
        """Return attributes."""
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
