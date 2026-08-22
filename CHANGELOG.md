# Changelog

All notable changes to the **Gemini Usage Monitor for Home Assistant** (`ha_gemini_usage`) will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
