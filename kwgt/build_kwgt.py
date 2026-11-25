#!/usr/bin/env python3
"""Generate Antigravity_Quota.kwgt (a ZIP containing preset.json) for KWGT.

Usage: python3 build_kwgt.py [FEED_URL]

The preset structure (internal_toggles/internal_formulas, paint_color,
shape_*, text_expression, position_offset_*, globals_list) mirrors real
presets exported by KWGT. Coordinates are offsets from the widget centre.
"""
import json
import sys
import zipfile
from pathlib import Path

URL = sys.argv[1] if len(sys.argv) > 1 else "http://192.168.1.240:8123/api/gemini_usage/quota.json"

W, H = 684, 440
ROWS = [
    ("Gemini 5h", "gemini_5h"),
    ("Gemini Wk", "gemini_weekly"),
    ("Claude 5h", "claude_gpt_5h"),
    ("Claude Wk", "claude_gpt_weekly"),
]
BAR_LEFT, BAR_W, BAR_H = -150, 190, 18

GREEN, AMBER, RED, CORAL = "#FF4CAF50", "#FFFFC107", "#FFFF5252", "#FFFF6B6B"


def g(path):
    return f'wg(gv(url), json, "{path}")'


def with_formulas(item, formulas):
    """Attach Kustom formula fields (toggle 10 = formula mode)."""
    if formulas:
        item["internal_toggles"] = {k: 10 for k in formulas}
        item["internal_formulas"] = dict(formulas)
    return item


def text(title, expr, x, y, size, color, formulas=None):
    formulas = dict(formulas or {})
    if "$" in expr:
        formulas["text_expression"] = expr
    item = {
        "internal_type": "TextModule",
        "internal_title": title,
        "text_expression": expr,
        "text_size": size,
        "paint_color": color,
        "position_offset_x": x,
        "position_offset_y": y,
    }
    return with_formulas(item, formulas)


def shape(title, x, y, w, h, color, corners, formulas=None):
    item = {
        "internal_type": "ShapeModule",
        "internal_title": title,
        "shape_type": "RECT",
        "shape_width": w,
        "shape_height": h,
        "shape_corners": corners,
        "paint_color": color,
        "position_offset_x": x,
        "position_offset_y": y,
    }
    return with_formulas(item, formulas)


items = [shape("Card", 0, 0, W, H, "#E6121212", 36)]

status = g(".status")
items.append(text("Title", "ANTIGRAVITY", -215, -155, 31, "#FFFFFFFF"))
items.append(
    text(
        "Status",
        f"$\"● \" + {status}$",
        250,
        -155,
        26,
        GREEN,
        {"paint_color": f'$if({status} = "Online", {GREEN}, {RED})$'},
    )
)

y0, step = -75, 76
for i, (label, key) in enumerate(ROWS):
    y = y0 + i * step
    used = g(f".buckets.{key}.used")
    proj = g(f".buckets.{key}.projected")
    risk = g(f".buckets.{key}.risk")
    reset = g(f".buckets.{key}.resets_in")

    items.append(text(f"{key} label", label, -250, y, 27, "#FFE0E0E0"))

    items.append(shape(f"{key} track", BAR_LEFT + BAR_W / 2, y, BAR_W, BAR_H, "#22FFFFFF", BAR_H / 2))
    fill_w = f"mu(max, 1, {used}) / 100 * {BAR_W}"
    items.append(
        shape(
            f"{key} fill",
            BAR_LEFT + 1,
            y,
            1,
            BAR_H,
            GREEN,
            BAR_H / 2,
            {
                "shape_width": f"${fill_w}$",
                "position_offset_x": f"${BAR_LEFT} + ({fill_w}) / 2$",
                "paint_color": f'$if({risk} = "true", {RED}, if({used} >= 60, {AMBER}, {GREEN}))$',
            },
        )
    )

    items.append(
        text(
            f"{key} usage",
            f'${used} + "% → " + {proj} + "%"$',
            125,
            y,
            27,
            "#FFFFFFFF",
            {"paint_color": f'$if({risk} = "true", {CORAL}, #FFFFFFFF)$'},
        )
    )
    items.append(text(f"{key} reset", f"${reset}$", 285, y, 22, "#FF888888"))

preset = {
    "preset_info": {
        "archive": "",
        "author": "",
        "description": "Live Antigravity quotas from Home Assistant",
        "email": "",
        "features": "",
        "pflags": 0,
        "hash": None,
        "height": H,
        "id": "a7d3c1e2-4f55-4c9e-9a31-antigravity01",
        "locked": False,
        "release": 1,
        "ts": 1763838000000,
        "title": "Antigravity Quota",
        "version": 13,
        "width": W,
        "xscreens": 0,
        "yscreens": 0,
    },
    "preset_root": {
        "internal_type": "RootLayerModule",
        "internal_events": [{"action": "KUSTOM_ACTION"}],
        "globals_list": {"url": {"index": 1, "type": "TEXT", "title": "url", "value": URL}},
        "config_scale_value": 100,
        "viewgroup_items": items,
    },
}

out = Path(__file__).with_name("Antigravity_Quota.kwgt")
with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
    z.writestr("preset.json", json.dumps(preset, ensure_ascii=False))
with zipfile.ZipFile(out) as z:
    assert z.testzip() is None
    json.loads(z.read("preset.json"))
print(f"Wrote {out} ({len(items)} items) using feed {URL}")
