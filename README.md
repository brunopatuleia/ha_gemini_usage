# Antigravity & Gemini Usage Monitor for Home Assistant 🚀

> [!IMPORTANT]
> 🍸 **100% Vibe Coded — Zero Human-Written Code**:  
> The human author ([@brunopatuleia](https://github.com/brunopatuleia)) conceived the idea, directed the design, and tested the integration while relaxing and having drinks — but **did not write a single line of code**.
> 
> 🤖 **AI Agents & Models Used**:
> * **Google DeepMind Antigravity**: Autonomous agent platform, CLI orchestration, and local daemon integration.
> * **Google Gemini**: Gemini 3.8 Flash (High), Gemini 2.5 Flash, and Gemini 2.5 Pro.
> * **Anthropic Claude**: Claude 3.5 Sonnet & Claude 3.7 Sonnet (via Antigravity).
> 
> Every single line of code — from architecture, Home Assistant async coordinators, daemon reverse-engineering, REST state synchronization, and systemd units to Lovelace cards and documentation — was **100% generated, debugged, and implemented by AI**. Use at your own discretion, inspect with curiosity, and community PRs / improvements are warmly welcomed!

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://github.com/hacs/default)
[![GitHub Release](https://img.shields.io/github/v/release/brunopatuleia/ha_gemini_usage)](https://github.com/brunopatuleia/ha_gemini_usage/releases)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

<p align="center">
  <img src="images/dashboard.png" alt="Antigravity Home Assistant Dashboard" width="850">
</p>

A real-time monitor for **Google Antigravity** compute limits and **Google Gemini API** quotas in Home Assistant.

---

## 🌟 What Does This Monitor?

### 1. Antigravity Compute Quotas (No Google API key required!)
Monitors your active **Google Antigravity** dual-tier rolling compute capacity:
* **Gemini Models**: Weekly Limit & 5-Hour rolling refresh window
* **Claude & GPT Models**: Weekly Limit & 5-Hour rolling refresh window
* **Live Reset Timers**: Countdown to reset (e.g., `Resets in 2d 19h` or `3h 16m`)
* **Two Display Styles**: 
  * **Quota Used (Increasing)**: 0% $\rightarrow$ 100% (fills up like CPU/RAM usage)
  * **Quota Remaining (Decreasing)**: 100% $\rightarrow$ 0% (depletes like a battery)

### 2. Google AI Studio Developer API *(Optional HACS Component)*
For developers using raw Gemini API keys (`AIzaSy...`):
* Dynamic model catalogue discovery (`GET /v1beta/models`)
* Model context windows and token output ceilings
* API health and round-trip HTTP latency

---

## ⚡ Quick Start: Antigravity Real-Time Quota Monitor

You **do not need HACS** to use the Antigravity Quota Monitor. It runs on the computer where Antigravity is installed and streams live sensors directly into Home Assistant via its native REST API.

### Step 1: Get a Home Assistant Long-Lived Access Token
1. Open your Home Assistant dashboard in your browser.
2. Click your **User Profile** icon in the bottom-left corner.
3. Select the **Security** tab (or scroll down to **Long-Lived Access Tokens**).
4. Click **Create Token**, name it `Antigravity`, and copy the token string (`eyJhbGciOi...`).

### Step 2: Configure Your Environment
1. Clone this repository on the machine running Antigravity:
   ```bash
   git clone https://github.com/brunopatuleia/ha_gemini_usage.git
   cd ha_gemini_usage
   ```
2. Copy the example configuration file:
   ```bash
   cp .env.example .env
   ```
3. Edit `.env` with your Home Assistant details:
   ```ini
   # Home Assistant URL (local IP, homeassistant.local, or Nabu Casa)
   HA_URL=http://192.168.1.240:8123

   # Paste the Long-Lived Access Token created in Step 1
   HA_TOKEN=eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...

   # Sync interval in seconds (default: 60)
   SYNC_INTERVAL=60
   ```

### Step 3: Run the Sync Service

#### Test it once:
```bash
python3 sync_antigravity_quota.py --once
```
You should see:
```text
[INFO] Successfully synced 4 Antigravity quota sensors to Home Assistant.
```

#### Run permanently as a background service (Linux / systemd):
```bash
# Copy service definition to your user systemd directory
mkdir -p ~/.config/systemd/user
cp antigravity-ha-sync.service ~/.config/systemd/user/

# Enable and start service
systemctl --user daemon-reload
systemctl --user enable antigravity-ha-sync.service
systemctl --user start antigravity-ha-sync.service
```

---

## 📊 Live Home Assistant Entities

The sync service automatically creates and updates these sensors:

| Entity ID | Friendly Name | Default Metric | Description |
| :--- | :--- | :---: | :--- |
| `sensor.antigravity_gemini_weekly` | Gemini Models — Weekly Quota Used | **Used %** | Weekly compute used by Gemini Flash / Pro |
| `sensor.antigravity_gemini_5h` | Gemini Models — Five Hour Quota Used | **Used %** | 5-Hour rolling compute used by Gemini |
| `sensor.antigravity_claude_gpt_weekly` | Claude & GPT — Weekly Quota Used | **Used %** | Weekly compute used by Claude Opus / Sonnet / GPT |
| `sensor.antigravity_claude_gpt_5h` | Claude & GPT — Five Hour Quota Used | **Used %** | 5-Hour rolling compute used by Claude / GPT |
| `sensor.antigravity_quota_status` | Antigravity Quota Status | `Online` | Connection status and heartbeat |

> [!TIP]
> **Both metrics are always available!**  
> If you prefer explicit entity names:
> * `sensor.antigravity_gemini_weekly_used` reports **Used %** (e.g. `20%`).
> * `sensor.antigravity_gemini_weekly_remaining` reports **Remaining %** (e.g. `80%`).
> * Every entity also contains `used_pct`, `remaining_pct`, `resets_in`, and `reset_time` in its attributes.

---

## 📈 Home Assistant Dashboard Cards

### 1. Visual Circular Gauges (Recommended)
Matches standard severity thresholds (Green when usage is low, Red when near quota):

```yaml
type: vertical-stack
title: Antigravity AI Compute Quotas
cards:
  - type: grid
    title: Gemini Models
    columns: 2
    square: false
    cards:
      - type: gauge
        entity: sensor.antigravity_gemini_weekly
        name: Weekly Limit
        min: 0
        max: 100
        severity:
          green: 0
          yellow: 70
          red: 90
      - type: gauge
        entity: sensor.antigravity_gemini_5h
        name: 5-Hour Limit
        min: 0
        max: 100
        severity:
          green: 0
          yellow: 70
          red: 90

  - type: grid
    title: Claude & GPT Models
    columns: 2
    square: false
    cards:
      - type: gauge
        entity: sensor.antigravity_claude_gpt_weekly
        name: Weekly Limit
        min: 0
        max: 100
        severity:
          green: 0
          yellow: 70
          red: 90
      - type: gauge
        entity: sensor.antigravity_claude_gpt_5h
        name: 5-Hour Limit
        min: 0
        max: 100
        severity:
          green: 0
          yellow: 70
          red: 90
```

### 2. Compact List with Reset Timers
Displays real-time countdowns (`resets_in`) under each model:

```yaml
type: entities
title: Antigravity Usage
show_header_toggle: false
entities:
  - type: section
    label: Gemini Models
  - entity: sensor.antigravity_gemini_weekly
    name: Weekly Quota
    secondary_info: attribute
    attribute: resets_in
  - entity: sensor.antigravity_gemini_5h
    name: 5-Hour Rolling
    secondary_info: attribute
    attribute: resets_in

  - type: section
    label: Claude & GPT Models
  - entity: sensor.antigravity_claude_gpt_weekly
    name: Weekly Quota
    secondary_info: attribute
    attribute: resets_in
  - entity: sensor.antigravity_claude_gpt_5h
    name: 5-Hour Rolling
    secondary_info: attribute
    attribute: resets_in
```

---

## 🔌 Google AI Studio HACS Integration *(Optional)*

If you also develop apps with a direct Google Gemini API Key:

1. In Home Assistant, open **HACS** > **Integrations** > **Custom repositories**.
2. Add: `https://github.com/brunopatuleia/ha_gemini_usage` (Category: **Integration**).
3. Download and restart Home Assistant.
4. Go to **Settings > Devices & Services > Add Integration > Gemini Usage Monitor**.
5. Paste your Gemini API key from [Google AI Studio](https://aistudio.google.com/app/apikey).

---

## ❓ Frequently Asked Questions (FAQ)

#### Do I need a Google AI Studio API key for Antigravity quotas?
**No.** Antigravity compute quotas are tied to your Google subscription and managed by the Antigravity CLI (`agy`). Once you are logged into `agy` on your computer, the sync daemon reads the quotas automatically without any API key.

#### Does Home Assistant need to run on the same computer as Antigravity?
**No.** As long as your computer can reach your Home Assistant instance over the local network (or via VPN / Nabu Casa), the script will push updates seamlessly.

#### How do I change the update interval?
Set `SYNC_INTERVAL=<seconds>` in your `.env` file (e.g. `SYNC_INTERVAL=30` for 30-second refreshes) and restart the service.

#### Can I purge stale sensors from earlier test runs?
Yes! Run:
```bash
python3 sync_antigravity_quota.py --cleanup-ghosts --once
```
This safely removes unavailable ghost sensors from previous test configurations.

---

## 📄 License

This project is licensed under the [MIT License](LICENSE).
