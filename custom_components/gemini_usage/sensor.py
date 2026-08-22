"""
Sensor platform for Gemini Usage Monitor.

This module is the bridge between the ``GeminiUsageCoordinator`` (which holds
all the raw data) and the Home Assistant entity registry (which displays
sensors in the UI, automations, and dashboards).

Entity creation strategy
------------------------
Sensors are created **dynamically** as models are discovered, rather than
being defined statically.  This is necessary because the set of available
Gemini models changes over time (Google periodically adds and deprecates
model versions), and hard-coding entity IDs would require code updates with
every model release.

The dynamic approach works as follows:

1. ``async_setup_entry`` is called once by HA during platform setup.  It
   immediately creates the **aggregate sensors** (total tokens today, total
   tokens this month, model count) which are always present regardless of
   which models are found.

2. A ``@callback`` function (``_handle_coordinator_update``) is registered as
   a listener on the coordinator.  This listener is invoked every time the
   coordinator publishes new data.

3. On each invocation, the listener collects all model slugs visible across
   the four data buckets (model list, rate-limit data, GCP data, local data),
   computes which ``(slug, sensor_key)`` combinations are not yet registered,
   and calls ``async_add_entities`` for the new ones.

4. A local ``entity_registry`` dict (keyed by ``"{slug}__{sensor_key}"``)
   tracks which entities have already been created so entities are never
   duplicated across update cycles.

5. The listener is called once immediately after registration (with the data
   from the first refresh) so entities appear in the UI synchronously during
   the first load, not only after the second coordinator tick.

Entity classes
--------------
``GeminiModelSensor``
    Represents one metric (e.g. ``tpm_remaining``) for one model (e.g.
    ``gemini-2.0-flash``).  Its ``native_value`` reads from the appropriate
    bucket in ``coordinator.data`` via the ``source`` key in ``SENSOR_DEFS``.

``GeminiAggregateSensor``
    Represents a cross-model summary (total tokens today / this month /
    model count).  Its ``native_value`` is computed from all slugs in the
    relevant bucket rather than from a single model's entry.

Both classes inherit ``CoordinatorEntity`` so HA automatically subscribes
them to coordinator updates and calls ``async_write_ha_state`` on each tick.
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


# ---------------------------------------------------------------------------
# Platform setup
# ---------------------------------------------------------------------------


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Gemini Usage sensor entities for a config entry.

    Called by HA once per config entry when the ``sensor`` platform is
    initialised (i.e. during ``async_setup_entry`` in ``__init__.py`` via
    ``async_forward_entry_setups``).

    This function:
    1. Retrieves the coordinator from ``hass.data``.
    2. Creates and registers the static aggregate sensors immediately.
    3. Registers a coordinator listener that adds per-model sensors
       dynamically whenever new models appear in the coordinator data.

    Args:
        hass:               The Home Assistant instance.
        entry:              The config entry for this integration instance.
        async_add_entities: Callback provided by HA to register new entities.
    """
    coordinator: GeminiUsageCoordinator = hass.data[DOMAIN][entry.entry_id]

    # Local registries prevent duplicate entity creation across update cycles.
    # Keys are "{slug}__{sensor_key}" for model sensors and sensor_key for aggregates.
    entity_registry: dict[str, GeminiModelSensor] = {}
    aggregate_registry: dict[str, GeminiAggregateSensor] = {}

    # --- Create aggregate sensors immediately (they don't depend on models) ---
    aggregate_entities: list[GeminiAggregateSensor] = []
    for sensor_key, sensor_def in AGGREGATE_SENSOR_DEFS.items():
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
        """Detect newly discovered models and register their sensor entities.

        This function is called:
        - Once immediately after ``coordinator.async_add_listener`` (via the
          explicit call at the end of ``async_setup_entry``).
        - On every coordinator update thereafter (driven by the timer).

        The entity-registry dict ensures idempotency: the same
        ``(slug, sensor_key)`` pair can never produce two entities.

        New entities are batched and registered in a single
        ``async_add_entities`` call so HA processes them efficiently.
        """
        data = coordinator.data or {}

        # Gather the complete set of model slugs seen across all sources.
        # A model might appear in the API list but have no rate-limit data yet
        # (if probing failed), or it might appear in local transcripts without
        # ever being listed by the API.  All such slugs should get sensors.
        all_slugs: set[str] = set()
        for m in data.get("models", []):
            name = m.get("name", "")
            if name:
                all_slugs.add(_model_slug(name))
        # Also include slugs from sources that may have data before the model
        # list was populated (e.g. on the very first coordinator tick).
        all_slugs.update(data.get("rate_limits", {}).keys())
        all_slugs.update(data.get("gcp", {}).keys())
        all_slugs.update(data.get("local", {}).keys())

        new_entities: list[GeminiModelSensor] = []
        for slug in all_slugs:
            for sensor_key, sensor_def in SENSOR_DEFS.items():
                # Compound key uniquely identifies this (model, metric) pair.
                entity_id_key = f"{slug}__{sensor_key}"
                if entity_id_key not in entity_registry:
                    sensor = GeminiModelSensor(
                        coordinator=coordinator,
                        entry=entry,
                        model_slug=slug,
                        sensor_key=sensor_key,
                        sensor_def=sensor_def,
                    )
                    entity_registry[entity_id_key] = sensor
                    new_entities.append(sensor)

        if new_entities:
            _LOGGER.debug("Adding %d new Gemini sensor entities", len(new_entities))
            async_add_entities(new_entities)

    # Register the listener — called on every coordinator data update.
    coordinator.async_add_listener(_handle_coordinator_update)
    # Run immediately so entities are created from the first-refresh data
    # rather than waiting for the second coordinator tick.
    _handle_coordinator_update()


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _device_info(entry: ConfigEntry) -> DeviceInfo:
    """Build a ``DeviceInfo`` object shared by all sensors in this entry.

    Grouping all entities under a single "device" keeps the HA dashboard
    tidy — users see one "Gemini Usage Monitor" device with many sensors,
    rather than a flat list of unrelated entities.

    Args:
        entry: The config entry that owns this device.

    Returns:
        A ``DeviceInfo`` instance with Google/Gemini branding.
    """
    return DeviceInfo(
        identifiers={(DOMAIN, entry.entry_id)},
        name="Gemini Usage Monitor",
        manufacturer="Google",
        model="Gemini API",
        sw_version="1.0.0",
        entry_type="service",
    )


# ---------------------------------------------------------------------------
# Entity classes
# ---------------------------------------------------------------------------


class GeminiModelSensor(CoordinatorEntity, SensorEntity):
    """Represents one usage metric for a single Gemini model.

    Instances are created dynamically by ``_handle_coordinator_update`` as
    models are discovered.  Each instance is parameterised by:

    - ``model_slug``: the sanitised, entity-ID-safe model identifier
      (e.g. ``"gemini_2_0_flash"``).
    - ``sensor_key``: the metric name within ``SENSOR_DEFS``
      (e.g. ``"tpm_remaining"``).
    - ``sensor_def``: the metadata dict from ``SENSOR_DEFS`` that provides
      the human-readable name, unit, icon, and data source.

    The ``source`` field in ``sensor_def`` determines which bucket of
    ``coordinator.data`` the sensor reads from:
    - ``"api_headers"`` → ``coordinator.data["rate_limits"][slug]``
    - ``"gcp"``         → ``coordinator.data["gcp"][slug]``
    - ``"local"``       → ``coordinator.data["local"][slug]``

    Availability
    ~~~~~~~~~~~~
    An entity is marked **unavailable** (grey in HA UI) when the coordinator
    last update failed *or* when the specific data key is absent from the
    relevant bucket (e.g. a GCP sensor when GCP is not configured).  This is
    preferable to showing a stale or zero value which could be misleading.
    """

    _attr_state_class = SensorStateClass.MEASUREMENT
    """Declares this as a measurement sensor (not a total/sum).  HA uses this
    to decide whether to include the entity in long-term statistics."""

    _attr_has_entity_name = True
    """Tells HA that ``name`` is relative to the device name, so the full
    entity name is "Gemini Usage Monitor {model} — {metric}"."""

    def __init__(
        self,
        coordinator: GeminiUsageCoordinator,
        entry: ConfigEntry,
        model_slug: str,
        sensor_key: str,
        sensor_def: dict,
    ) -> None:
        """Initialise the sensor with its coordinator and identity parameters.

        Args:
            coordinator: The shared data coordinator for this integration instance.
            entry:       The config entry (used for unique-ID namespacing and
                         device-info construction).
            model_slug:  Sanitised model identifier (e.g. ``"gemini_2_0_flash"``).
            sensor_key:  Metric key from ``SENSOR_DEFS`` (e.g. ``"tpm_remaining"``).
            sensor_def:  Metadata dict from ``SENSOR_DEFS``.
        """
        super().__init__(coordinator)
        self._model_slug = model_slug
        self._sensor_key = sensor_key
        self._sensor_def = sensor_def
        self._entry = entry

        # Unique ID: entry_id scopes it to this integration instance so
        # multiple Gemini accounts can coexist without ID collisions.
        self._attr_unique_id = f"{entry.entry_id}_{model_slug}_{sensor_key}"
        self._attr_unit_of_measurement = sensor_def["unit"]
        self._attr_icon = sensor_def["icon"]
        self._attr_device_info = _device_info(entry)

    @property
    def name(self) -> str:
        """Return a human-readable name combining the model display name and metric.

        Prefers ``displayName`` from the model list (e.g. ``"Gemini 2.0 Flash"``)
        over the raw resource name (``"models/gemini-2.0-flash"``).  Falls back
        to the slug if the model list has not been fetched yet.

        Example return value: ``"Gemini 2.0 Flash — Tokens/Min Remaining"``

        Returns:
            A descriptive string for display in the HA entity registry.
        """
        models: list[dict] = (self.coordinator.data or {}).get("models", [])
        display_name = self._model_slug  # Fallback to slug if no model metadata
        for m in models:
            if _model_slug(m.get("name", "")) == self._model_slug:
                display_name = m.get("displayName") or m.get("name", self._model_slug)
                # Strip the "models/" resource prefix from unaliased names.
                if display_name.startswith("models/"):
                    display_name = display_name[len("models/"):]
                break
        return f"{display_name} — {self._sensor_def['name']}"

    @property
    def native_value(self) -> int | float | None:
        """Return the current sensor reading.

        Reads the appropriate key from the coordinator data bucket determined
        by ``sensor_def["source"]``.  Returns ``None`` if the key is absent,
        which causes the entity to be shown as unavailable (see ``available``).

        Local sensors use a mapping (``"tokens_today"`` → ``"today"``) because
        the transcript scanner stores values under shorter keys to avoid
        redundancy in the per-model dict.

        Returns:
            An ``int`` or ``float`` value, or ``None`` if data is not yet
            available for this source/model combination.
        """
        data = self.coordinator.data or {}
        source = self._sensor_def["source"]

        if source == "api_headers":
            # Rate-limit header data: keyed directly by sensor_key
            model_data = data.get("rate_limits", {}).get(self._model_slug, {})
            return model_data.get(self._sensor_key)

        if source == "gcp":
            # GCP Cloud Monitoring data: keyed directly by sensor_key
            model_data = data.get("gcp", {}).get(self._model_slug, {})
            return model_data.get(self._sensor_key)

        if source == "local":
            # Local transcript data uses shorter internal key names.
            model_data = data.get("local", {}).get(self._model_slug, {})
            key_map = {"tokens_today": "today", "tokens_this_month": "this_month"}
            return model_data.get(key_map.get(self._sensor_key, self._sensor_key))

        return None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return supplementary attributes exposed in the entity's detail panel.

        Attributes serve two purposes:
        1. **Debugging**: raw source data (headers, GCP series info) lets
           users verify what the integration is receiving.
        2. **Dashboard cards**: context window sizes, display names, and
           supported methods are useful for building rich Lovelace cards.

        Model metadata (context window, output limit, supported methods) is
        attached to *every* sensor for the same model so users can access
        it from whichever sensor they happen to inspect.

        Returns:
            A flat dict of attribute name → value.  HA stores these alongside
            the sensor state in the recorder database.
        """
        data = self.coordinator.data or {}
        attrs: dict[str, Any] = {"model_slug": self._model_slug}

        # --- Model metadata (from the model list) ---
        models: list[dict] = data.get("models", [])
        for m in models:
            if _model_slug(m.get("name", "")) == self._model_slug:
                attrs["model_name"] = m.get("name")
                attrs["display_name"] = m.get("displayName")
                attrs["input_token_limit"] = m.get("inputTokenLimit")
                attrs["output_token_limit"] = m.get("outputTokenLimit")
                attrs["supported_generation_methods"] = m.get(
                    "supportedGenerationMethods"
                )
                break

        # --- Source-specific raw data ---
        # Errors are excluded from attributes to keep the panel clean; they
        # appear in the HA log at DEBUG level.
        source = self._sensor_def["source"]
        if source == "api_headers":
            raw = data.get("rate_limits", {}).get(self._model_slug, {})
            attrs.update({k: v for k, v in raw.items() if k != "error"})
        elif source == "gcp":
            raw = data.get("gcp", {}).get(self._model_slug, {})
            attrs.update(raw)
        elif source == "local":
            raw = data.get("local", {}).get(self._model_slug, {})
            attrs.update(raw)

        return attrs

    @property
    def available(self) -> bool:
        """Return ``True`` only when the coordinator has fresh data and a value.

        An entity is unavailable when:
        - The coordinator's last update failed (network error, bad credentials).
        - The data key for this sensor is absent from the relevant bucket
          (e.g. GCP sensors when GCP is not configured, or a model that was
          probed but didn't return this particular header).

        Marking unavailable is safer than returning ``0`` or a stale value,
        which could mislead automations.

        Returns:
            ``bool`` — ``True`` if the entity should show a value.
        """
        return self.coordinator.last_update_success and self.native_value is not None


class GeminiAggregateSensor(CoordinatorEntity, SensorEntity):
    """Represents a cross-model summary metric.

    Unlike ``GeminiModelSensor``, these entities are created immediately during
    ``async_setup_entry`` and are always present — they do not depend on model
    discovery.

    Supported sensor keys (from ``AGGREGATE_SENSOR_DEFS``):
    - ``"model_count"``          — count of models in the API model list.
    - ``"total_tokens_today"``   — sum of today's token counts across all slugs.
    - ``"total_tokens_this_month"`` — same but for the current calendar month.

    The ``extra_state_attributes`` for token totals include a per-model
    breakdown so users can see which model contributed how many tokens.
    For ``model_count`` the full model catalogue (with limits) is attached.
    """

    _attr_state_class = SensorStateClass.MEASUREMENT
    """Marks this as a measurement (not a cumulative total)."""

    _attr_has_entity_name = True
    """Name is relative to the device; HA prepends the device name."""

    def __init__(
        self,
        coordinator: GeminiUsageCoordinator,
        entry: ConfigEntry,
        sensor_key: str,
        sensor_def: dict,
    ) -> None:
        """Initialise the aggregate sensor.

        Args:
            coordinator: The shared data coordinator.
            entry:       The config entry (for unique-ID and device-info).
            sensor_key:  Key from ``AGGREGATE_SENSOR_DEFS`` (e.g. ``"model_count"``).
            sensor_def:  Metadata dict from ``AGGREGATE_SENSOR_DEFS``.
        """
        super().__init__(coordinator)
        self._sensor_key = sensor_key
        self._sensor_def = sensor_def
        self._entry = entry

        # Use just the sensor_key (no model slug) in the unique ID.
        self._attr_unique_id = f"{entry.entry_id}_{sensor_key}"
        self._attr_name = sensor_def["name"]
        self._attr_unit_of_measurement = sensor_def["unit"]
        self._attr_icon = sensor_def["icon"]
        self._attr_device_info = _device_info(entry)

    @property
    def native_value(self) -> int | float | None:
        """Compute and return the aggregate value from coordinator data.

        Each sensor key has its own computation:
        - ``model_count``: ``len(data["models"])``
        - ``total_tokens_today``: ``sum(v["today"] for v in data["local"].values())``
        - ``total_tokens_this_month``: ``sum(v["this_month"] for v in ...)``

        Returns:
            An integer count/sum, or ``None`` if data is unavailable.
        """
        data = self.coordinator.data or {}

        if self._sensor_key == "model_count":
            # Simply count the models returned by the last API call.
            return len(data.get("models", []))

        if self._sensor_key == "total_tokens_today":
            # Sum across all model slugs in the local data bucket.
            local = data.get("local", {})
            return sum(v.get("today", 0) for v in local.values())

        if self._sensor_key == "total_tokens_this_month":
            local = data.get("local", {})
            return sum(v.get("this_month", 0) for v in local.values())

        return None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return breakdown attributes for aggregate sensors.

        For token-total sensors: a ``"breakdown"`` dict mapping model slug to
        its individual count, allowing dashboard cards to render a per-model
        breakdown alongside the total.

        For the model-count sensor: a ``"models"`` list with each model's
        name, display name, and context window limits — useful for populating
        a model-picker dropdown in a custom Lovelace card.

        Returns:
            A flat dict of attribute name → value.
        """
        data = self.coordinator.data or {}
        attrs: dict[str, Any] = {}

        if self._sensor_key in ("total_tokens_today", "total_tokens_this_month"):
            local = data.get("local", {})
            # Determine which sub-key to read ("today" vs "this_month").
            key = "today" if "today" in self._sensor_key else "this_month"
            attrs["breakdown"] = {
                slug: vals.get(key, 0) for slug, vals in local.items()
            }

        if self._sensor_key == "model_count":
            # Expose the full model catalogue for dashboard and automation use.
            attrs["models"] = [
                {
                    "name": m.get("name"),
                    "displayName": m.get("displayName"),
                    "inputTokenLimit": m.get("inputTokenLimit"),
                    "outputTokenLimit": m.get("outputTokenLimit"),
                }
                for m in data.get("models", [])
            ]

        return attrs
