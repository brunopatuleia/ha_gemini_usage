#!/usr/bin/env python3
"""
Antigravity Quota to Home Assistant Real-Time Sync Service.

Queries the local Antigravity CLI/daemon for active compute quotas
(Gemini Models and Claude & GPT models: Weekly & 5-Hour limits) and publishes
live sensors to Home Assistant via its REST API.

Hardened for 24/7 daemon reliability with HTTP connection pooling,
circuit-breaker fail-fast on HA restarts, systemd watchdog support,
and graceful signal handling.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    import requests
except ImportError:
    requests = None
import urllib.error
import urllib.request

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
_LOGGER = logging.getLogger("antigravity_ha_sync")

DEFAULT_INTERVAL = 60  # seconds
VERSION = "1.1.0"
SHUTDOWN_EVENT = threading.Event()

ENV_PATHS = [
    Path(__file__).parent / ".env",
    Path.home() / ".config" / "antigravity-ha-sync" / ".env",
]

# Static bucket configuration mappings
BUCKET_DEFINITIONS: dict[str, dict[str, str]] = {
    "gemini-weekly": {
        "base_id": "sensor.antigravity_gemini_weekly",
        "name_used": "Gemini Models — Weekly Quota Used",
        "name_remaining": "Gemini Models — Weekly Limit Remaining",
        "icon": "mdi:google",
        "group": "Gemini Models",
    },
    "gemini-5h": {
        "base_id": "sensor.antigravity_gemini_5h",
        "name_used": "Gemini Models — Five Hour Quota Used",
        "name_remaining": "Gemini Models — Five Hour Limit Remaining",
        "icon": "mdi:clock-fast",
        "group": "Gemini Models",
    },
    "3p-weekly": {
        "base_id": "sensor.antigravity_claude_gpt_weekly",
        "name_used": "Claude & GPT — Weekly Quota Used",
        "name_remaining": "Claude & GPT — Weekly Limit Remaining",
        "icon": "mdi:creation-outline",
        "group": "Claude and GPT models",
    },
    "3p-5h": {
        "base_id": "sensor.antigravity_claude_gpt_5h",
        "name_used": "Claude & GPT — Five Hour Quota Used",
        "name_remaining": "Claude & GPT — Five Hour Limit Remaining",
        "icon": "mdi:clock-fast",
        "group": "Claude and GPT models",
    },
}


# ---------------------------------------------------------------------------
# Systemd Watchdog Notification Helper
# ---------------------------------------------------------------------------

def sd_notify(state: str) -> bool:
    """Send state notification (e.g. READY=1, WATCHDOG=1) to systemd notify socket."""
    notify_socket = os.environ.get("NOTIFY_SOCKET")
    if not notify_socket:
        return False
    try:
        if notify_socket.startswith("@"):
            notify_socket = "\0" + notify_socket[1:]
        with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as sock:
            sock.connect(notify_socket)
            sock.sendall(state.encode("utf-8"))
        return True
    except Exception as exc:
        _LOGGER.debug("sd_notify failed: %s", exc)
        return False


# ---------------------------------------------------------------------------
# Configuration & Binary Resolution
# ---------------------------------------------------------------------------

def load_env_file() -> dict[str, str]:
    """Parse key=value pairs from standard .env locations if present."""
    env_vars = {}
    for p in ENV_PATHS:
        if p.is_file():
            try:
                with open(p, "r", encoding="utf-8") as fh:
                    for line in fh:
                        line = line.strip()
                        if line and not line.startswith("#") and "=" in line:
                            k, v = line.split("=", 1)
                            env_vars[k.strip()] = v.strip().strip("'\"")
            except Exception as exc:
                _LOGGER.debug("Could not read %s: %s", p, exc)
    return env_vars


def get_config() -> tuple[str, str, int]:
    """Resolve Home Assistant URL, Long-Lived Token, and sync interval."""
    env_file_vars = load_env_file()

    ha_url = (
        os.environ.get("HA_URL")
        or env_file_vars.get("HA_URL")
        or "http://homeassistant.local:8123"
    ).rstrip("/")

    ha_token = (
        os.environ.get("HA_TOKEN")
        or env_file_vars.get("HA_TOKEN")
        or ""
    )

    interval = int(
        os.environ.get("SYNC_INTERVAL")
        or env_file_vars.get("SYNC_INTERVAL")
        or DEFAULT_INTERVAL
    )

    return ha_url, ha_token, interval


def find_agy_binary() -> str:
    """Resolve the agy binary path across standard user and system paths."""
    env_path = os.environ.get("AGY_PATH")
    if env_path and os.path.isfile(env_path) and os.access(env_path, os.X_OK):
        return env_path

    which_path = shutil.which("agy")
    if which_path:
        return which_path

    for candidate in [
        Path.home() / ".local" / "bin" / "agy",
        Path("/usr/local/bin/agy"),
        Path("/usr/bin/agy"),
    ]:
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)

    return "agy"


def format_resets_in(reset_time_iso: str) -> str:
    """Compute human-friendly relative reset duration like '2d 19h' or '3h 22m'."""
    try:
        target = datetime.fromisoformat(reset_time_iso.replace("Z", "+00:00"))
        if target.tzinfo is None:
            target = target.replace(tzinfo=timezone.utc)
        now = datetime.now(timezone.utc)
        diff = target - now
        total_seconds = int(diff.total_seconds())

        if total_seconds <= 0:
            return "Now"

        days = total_seconds // 86400
        hours = (total_seconds % 86400) // 3600
        minutes = (total_seconds % 3600) // 60

        if days > 0:
            return f"{days}d {hours}h"
        if hours > 0:
            return f"{hours}h {minutes}m"
        return f"{minutes}m"
    except Exception:
        return "Unknown"


# ---------------------------------------------------------------------------
# CLI Execution & Robust JSON Extraction
# ---------------------------------------------------------------------------

def fetch_quota_from_agy() -> dict[str, Any] | None:
    """Execute agy CLI and parse quota JSON safely."""
    agy_bin = find_agy_binary()
    cmd = [agy_bin, "--print", "/quota", "--output-format", "json"]
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=15,
            check=True,
        )
        raw_output = result.stdout.strip()
        start_idx = raw_output.find("{")
        end_idx = raw_output.rfind("}")
        if start_idx != -1 and end_idx != -1:
            raw_output = raw_output[start_idx : end_idx + 1]

        data = json.loads(raw_output)
        if data.get("status") and data.get("status") != "SUCCESS":
            _LOGGER.error("agy returned non-success status '%s': %s", data.get("status"), data.get("response"))
            return None
        return data

    except FileNotFoundError:
        _LOGGER.error("Executable '%s' not found. Set AGY_PATH or ensure agy is in PATH.", agy_bin)
    except subprocess.CalledProcessError as err:
        err_msg = (err.stderr or err.stdout or "No error output").strip()
        _LOGGER.error("agy exited with error (code %d): %s", err.returncode, err_msg)
    except subprocess.TimeoutExpired:
        _LOGGER.error("Timeout fetching quota from agy CLI (>15s)")
    except json.JSONDecodeError as err:
        _LOGGER.error("Failed to decode JSON from agy output: %s", err)
    except Exception as exc:
        _LOGGER.error("Unexpected error querying agy: %s", exc)
    return None


# ---------------------------------------------------------------------------
# Resilient Home Assistant HTTP Client with Connection Pooling & Circuit Breaker
# ---------------------------------------------------------------------------

class HomeAssistantUnreachableError(Exception):
    """Raised when Home Assistant connection fails (host down or network timeout)."""


class HomeAssistantAuthError(Exception):
    """Raised when Home Assistant returns HTTP 401 or 403."""


class HomeAssistantClient:
    """Resilient HTTP client for Home Assistant with Keep-Alive connection pooling."""

    def __init__(self, base_url: str, token: str):
        self.base_url = base_url.rstrip("/")
        if not self.base_url.startswith(("http://", "https://")):
            self.base_url = f"http://{self.base_url}"
        self.token = token
        self._session = None

        if requests is not None:
            self._session = requests.Session()
            self._session.headers.update({
                "Authorization": f"Bearer {self.token}",
                "Content-Type": "application/json",
            })

    def post_state(self, entity_id: str, state: str | int | float, attributes: dict[str, Any]) -> bool:
        """POST entity state update to Home Assistant."""
        url = f"{self.base_url}/api/states/{entity_id}"
        payload = {"state": str(state), "attributes": attributes}

        if self._session is not None:
            try:
                resp = self._session.post(url, json=payload, timeout=6)
                if resp.status_code in (200, 201):
                    return True
                if resp.status_code in (401, 403):
                    raise HomeAssistantAuthError(f"HTTP {resp.status_code} Unauthorized")
                _LOGGER.error("HTTP error %d posting to %s: %s", resp.status_code, entity_id, resp.text[:200])
                return False
            except (requests.ConnectionError, requests.Timeout) as exc:
                raise HomeAssistantUnreachableError(str(exc)) from exc
            except requests.RequestException as exc:
                _LOGGER.error("Failed to post to HA %s: %s", entity_id, exc)
                return False
        else:
            data = json.dumps(payload).encode("utf-8")
            headers = {"Authorization": f"Bearer {self.token}", "Content-Type": "application/json"}
            req = urllib.request.Request(url, data=data, headers=headers, method="POST")
            try:
                with urllib.request.urlopen(req, timeout=6) as resp:
                    return resp.status in (200, 201)
            except urllib.error.HTTPError as exc:
                if exc.code in (401, 403):
                    raise HomeAssistantAuthError(f"HTTP {exc.code} Unauthorized") from exc
                body = exc.read().decode("utf-8", errors="replace")
                _LOGGER.error("HTTP error %d posting to %s: %s", exc.code, entity_id, body[:200])
                return False
            except urllib.error.URLError as exc:
                raise HomeAssistantUnreachableError(str(exc.reason)) from exc
            except Exception as exc:
                _LOGGER.error("Failed to post to HA %s: %s", entity_id, exc)
                return False

    def post_webhook(self, payload: dict[str, Any]) -> bool:
        """POST full quota payload to the custom component's webhook endpoint."""
        url = f"{self.base_url}/api/webhook/gemini_usage_antigravity_quota"
        if self._session is not None:
            try:
                resp = self._session.post(url, json=payload, timeout=6)
                return resp.status_code in (200, 204)
            except (requests.ConnectionError, requests.Timeout) as exc:
                raise HomeAssistantUnreachableError(str(exc)) from exc
            except Exception as exc:
                _LOGGER.debug("Webhook post failed: %s", exc)
                return False
        else:
            data = json.dumps(payload).encode("utf-8")
            req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"}, method="POST")
            try:
                with urllib.request.urlopen(req, timeout=6) as resp:
                    return resp.status in (200, 204)
            except urllib.error.URLError as exc:
                raise HomeAssistantUnreachableError(str(exc.reason)) from exc
            except Exception as exc:
                _LOGGER.debug("Webhook post failed: %s", exc)
                return False

    def delete_entity(self, entity_id: str) -> bool:
        """Delete an entity from Home Assistant states."""
        url = f"{self.base_url}/api/states/{entity_id}"
        if self._session is not None:
            try:
                resp = self._session.delete(url, timeout=6)
                return resp.status_code in (200, 201, 204)
            except Exception:
                return False
        else:
            req = urllib.request.Request(url, headers={"Authorization": f"Bearer {self.token}"}, method="DELETE")
            try:
                with urllib.request.urlopen(req, timeout=6) as resp:
                    return resp.status in (200, 201, 204)
            except Exception:
                return False


def cleanup_vibe_coded_ghost_sensors(client: HomeAssistantClient) -> int:
    """Find and clean up unavailable ghost sensors from old vibe-coded sessions."""
    url = f"{client.base_url}/api/states"
    states = []
    if client._session is not None:
        try:
            resp = client._session.get(url, timeout=10)
            if resp.status_code == 200:
                states = resp.json()
        except Exception as exc:
            _LOGGER.error("Failed to fetch states from HA for cleanup: %s", exc)
            return 0
    else:
        req = urllib.request.Request(url, headers={"Authorization": f"Bearer {client.token}"})
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                states = json.loads(resp.read().decode())
        except Exception as exc:
            _LOGGER.error("Failed to fetch states from HA for cleanup: %s", exc)
            return 0

    to_delete = [
        s["entity_id"]
        for s in states
        if (
            s["entity_id"].startswith("sensor.gemini_usage_monitor_")
            or s["entity_id"].startswith("sensor.server_room_gemini_usage_monitor_")
        )
        and s.get("state") in ("unavailable", "unknown")
    ]

    count = 0
    for entity_id in to_delete:
        if client.delete_entity(entity_id):
            count += 1
            _LOGGER.info("Deleted stale ghost entity: %s", entity_id)

    return count


# ---------------------------------------------------------------------------
# Core Synchronization Logic with Circuit Breaker
# ---------------------------------------------------------------------------

def sync_once(client: HomeAssistantClient) -> bool:
    """Fetch current quota and publish all sensors with fail-fast circuit breaker."""
    payload = fetch_quota_from_agy()
    now_iso = datetime.now(timezone.utc).isoformat()

    if not payload:
        try:
            client.post_state(
                "sensor.antigravity_quota_status",
                "Offline",
                {
                    "friendly_name": "Antigravity Quota Status",
                    "icon": "mdi:cloud-alert",
                    "last_error": "Failed to query agy CLI",
                    "timestamp": now_iso,
                },
            )
        except HomeAssistantUnreachableError:
            pass
        return False

    # 1. Push payload to Home Assistant Custom Component webhook
    try:
        client.post_webhook(payload)
    except HomeAssistantUnreachableError as exc:
        _LOGGER.warning("Home Assistant is unreachable (%s). Circuit breaker activated.", exc)
        return False

    groups = payload.get("command", {}).get("data", {}).get("groups", [])
    if not groups:
        _LOGGER.warning("No quota groups found in agy response")
        return False

    synced_count = 0
    try:
        for group in groups:
            group_name = group.get("name", "")
            group_desc = group.get("description", "")
            for bucket in group.get("buckets", []):
                bucket_id = bucket.get("id", "")
                cfg = BUCKET_DEFINITIONS.get(bucket_id)
                if not cfg:
                    continue

                # Null-safe extraction and bounds clamping
                rem_val = bucket.get("remaining_fraction")
                try:
                    rem_frac = max(0.0, min(1.0, float(rem_val))) if rem_val is not None else 0.0
                except (ValueError, TypeError):
                    rem_frac = 0.0

                used_frac = max(0.0, min(1.0, 1.0 - rem_frac))
                rem_pct = round(rem_frac * 100.0, 1)
                used_pct = round(used_frac * 100.0, 1)
                int_rem_pct = round(rem_frac * 100.0)
                int_used_pct = round(used_frac * 100.0)

                reset_time = bucket.get("reset_time", "")
                resets_in = format_resets_in(reset_time) if reset_time else "Unknown"
                desc = bucket.get("description") or group_desc

                # Predictive analytics
                window_str = bucket.get("window", "")
                window_seconds = 5 * 3600 if "5h" in window_str or "5h" in bucket_id else 7 * 86400

                time_to_reset_seconds: int | None = None
                if reset_time:
                    try:
                        target_dt = datetime.fromisoformat(reset_time.replace("Z", "+00:00"))
                        if target_dt.tzinfo is None:
                            target_dt = target_dt.replace(tzinfo=timezone.utc)
                        now_dt = datetime.now(timezone.utc)
                        time_to_reset_seconds = max(0, int((target_dt - now_dt).total_seconds()))
                    except Exception:
                        time_to_reset_seconds = None

                if time_to_reset_seconds is not None:
                    elapsed_seconds = max(0, window_seconds - time_to_reset_seconds)
                    elapsed_hours = elapsed_seconds / 3600.0
                    hours_to_reset = time_to_reset_seconds / 3600.0

                    # Observation window guard (at least 15 mins or 2% of total window)
                    min_obs_hours = max(0.25, (window_seconds * 0.02) / 3600.0)
                    if elapsed_hours >= min_obs_hours and used_pct > 0:
                        burn_rate_pct_per_h = round(used_pct / elapsed_hours, 2)
                    else:
                        burn_rate_pct_per_h = 0.0
                else:
                    elapsed_hours = 0.0
                    hours_to_reset = 0.0
                    burn_rate_pct_per_h = 0.0

                if time_to_reset_seconds is not None and hours_to_reset > 0 and burn_rate_pct_per_h > 0:
                    projected_pct = round(min(500.0, used_pct + (burn_rate_pct_per_h * hours_to_reset)), 1)
                else:
                    projected_pct = used_pct

                if burn_rate_pct_per_h > 0 and rem_frac > 0:
                    runway_hours = rem_pct / burn_rate_pct_per_h
                    total_runway_sec = int(runway_hours * 3600)
                    r_days = total_runway_sec // 86400
                    r_hrs = (total_runway_sec % 86400) // 3600
                    r_mins = (total_runway_sec % 3600) // 60
                    if r_days > 0:
                        runway_str = f"{r_days}d {r_hrs}h"
                    elif r_hrs > 0:
                        runway_str = f"{r_hrs}h {r_mins}m"
                    else:
                        runway_str = f"{r_mins}m"
                elif rem_frac <= 0 or used_pct >= 100:
                    runway_hours = 0.0
                    runway_str = "0m (Exhausted)"
                else:
                    runway_hours = 999.0
                    runway_str = "Sufficient"

                if rem_frac <= 0 or used_pct >= 100:
                    pace_status = "Exhausted"
                    is_over_quota_risk = True
                elif time_to_reset_seconds is not None and time_to_reset_seconds > 0 and projected_pct > 100.0:
                    pace_status = "High Risk (Over Quota)"
                    is_over_quota_risk = True
                elif projected_pct > 80.0:
                    pace_status = "Elevated"
                    is_over_quota_risk = False
                else:
                    pace_status = "Safe"
                    is_over_quota_risk = False

                if hours_to_reset >= 0.05:
                    safe_pace_pct_per_h = round(min(100.0, rem_pct / hours_to_reset), 2)
                elif rem_frac <= 0:
                    safe_pace_pct_per_h = 0.0
                else:
                    safe_pace_pct_per_h = 100.0

                base_id = cfg["base_id"]
                attrs_common = {
                    "bucket_id": bucket_id,
                    "group": group_name,
                    "description": desc,
                    "window": bucket.get("window", ""),
                    "reset_time": reset_time,
                    "resets_in": resets_in,
                    "burn_rate_pct_per_h": burn_rate_pct_per_h,
                    "projected_pct": projected_pct,
                    "runway": runway_str,
                    "runway_hours": round(runway_hours, 2),
                    "pace_status": pace_status,
                    "is_over_quota_risk": is_over_quota_risk,
                    "safe_pace_pct_per_h": safe_pace_pct_per_h,
                    "last_synced": now_iso,
                    "version": VERSION,
                }

                # 1. Primary sensor (Used %)
                attrs_primary = dict(attrs_common)
                attrs_primary.update({
                    "friendly_name": cfg["name_used"],
                    "unit_of_measurement": "%",
                    "icon": cfg["icon"],
                    "remaining_fraction": rem_frac,
                    "used_fraction": round(used_frac, 4),
                    "remaining_pct": rem_pct,
                    "used_pct": used_pct,
                })
                client.post_state(base_id, int_used_pct, attrs_primary)
                synced_count += 1

                # 2. Dedicated Used sensor
                attrs_used = dict(attrs_common)
                attrs_used.update({
                    "friendly_name": f"{cfg['name_used']} (Used)",
                    "unit_of_measurement": "%",
                    "icon": cfg["icon"],
                    "used_fraction": round(used_frac, 4),
                    "used_pct": used_pct,
                })
                client.post_state(f"{base_id}_used", int_used_pct, attrs_used)
                synced_count += 1

                # 3. Dedicated Remaining sensor
                attrs_rem = dict(attrs_common)
                attrs_rem.update({
                    "friendly_name": f"{cfg['name_remaining']} (Remaining)",
                    "unit_of_measurement": "%",
                    "icon": cfg["icon"],
                    "remaining_fraction": rem_frac,
                    "remaining_pct": rem_pct,
                })
                client.post_state(f"{base_id}_remaining", int_rem_pct, attrs_rem)
                synced_count += 1

                # 4. Resets In sensor
                client.post_state(
                    f"{base_id}_resets_in",
                    resets_in,
                    {
                        "friendly_name": f"{cfg['name_used']} (Resets In)",
                        "icon": "mdi:clock-outline",
                        "reset_time": reset_time,
                        "last_synced": now_iso,
                    },
                )
                synced_count += 1

                # 5. Burn Rate sensor
                client.post_state(
                    f"{base_id}_burn_rate",
                    burn_rate_pct_per_h,
                    {
                        "friendly_name": f"{group_name} ({bucket.get('window', bucket_id)}) Burn Rate",
                        "unit_of_measurement": "%/h",
                        "icon": "mdi:fire",
                        "bucket_id": bucket_id,
                        "safe_pace_pct_per_h": safe_pace_pct_per_h,
                        "last_synced": now_iso,
                    },
                )
                synced_count += 1

                # 6. Projected Usage sensor
                client.post_state(
                    f"{base_id}_projected",
                    projected_pct,
                    {
                        "friendly_name": f"{group_name} ({bucket.get('window', bucket_id)}) Projected Usage",
                        "unit_of_measurement": "%",
                        "icon": "mdi:chart-timeline-variant",
                        "bucket_id": bucket_id,
                        "last_synced": now_iso,
                    },
                )
                synced_count += 1

                # 7. Runway sensor
                client.post_state(
                    f"{base_id}_runway",
                    runway_str,
                    {
                        "friendly_name": f"{group_name} ({bucket.get('window', bucket_id)}) Runway",
                        "icon": "mdi:timer-sand",
                        "bucket_id": bucket_id,
                        "runway_hours": round(runway_hours, 2),
                        "last_synced": now_iso,
                    },
                )
                synced_count += 1

                # 8. Pace Status sensor
                client.post_state(
                    f"{base_id}_pace",
                    pace_status,
                    {
                        "friendly_name": f"{group_name} ({bucket.get('window', bucket_id)}) Pace Status",
                        "icon": "mdi:speedometer",
                        "bucket_id": bucket_id,
                        "last_synced": now_iso,
                    },
                )
                synced_count += 1

                # 9. Over-Quota Risk Problem Binary Sensor
                client.post_state(
                    base_id.replace("sensor.", "binary_sensor.") + "_over_quota_risk",
                    "on" if is_over_quota_risk else "off",
                    {
                        "friendly_name": f"{group_name} ({bucket.get('window', bucket_id)}) Over-Quota Risk",
                        "device_class": "problem",
                        "icon": "mdi:alert-circle" if is_over_quota_risk else "mdi:check-circle-outline",
                        "bucket_id": bucket_id,
                        "projected_pct": projected_pct,
                        "last_synced": now_iso,
                    },
                )
                synced_count += 1

        # Overarching health sensor
        client.post_state(
            "sensor.antigravity_quota_status",
            "Online",
            {
                "friendly_name": "Antigravity Quota Status",
                "icon": "mdi:cloud-check",
                "synced_sensors": synced_count,
                "last_synced": now_iso,
                "version": VERSION,
            },
        )

        # Integration version sensor
        client.post_state(
            "sensor.antigravity_version",
            VERSION,
            {
                "friendly_name": "Antigravity Integration Version",
                "icon": "mdi:tag-outline",
                "installed_version": VERSION,
                "last_synced": now_iso,
            },
        )

    except HomeAssistantUnreachableError as exc:
        _LOGGER.warning("Home Assistant became unreachable during sync (%s). Aborting remaining updates.", exc)
        return False

    _LOGGER.info("Successfully synced %d Antigravity quota sensors to Home Assistant.", synced_count)
    return synced_count > 0


# ---------------------------------------------------------------------------
# Signal Handling & Daemon Entrypoint
# ---------------------------------------------------------------------------

def _sig_handler(signum: int, frame: Any) -> None:
    sig_name = signal.Signals(signum).name
    _LOGGER.info("Received %s. Initiating clean graceful shutdown...", sig_name)
    SHUTDOWN_EVENT.set()


def main() -> None:
    """CLI entrypoint."""
    signal.signal(signal.SIGTERM, _sig_handler)
    signal.signal(signal.SIGINT, _sig_handler)

    parser = argparse.ArgumentParser(description="Sync Antigravity Quotas to Home Assistant")
    parser.add_argument("--version", action="version", version=f"%(prog)s {VERSION}")
    parser.add_argument("--ha-url", help="Home Assistant base URL")
    parser.add_argument("--ha-token", help="Home Assistant Long-Lived Access Token")
    parser.add_argument("--interval", type=int, help="Sync interval in seconds (default 60)")
    parser.add_argument("--once", action="store_true", help="Run sync once and exit")
    parser.add_argument(
        "--cleanup-ghosts",
        action="store_true",
        help="Purge unavailable sensors left over from old vibe-coded sessions",
    )
    args = parser.parse_args()

    default_url, default_token, default_interval = get_config()
    ha_url = (args.ha_url or default_url).rstrip("/")
    ha_token = args.ha_token or default_token
    interval = args.interval or default_interval

    if not ha_token:
        _LOGGER.error("Missing HA_TOKEN. Provide via env or .env file.")
        sys.exit(1)

    client = HomeAssistantClient(ha_url, ha_token)

    if args.cleanup_ghosts:
        _LOGGER.info("Cleaning up unavailable ghost entities from previous sessions...")
        deleted = cleanup_vibe_coded_ghost_sensors(client)
        _LOGGER.info("Cleanup complete. Removed %d ghost entities.", deleted)

    if args.once:
        success = sync_once(client)
        sys.exit(0 if success else 1)

    sd_notify("READY=1")
    _LOGGER.info(
        "Starting Antigravity -> Home Assistant quota sync daemon (interval: %ds, target: %s)",
        interval,
        ha_url,
    )

    ha_was_offline = False

    while not SHUTDOWN_EVENT.is_set():
        cycle_start = time.monotonic()
        try:
            success = sync_once(client)
            if success and ha_was_offline:
                _LOGGER.info("Home Assistant connection restored.")
                ha_was_offline = False
            elif not success and not ha_was_offline:
                ha_was_offline = True
        except HomeAssistantAuthError as err:
            _LOGGER.critical("Authentication failed: %s. Pausing for 300s.", err)
            for _ in range(30):
                if SHUTDOWN_EVENT.wait(10):
                    break
                sd_notify("WATCHDOG=1")
            continue
        except Exception as exc:
            _LOGGER.error("Error during sync cycle: %s", exc)

        sd_notify("WATCHDOG=1")

        elapsed = time.monotonic() - cycle_start
        sleep_duration = max(1.0, interval - elapsed)
        SHUTDOWN_EVENT.wait(timeout=sleep_duration)

    sd_notify("STOPPING=1")
    _LOGGER.info("Antigravity sync daemon stopped cleanly.")


if __name__ == "__main__":
    main()
