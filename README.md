# Gemini Usage Monitor for Home Assistant (`ha_gemini_usage`)

> [!NOTE]
> 💡 **Concept & Idea**: [@brunopatuleia](https://github.com/brunopatuleia)  
> ⚡ **Code & Implementation**: **100% vibe coded by AI** (Google Antigravity / Gemini & Claude). From architecture and dynamic multi-model sensor design to async coordinators, documentation, and tests — entirely written by the AI assistant. Use at your own discretion, inspect the code, and PRs / improvements are warmly welcomed!

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://github.com/hacs/default)
[![GitHub Release](https://img.shields.io/github/v/release/brunopatuleia/ha_gemini_usage)](https://github.com/brunopatuleia/ha_gemini_usage/releases)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

A comprehensive custom Home Assistant integration that monitors your **Google Gemini API** quota, rate limits, and token usage in real-time — across **all available models**, discovered dynamically.

---

## 🌟 Key Features

* 🤖 **Dynamic Model Discovery**: Automatically discovers all models accessible to your API key (via `GET /v1beta/models`), refreshing hourly and generating per-model sensor entities on the fly.
* ⚡ **Live Rate-Limit Header Probing**: Probes models using a lightweight, minimal-token request to retrieve active sliding-window counters:
  * Requests per minute (RPM) remaining & limit
  * Tokens per minute (TPM) remaining & limit
  * Requests per day (RPD) remaining & limit
* ☁️ **Google Cloud Monitoring Quota Integration** *(Optional)*: Uses GCP Service Account credentials to query Cloud Monitoring metric time-series (`serviceruntime.googleapis.com`) for:
  * 1-hour rolling input tokens per model
  * 1-hour rolling output tokens per model
  * Quota ceiling and consumption percentage (`quota_used_pct`)
* 💻 **Local Antigravity / Gemini CLI Log Tracking** *(Optional)*: Scans local conversation transcript JSONL logs on your machine to aggregate:
  * Tokens consumed today (per-model and total)
  * Tokens consumed this month (per-model and total)
* 🛡️ **Fail-safe & Asynchronous Architecture**:
  * Individual poll intervals for each data source
  * Safe concurrency limiter (semaphore) to prevent rate-limit exhaustion during probing
  * Zero-block event loop execution using Home Assistant thread executor pools

---

## 📊 Sensor Entities

### 1. Cross-Model Aggregate Sensors (Always Available)

| Entity ID | Friendly Name | Unit | Description |
| :--- | :--- | :--- | :--- |
| `sensor.gemini_model_count` | Available Model Count | models | Total count of Gemini models currently accessible |
| `sensor.gemini_total_tokens_today` | Total Tokens Today | tokens | Combined local token usage recorded today |
| `sensor.gemini_total_tokens_this_month` | Total Tokens This Month | tokens | Combined local token usage recorded this calendar month |

### 2. Per-Model Dynamic Sensors (Generated per Model, e.g. `gemini_2_0_flash`)

| Entity ID Pattern | Friendly Name | Unit | Source |
| :--- | :--- | :--- | :--- |
| `sensor.gemini_{model}_rpm_remaining` | `{Model}` Req/Min Remaining | requests | Gemini API Headers |
| `sensor.gemini_{model}_rpm_limit` | `{Model}` Req/Min Limit | requests | Gemini API Headers |
| `sensor.gemini_{model}_tpm_remaining` | `{Model}` Tokens/Min Remaining | tokens | Gemini API Headers |
| `sensor.gemini_{model}_tpm_limit` | `{Model}` Tokens/Min Limit | tokens | Gemini API Headers |
| `sensor.gemini_{model}_rpd_remaining` | `{Model}` Req/Day Remaining | requests | Gemini API Headers |
| `sensor.gemini_{model}_rpd_limit` | `{Model}` Req/Day Limit | requests | Gemini API Headers |
| `sensor.gemini_{model}_tokens_input_1h` | `{Model}` Input Tokens (1h) | tokens | GCP Cloud Monitoring |
| `sensor.gemini_{model}_tokens_output_1h` | `{Model}` Output Tokens (1h) | tokens | GCP Cloud Monitoring |
| `sensor.gemini_{model}_quota_used_pct` | `{Model}` Quota Used % | % | GCP Cloud Monitoring |
| `sensor.gemini_{model}_tokens_today` | `{Model}` Tokens Today (Local) | tokens | Local Transcripts |
| `sensor.gemini_{model}_tokens_this_month` | `{Model}` Tokens This Month (Local) | tokens | Local Transcripts |

---

## 🚀 Installation

### Option 1: HACS (Recommended)
1. Ensure [HACS](https://hacs.xyz/) is installed in your Home Assistant instance.
2. In Home Assistant, navigate to **HACS** > **Integrations** > Three-dots menu (top right) > **Custom repositories**.
3. Add repository URL: `https://github.com/brunopatuleia/ha_gemini_usage`
4. Category: **Integration**
5. Click **Download**, then restart Home Assistant.

### Option 2: Manual Installation
1. Download the latest release from the [Releases](https://github.com/brunopatuleia/ha_gemini_usage/releases) page.
2. Copy the `custom_components/gemini_usage` directory into your Home Assistant `<config>/custom_components/` directory.
3. Restart Home Assistant.

---

## ⚙️ Configuration

1. In Home Assistant, go to **Settings** > **Devices & Services** > **Add Integration**.
2. Search for **Gemini Usage Monitor**.
3. Follow the 3-step configuration flow:

### Step 1: Gemini API Key (Required)
* Enter your Gemini API key from [Google AI Studio](https://aistudio.google.com/app/apikey).
* The integration verifies the key against `GET /v1beta/models` before proceeding.

### Step 2: Google Cloud Platform Service Account (Optional)
* If you link your Google Cloud project for quota monitoring:
  * **GCP Project ID**: Your Google Cloud project ID.
  * **Service Account JSON**: Paste the full contents of a GCP Service Account key with the `roles/monitoring.viewer` role.
* *Leave blank to skip GCP Cloud Monitoring.*

### Step 3: Local Brain Directory Path (Optional)
* If running Home Assistant on a machine that also runs Antigravity or local Gemini CLI tooling:
  * Specify the path to your brain directory (e.g. `C:\Users\<user>\.gemini\antigravity\brain`).
* *Leave blank to skip local transcript parsing.*

---

## 🔧 Options & Customization

Click **Configure** on the integration card to adjust settings at any time without removing the integration:
* **API Rate-Limit Probe Interval**: 30 – 3600 seconds (Default: 60s)
* **GCP Monitoring Poll Interval**: 60 – 3600 seconds (Default: 300s)
* **Local Transcript Scan Interval**: 30 – 3600 seconds (Default: 120s)
* Rotate API keys or update service account credentials on the fly.

---

## 📈 Example Dashboard Card

```yaml
type: entities
title: Google Gemini Usage
show_header_toggle: false
entities:
  - entity: sensor.gemini_model_count
    name: Accessible Models
  - type: section
    label: Live Flash Rate Limits
  - entity: sensor.gemini_2_0_flash_rpm_remaining
    name: RPM Remaining
  - entity: sensor.gemini_2_0_flash_tpm_remaining
    name: TPM Remaining
  - entity: sensor.gemini_2_0_flash_rpd_remaining
    name: Daily Requests Remaining
  - type: section
    label: Local Usage Stats
  - entity: sensor.gemini_total_tokens_today
    name: Tokens Today
  - entity: sensor.gemini_total_tokens_this_month
    name: Tokens This Month
```

---

## 📄 License

This project is licensed under the [MIT License](LICENSE).
