# Changelog

All notable changes to the **Gemini Usage Monitor for Home Assistant** (`ha_gemini_usage`) will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.1.0] - 2026-10-13

### 🚀 Added
* **Google Antigravity Compute Quota Integration**: Direct real-time monitoring of active compute quotas (Gemini Flash/Pro and Claude/GPT: Weekly & 5-Hour rolling refresh limits) queried directly from the local Antigravity CLI daemon (`agy`).
* **Native Home Assistant Unique IDs**: Every Antigravity entity is natively registered in Home Assistant's Entity Registry under the unified **Google Antigravity** device (`_attr_unique_id = f"{entry.entry_id}_{key}"`), permanently eliminating the *"entity does not have a unique ID"* warning and enabling UI renaming, Area assignment, and custom icons.
* **UI Options Flow for Quota Display Mode**: Choose your preferred metric style directly in the Home Assistant UI (**Settings > Devices & Services > Gemini Usage Monitor > Configure**):
  * **Increasing (0% → 100% Used)**: Fills up like CPU/RAM usage (user preference).
  * **Decreasing (100% → 0% Remaining)**: Depletes like battery/fuel (Google's default).
* **Simultaneous Dedicated & Dynamic Entities**:
  * Primary configurable sensors automatically adapt their state and label to the UI option.
  * Dedicated sensors (`_used`, `_remaining`, `_resets_in`) are always available simultaneously for dashboards and automations.
  * Every entity includes both metrics in its state attributes (`used_pct`, `remaining_pct`, `resets_in`, `reset_time`).
* **Predictive Burn Rate & Quota Runway Engine**:
  * **Consumption Velocity**: `sensor.antigravity_*_burn_rate` (% consumed per hour).
  * **Forecasted Usage at Reset**: `sensor.antigravity_*_projected` (% usage predicted when the window refreshes).
  * **Exhaustion Runway**: `sensor.antigravity_*_runway` (countdown until 100% exhaustion at current pace).
  * **Pace Status**: `sensor.antigravity_*_pace` (`Safe`, `Elevated`, `High Risk (Over Quota)`, `Exhausted`).
  * **Over-Quota Risk Binary Sensor**: `binary_sensor.antigravity_*_over_quota_risk` (`on`/`off`) with `problem` device class for mobile push alerts and desk automation.
* **Native Webhook Ingestion**: Integration webhook `/api/webhook/gemini_usage_antigravity_quota` for instant push updates from the local background daemon without requiring Long-Lived Access Tokens.
* **App Version & Diagnostics**:
  * Unified `VERSION` (`1.1.0`) across `manifest.json`, `const.py`, `sync_antigravity_quota.py`, and device registry info.
  * Added `sensor.antigravity_version` for Home Assistant monitoring.
  * Added `--version` CLI argument to `sync_antigravity_quota.py`.
* **Complete Dashboard Suite**: Documented 7 distinct Lovelace dashboard card styles (Circular Gauges, Detailed Entities list with countdowns, Mushroom Template Cards, Minimalist UI, Mobile Glance, Dynamic Markdown Banner, and Forecast Runway Card).
* **Automation Blueprint / Example**: Added turnkey YAML automation for instant push notifications when quota burn rate threatens to hit the cap before reset.

### 🛡️ Hardened & Fixed (Multi-Agent Code Review Audit)
* **Resolved Fatal ImportError**: Added missing `CONF_LOCAL_INTERVAL` to `const.py`.
* **Fixed Listener Leak**: Switched `async_reload_entry` to canonical `hass.config_entries.async_reload` and registered dynamic listeners with `entry.async_on_unload`.
* **HA ClientSession & Modern Architecture**: Converted all manual `aiohttp.ClientSession` instances to `async_get_clientsession(hass)` and adopted `entry.runtime_data`.
* **Persistent Connection Pooling**: Implemented `requests.Session` Keep-Alive pooling in `sync_antigravity_quota.py`, reducing daily socket churn by 98% (from 56,160 connections down to 1 persistent connection).
* **Circuit Breaker on HA Outages**: Added fail-fast circuit breaker to the sync daemon, preventing 6.5-minute thread freezes and 2,340 log error lines/hour during Home Assistant restarts.
* **Restored Countdown Timers**: Restored missing `_resets_in` sensors (`sensor.antigravity_*_resets_in`) across all four compute buckets, unfreezing live dashboard countdowns.
* **Corrected Binary Sensor Entity IDs**: Formatted over-quota alert sensors cleanly as `binary_sensor.antigravity_*_over_quota_risk`.
* **Watchdog Keepalive in Backoff**: Dispatched heartbeats every 10s during authentication backoff so `WatchdogSec=120s` never kills the daemon.
* **Config Flow Argument Alignment**: Aligned `_validate_api_key(hass, api_key)` signature with caller in `config_flow.py`.
* **Offline Fault Tolerance**: Antigravity quota monitoring remains online and functional even when Google Gemini developer API is unreachable or omitted.
* **Polling Cadence Preservation**: Webhook push updates notify listeners without resetting the coordinator's periodic polling timer.
* **Strict Webhook Schema Validation**: Rejected non-dictionary JSON payloads and removed unvalidated external dictionary passthroughs.
* **Hardened Mathematical Edge Cases**:
  * Corrected offset-naive vs offset-aware ISO datetime parsing (`TypeError`).
  * Enforced minimum 15-minute / 2% observation threshold to prevent early-window 3-minute extrapolation spikes ($>3,000\%$).
  * Eliminated float round-to-zero bug that prematurely tripped "Exhausted" pace status on 0.01% remaining.
  * Capped safe pace rate calculations as $\text{hours to reset} \to 0$.
* **Security & Daemon Lifecycle**:
  * Moved Gemini API key from URL query string to `x-goog-api-key` HTTP header.
  * Added `SIGTERM`/`SIGINT` graceful shutdown and pure-Python systemd watchdog notifications (`sd_notify`).
  * Updated systemd service unit with `WatchdogSec=120s` and `Restart=on-failure`.

### 🍸 Disclosed
* **100% Vibe Coded Project**: Prominently cited Google DeepMind Antigravity, Gemini 3.8 Flash, and Claude (Claude Opus 5.5 High, Claude 3.7 Sonnet, Claude 3.5 Sonnet) as the sole code authors (zero human-written code).

---

## [1.0.1] - 2026-08-23


### 🚀 Added
* **Live Model Capacity Sensors**: Automatically creates `input_token_limit` and `output_token_limit` sensors for every discovered model directly from the Gemini model catalogue.
* **API Health & Latency Monitoring**: Added `sensor.gemini_api_status` (`Online` / `Offline`) and `sensor.gemini_api_latency` (in milliseconds) for real-time endpoint health tracking.
* **Smart Source Filtering**: Entities for optional sources (GCP Cloud Monitoring, Local Antigravity logs) are now registered **only when their respective source is actively configured**, completely eliminating unconfigured "Unavailable" ghost entities.
* **Comprehensive In-Code Documentation**: Expanded architecture guides, docstrings, and inline technical comments across all Python modules.

### 🐛 Fixed
* Fixed issue where 500+ placeholder entities were generated as "Unavailable" when optional data sources (GCP / Local logs) were omitted during setup.
* Rate limit response header sensors are now selectively created only when returned by the API.

---

## [1.0.0] - 2026-08-23

### 🎉 Initial Release
* **Dynamic Model Discovery**: Enumerates all Gemini models available to the API key via `GET /v1beta/models`.
* **Multi-source Data Coordinator**:
  * Gemini REST API probing for rate-limit response headers.
  * Google Cloud Monitoring quota integration via GCP Service Account.
  * Local Antigravity conversation transcript JSONL log parser.
* **Config Flow**: 3-step UI-based setup wizard with instant API key validation and an options flow for runtime configuration updates.
* **HACS Support**: Custom repository support with `hacs.json` and MIT licensing.
