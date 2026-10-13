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
    ANTIGRAVITY_SENSOR_DEFS,
    CONF_DISPLAY_MODE,
    DEFAULT_DISPLAY_MODE,
    DISPLAY_MODE_REMAINING,
    DISPLAY_MODE_USED,
    DOMAIN,
    SENSOR_DEFS,
    VERSION,
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

    # Register Google Antigravity quota sensors (with Unique ID and UI management)
    antigravity_entities: list[AntigravityQuotaSensor] = []
    for sensor_key, sensor_def in ANTIGRAVITY_SENSOR_DEFS.items():
        sensor = AntigravityQuotaSensor(
            coordinator=coordinator,
            entry=entry,
            sensor_key=sensor_key,
            sensor_def=sensor_def,
        )
        antigravity_entities.append(sensor)

    async_add_entities(antigravity_entities)


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
        sw_version=VERSION,
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


def _antigravity_device_info(entry: ConfigEntry) -> DeviceInfo:
    """Return DeviceInfo grouping all Google Antigravity sensors."""
    return DeviceInfo(
        identifiers={(DOMAIN, f"{entry.entry_id}_antigravity")},
        name="Google Antigravity",
        manufacturer="Google DeepMind",
        model="Antigravity 2.0",
        sw_version=VERSION,
        entry_type="service",
    )



class AntigravityQuotaSensor(CoordinatorEntity, SensorEntity):
    """Sensor entity for Google Antigravity compute quotas with full UI management support."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: GeminiUsageCoordinator,
        entry: ConfigEntry,
        sensor_key: str,
        sensor_def: dict,
    ) -> None:
        """Initialise Antigravity quota sensor."""
        super().__init__(coordinator)
        self._sensor_key = sensor_key
        self._sensor_def = sensor_def
        self._entry = entry

        # Unique ID guarantees this entity is stored in HA's Entity Registry
        self._attr_unique_id = f"{entry.entry_id}_{sensor_key}"
        self._attr_device_info = _antigravity_device_info(entry)
        self._attr_unit_of_measurement = sensor_def.get("unit")
        if sensor_def.get("unit") in ("%", "%/h"):
            self._attr_state_class = SensorStateClass.MEASUREMENT

    @property
    def display_mode(self) -> str:
        """Return user-configured display mode: 'used' (Increasing) or 'remaining' (Decreasing)."""
        return self._entry.options.get(
            CONF_DISPLAY_MODE,
            self._entry.data.get(CONF_DISPLAY_MODE, DEFAULT_DISPLAY_MODE),
        )

    @property
    def name(self) -> str:
        """Return sensor label."""
        sensor_type = self._sensor_def.get("type", "")
        if sensor_type == "dynamic":
            if self.display_mode == DISPLAY_MODE_REMAINING:
                return self._sensor_def.get("name_remaining", self._sensor_def.get("name", self._sensor_key))
            return self._sensor_def.get("name_used", self._sensor_def.get("name", self._sensor_key))
        return self._sensor_def.get("name", self._sensor_key)

    @property
    def icon(self) -> str | None:
        """Return icon based on state or type."""
        sensor_type = self._sensor_def.get("type", "")
        if sensor_type == "resets_in":
            return "mdi:timer-sand"
        if sensor_type == "status":
            return "mdi:cloud-check"
        if sensor_type == "burn_rate":
            return "mdi:fire"
        if sensor_type == "projected":
            return "mdi:chart-bell-curve-cumulative"
        if sensor_type == "runway":
            return "mdi:airplane-takeoff"
        if sensor_type == "pace":
            return "mdi:speedometer"
        return self._sensor_def.get("icon", "mdi:chart-donut")

    @property
    def native_value(self) -> int | float | str | None:
        """Return current sensor state."""
        data = self.coordinator.data or {}
        ag_data = data.get("antigravity", {})
        sensor_type = self._sensor_def.get("type", "")

        if sensor_type == "status":
            return ag_data.get("status", "Waiting for data")

        if sensor_type == "version":
            return VERSION

        bucket_id = self._sensor_def.get("bucket_id")
        bucket = ag_data.get("buckets", {}).get(bucket_id)
        if not bucket:
            return None

        if sensor_type == "dynamic":
            if self.display_mode == DISPLAY_MODE_REMAINING:
                return bucket.get("remaining_pct")
            return bucket.get("used_pct")

        if sensor_type == "used":
            return bucket.get("used_pct")

        if sensor_type == "remaining":
            return bucket.get("remaining_pct")

        if sensor_type == "resets_in":
            return bucket.get("resets_in")

        if sensor_type == "burn_rate":
            return bucket.get("burn_rate_pct_per_h")

        if sensor_type == "projected":
            return bucket.get("projected_pct")

        if sensor_type == "runway":
            return bucket.get("runway")

        if sensor_type == "pace":
            return bucket.get("pace_status")

        return None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return rich metadata including both used and remaining percentages."""
        data = self.coordinator.data or {}
        ag_data = data.get("antigravity", {})
        bucket_id = self._sensor_def.get("bucket_id")
        bucket = ag_data.get("buckets", {}).get(bucket_id)

        attrs: dict[str, Any] = {
            "integration": DOMAIN,
            "version": VERSION,
            "display_mode": self.display_mode,
            "last_synced": ag_data.get("last_updated"),
        }


        if bucket:
            attrs.update({
                "bucket_id": bucket_id,
                "group": bucket.get("group", self._sensor_def.get("group")),
                "window": bucket.get("window"),
                "used_pct": bucket.get("used_pct"),
                "remaining_pct": bucket.get("remaining_pct"),
                "used_fraction": bucket.get("used_fraction"),
                "remaining_fraction": bucket.get("remaining_fraction"),
                "reset_time": bucket.get("reset_time"),
                "resets_in": bucket.get("resets_in"),
                "description": bucket.get("description"),
                "burn_rate_pct_per_h": bucket.get("burn_rate_pct_per_h"),
                "projected_pct": bucket.get("projected_pct"),
                "runway": bucket.get("runway"),
                "runway_hours": bucket.get("runway_hours"),
                "pace_status": bucket.get("pace_status"),
                "is_over_quota_risk": bucket.get("is_over_quota_risk"),
                "safe_pace_pct_per_h": bucket.get("safe_pace_pct_per_h"),
            })

        return attrs


