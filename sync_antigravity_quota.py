#!/usr/bin/env python3
"""
Antigravity Quota to Home Assistant Real-Time Sync Service.

Queries the local Antigravity CLI/daemon for active compute quotas
(Gemini Models and Claude & GPT models: Weekly & 5-Hour limits) and publishes
live sensors to Home Assistant via its REST API.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
import urllib.error
import urllib.request

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
_LOGGER = logging.getLogger("antigravity_ha_sync")

DEFAULT_INTERVAL = 60  # seconds
ENV_PATHS = [
    Path.cwd() / ".env",
    Path(__file__).parent / ".env",
    Path.home() / ".config" / "antigravity-ha-sync" / ".env",
]


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


def format_resets_in(reset_time_iso: str) -> str:
    """Compute human-friendly relative reset duration like '2d 19h' or '3h 22m'."""
    try:
        # Expected format: "2026-10-16T17:22:03Z"
        target = datetime.fromisoformat(reset_time_iso.replace("Z", "+00:00"))
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


def fetch_quota_from_agy() -> dict[str, Any] | None:
    """Run agy --print '/quota' --output-format json and return the parsed JSON payload."""
    try:
        cmd = ["agy", "--print", "/quota", "--output-format", "json"]
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=15,
            check=True,
        )
        data = json.loads(result.stdout)
        return data
    except subprocess.CalledProcessError as err:
        _LOGGER.error("agy exited with error (code %d): %s", err.returncode, err.stderr.strip())
    except subprocess.TimeoutExpired:
        _LOGGER.error("Timeout fetching quota from agy CLI (>15s)")
    except json.JSONDecodeError as err:
        _LOGGER.error("Failed to decode JSON from agy output: %s", err)
    except Exception as exc:
        _LOGGER.error("Unexpected error querying agy: %s", exc)
    return None


def post_webhook_to_ha(ha_url: str, payload: dict[str, Any]) -> bool:
    """POST full quota payload to the custom component's webhook endpoint for native entity management."""
    url = f"{ha_url}/api/webhook/gemini_usage_antigravity_quota"
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status in (200, 204)
    except Exception as exc:
        _LOGGER.debug("Webhook post to %s failed (native integration may not be loaded yet): %s", url, exc)
        return False


def post_state_to_ha(
    ha_url: str,
    ha_token: str,
    entity_id: str,
    state: str | int | float,
    attributes: dict[str, Any],
) -> bool:
    """POST an entity state update to Home Assistant REST API."""
    url = f"{ha_url}/api/states/{entity_id}"
    payload = json.dumps({"state": str(state), "attributes": attributes}).encode("utf-8")
    headers = {
        "Authorization": f"Bearer {ha_token}",
        "Content-Type": "application/json",
    }
    req = urllib.request.Request(url, data=payload, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status in (200, 201)
    except urllib.error.HTTPError as exc:
        _LOGGER.error("HTTP error %d posting to %s: %s", exc.code, entity_id, exc.read().decode())
    except Exception as exc:
        _LOGGER.error("Failed to post to HA %s: %s", entity_id, exc)
    return False



def delete_entity_from_ha(ha_url: str, ha_token: str, entity_id: str) -> bool:
    """Remove an entity from Home Assistant states via DELETE /api/states/<entity_id>."""
    url = f"{ha_url}/api/states/{entity_id}"
    headers = {"Authorization": f"Bearer {ha_token}"}
    req = urllib.request.Request(url, headers=headers, method="DELETE")
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status in (200, 201, 204)
    except Exception as exc:
        _LOGGER.debug("Could not delete %s: %s", entity_id, exc)
        return False


def cleanup_vibe_coded_ghost_sensors(ha_url: str, ha_token: str) -> int:
    """Find and clean up unavailable ghost sensors from the old vibe-coded integration."""
    url = f"{ha_url}/api/states"
    headers = {"Authorization": f"Bearer {ha_token}"}
    req = urllib.request.Request(url, headers=headers)
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
        if delete_entity_from_ha(ha_url, ha_token, entity_id):
            count += 1
            _LOGGER.info("Deleted stale ghost entity: %s", entity_id)

    return count


def sync_once(ha_url: str, ha_token: str) -> bool:
    """Fetch current quota and publish all sensors to Home Assistant."""
    payload = fetch_quota_from_agy()
    if not payload:
        post_state_to_ha(
            ha_url,
            ha_token,
            "sensor.antigravity_quota_status",
            "Offline",
            {
                "friendly_name": "Antigravity Quota Status",
                "icon": "mdi:cloud-alert",
                "last_error": "Failed to query agy CLI",
                "timestamp": datetime.now(timezone.utc).isoformat(),
            },
        )
        return False

    now_iso = datetime.now(timezone.utc).isoformat()

    # 1. Push payload to Home Assistant Custom Component webhook (updates native entities with Unique IDs)
    webhook_success = post_webhook_to_ha(ha_url, payload)
    if webhook_success:
        _LOGGER.debug("Pushed live quota payload to Home Assistant native webhook")

    groups = payload.get("command", {}).get("data", {}).get("groups", [])
    if not groups:
        _LOGGER.warning("No quota groups found in agy response")
        return False


    # Bucket configuration mappings
    bucket_definitions = {
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

    synced_count = 0
    for group in groups:
        group_name = group.get("name", "")
        group_desc = group.get("description", "")
        for bucket in group.get("buckets", []):
            bucket_id = bucket.get("id", "")
            cfg = bucket_definitions.get(bucket_id)
            if not cfg:
                continue

            rem_frac = max(0.0, min(1.0, float(bucket.get("remaining_fraction", 0.0))))
            used_frac = max(0.0, min(1.0, 1.0 - rem_frac))

            rem_pct = round(rem_frac * 100.0, 1)
            used_pct = round(used_frac * 100.0, 1)

            int_rem_pct = round(rem_frac * 100.0)
            int_used_pct = round(used_frac * 100.0)

            reset_time = bucket.get("reset_time", "")
            resets_in = format_resets_in(reset_time)
            desc = bucket.get("description") or group_desc

            # Predictive analytics: Burn Rate, Projected Usage, Runway, and Risk
            window_str = bucket.get("window", "")
            window_seconds = 5 * 3600 if "5h" in window_str or "5h" in bucket_id else 7 * 86400

            time_to_reset_seconds = 0
            if reset_time:
                try:
                    target_dt = datetime.fromisoformat(reset_time.replace("Z", "+00:00"))
                    now_dt = datetime.now(timezone.utc)
                    time_to_reset_seconds = max(0, int((target_dt - now_dt).total_seconds()))
                except Exception:
                    time_to_reset_seconds = 0

            elapsed_seconds = max(0, window_seconds - time_to_reset_seconds)
            elapsed_hours = elapsed_seconds / 3600.0
            hours_to_reset = time_to_reset_seconds / 3600.0

            if elapsed_hours >= 0.05 and used_pct > 0:
                burn_rate_pct_per_h = round(used_pct / elapsed_hours, 2)
            else:
                burn_rate_pct_per_h = 0.0

            if hours_to_reset > 0 and burn_rate_pct_per_h > 0:
                projected_pct = round(min(500.0, used_pct + (burn_rate_pct_per_h * hours_to_reset)), 1)
            else:
                projected_pct = used_pct

            if burn_rate_pct_per_h > 0 and rem_pct > 0:
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
            elif rem_pct <= 0:
                runway_hours = 0.0
                runway_str = "0m (Exhausted)"
            else:
                runway_hours = 999.0
                runway_str = "Sufficient"

            if rem_pct <= 0 or used_pct >= 100:
                pace_status = "Exhausted"
                is_over_quota_risk = True
            elif projected_pct > 100.0 and time_to_reset_seconds > 0:
                pace_status = "High Risk (Over Quota)"
                is_over_quota_risk = True
            elif projected_pct > 80.0:
                pace_status = "Elevated"
                is_over_quota_risk = False
            else:
                pace_status = "Safe"
                is_over_quota_risk = False

            safe_pace_pct_per_h = round(rem_pct / hours_to_reset, 2) if hours_to_reset > 0 else 0.0

            common_attrs = {
                "unit_of_measurement": "%",
                "state_class": "measurement",
                "used_pct": used_pct,
                "remaining_pct": rem_pct,
                "used_fraction": round(used_frac, 4),
                "remaining_fraction": round(rem_frac, 4),
                "reset_time": reset_time,
                "resets_in": resets_in,
                "window": bucket.get("window", ""),
                "group": group_name,
                "description": desc,
                "burn_rate_pct_per_h": burn_rate_pct_per_h,
                "projected_pct": projected_pct,
                "runway": runway_str,
                "runway_hours": round(runway_hours, 2),
                "pace_status": pace_status,
                "is_over_quota_risk": is_over_quota_risk,
                "safe_pace_pct_per_h": safe_pace_pct_per_h,
                "last_synced": now_iso,
            }

            base_id = cfg["base_id"]

            # 1. Primary sensor defaults to Used % (user preference)
            attrs_primary = {**common_attrs, "friendly_name": cfg["name_used"], "icon": cfg["icon"]}
            if post_state_to_ha(ha_url, ha_token, base_id, int_used_pct, attrs_primary):
                synced_count += 1
                _LOGGER.debug("Synced primary %s -> %d%% used (%s)", base_id, int_used_pct, resets_in)

            # 2. Explicit _used sensor
            used_id = f"{base_id}_used"
            attrs_used = {**common_attrs, "friendly_name": cfg["name_used"], "icon": cfg["icon"]}
            post_state_to_ha(ha_url, ha_token, used_id, int_used_pct, attrs_used)

            # 3. Explicit _remaining sensor
            rem_id = f"{base_id}_remaining"
            attrs_rem = {**common_attrs, "friendly_name": cfg["name_remaining"], "icon": cfg["icon"]}
            post_state_to_ha(ha_url, ha_token, rem_id, int_rem_pct, attrs_rem)

            # 4. Explicit _resets_in countdown sensor (e.g. '2d 19h')
            resets_id = f"{base_id}_resets_in"
            attrs_resets = {
                "friendly_name": f"{cfg['name_used']} — Reset In",
                "icon": "mdi:timer-sand",
                "reset_time": reset_time,
                "group": group_name,
                "window": bucket.get("window", ""),
                "last_synced": now_iso,
            }
            post_state_to_ha(ha_url, ha_token, resets_id, resets_in, attrs_resets)

            # 5. Burn Rate sensor (%/h)
            burn_id = f"{base_id}_burn_rate"
            post_state_to_ha(
                ha_url,
                ha_token,
                burn_id,
                burn_rate_pct_per_h,
                {
                    "friendly_name": f"{cfg['name_used']} — Burn Rate",
                    "unit_of_measurement": "%/h",
                    "state_class": "measurement",
                    "icon": "mdi:fire",
                    "group": group_name,
                    "last_synced": now_iso,
                },
            )

            # 6. Projected Usage at Reset (%)
            proj_id = f"{base_id}_projected"
            post_state_to_ha(
                ha_url,
                ha_token,
                proj_id,
                projected_pct,
                {
                    "friendly_name": f"{cfg['name_used']} — Projected at Reset",
                    "unit_of_measurement": "%",
                    "state_class": "measurement",
                    "icon": "mdi:chart-bell-curve-cumulative",
                    "is_over_quota_risk": is_over_quota_risk,
                    "group": group_name,
                    "last_synced": now_iso,
                },
            )

            # 7. Quota Runway (Hours remaining until 0%)
            runway_id = f"{base_id}_runway"
            post_state_to_ha(
                ha_url,
                ha_token,
                runway_id,
                runway_str,
                {
                    "friendly_name": f"{cfg['name_used']} — Runway",
                    "icon": "mdi:airplane-takeoff",
                    "runway_hours": round(runway_hours, 2),
                    "group": group_name,
                    "last_synced": now_iso,
                },
            )

            # 8. Pace Status sensor
            pace_id = f"{base_id}_pace"
            post_state_to_ha(
                ha_url,
                ha_token,
                pace_id,
                pace_status,
                {
                    "friendly_name": f"{cfg['name_used']} — Pace Status",
                    "icon": "mdi:speedometer",
                    "is_over_quota_risk": is_over_quota_risk,
                    "safe_pace_pct_per_h": safe_pace_pct_per_h,
                    "group": group_name,
                    "last_synced": now_iso,
                },
            )

            # 9. Binary Sensor: Over-quota risk (problem)
            risk_id = base_id.replace("sensor.", "binary_sensor.") + "_over_quota_risk"
            post_state_to_ha(
                ha_url,
                ha_token,
                risk_id,
                "on" if is_over_quota_risk else "off",
                {
                    "friendly_name": f"{cfg['name_used']} — Over-Quota Risk",
                    "device_class": "problem",
                    "icon": "mdi:alert-circle-outline",
                    "pace_status": pace_status,
                    "projected_pct": projected_pct,
                    "group": group_name,
                    "last_synced": now_iso,
                },
            )


    # Sync overarching health / status sensor
    post_state_to_ha(
        ha_url,
        ha_token,
        "sensor.antigravity_quota_status",
        "Online",
        {
            "friendly_name": "Antigravity Quota Status",
            "icon": "mdi:cloud-check",
            "synced_sensors": synced_count,
            "last_synced": now_iso,
            "version": "1.0.0",
        },
    )

    _LOGGER.info("Successfully synced %d Antigravity quota sensors to Home Assistant.", synced_count)
    return synced_count > 0


def main() -> None:
    """CLI entrypoint."""
    parser = argparse.ArgumentParser(description="Sync Antigravity Quotas to Home Assistant")
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
        _LOGGER.error("Missing HA_TOKEN. Provide via --ha-token, env, or .env file.")
        sys.exit(1)

    if args.cleanup_ghosts:
        _LOGGER.info("Cleaning up unavailable ghost entities from previous sessions...")
        deleted = cleanup_vibe_coded_ghost_sensors(ha_url, ha_token)
        _LOGGER.info("Cleanup complete. Removed %d ghost entities.", deleted)

    if args.once:
        success = sync_once(ha_url, ha_token)
        sys.exit(0 if success else 1)

    _LOGGER.info(
        "Starting Antigravity -> Home Assistant quota sync daemon (interval: %ds, target: %s)",
        interval,
        ha_url,
    )

    while True:
        try:
            sync_once(ha_url, ha_token)
        except Exception as exc:
            _LOGGER.error("Error during sync cycle: %s", exc)
        time.sleep(interval)


if __name__ == "__main__":
    main()
