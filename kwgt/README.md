# Antigravity KWGT widget

Feed (served by the `gemini_usage` HA integration, needs a HA restart after updating it):

- Tailscale (sync service): `http://100.73.102.17:8787/quota.json`
- HA LAN: `http://192.168.1.240:8123/api/gemini_usage/quota.json`
- Public: `https://hellx.patuleia.xyz/api/gemini_usage/quota.json`

## Import

`python3 build_kwgt.py [FEED_URL]` → `Antigravity_Quota.kwgt` → copy to phone
(`Kustom/widgets/`) → KWGT → Load preset. The generated property names are
best-effort and untested; if import fails, build manually:

## Manual build (exact formulas)

Widget size 4x3. Add a **Global** (Text) `url` with the feed URL, then:

1. **Shape** (Rounded Rect) full size, color `#D9121212`, corners ~28dp.
2. **Text** `ANTIGRAVITY` bold white, top-left.
3. **Text** `● $wg(gv(url), json, ".status")$`, top-right, color
   `$if(wg(gv(url), json, ".status")="Online","#4CAF50","#FF5252")$`.
4. Per row (keys: `gemini_5h`, `gemini_weekly`,
   `claude_gpt_5h`, `claude_gpt_weekly`):
   - Label text (e.g. `Gemini Wk`), `#E0E0E0`.
   - **Progress** (Linear/Bar), min 0, max 100, level
     `$wg(gv(url), json, ".buckets.<key>.used")$`, track `#22FFFFFF`, fill color
     `$if(wg(gv(url), json, ".buckets.<key>.risk")="true","#FF5252",if(wg(gv(url), json, ".buckets.<key>.used")>=60,"#FFC107","#4CAF50"))$`.
   - Text `$wg(gv(url), json, ".buckets.<key>.used")$% → $wg(gv(url), json, ".buckets.<key>.projected")$%`,
     color `$if(wg(gv(url), json, ".buckets.<key>.risk")="true","#FF6B6B","#FFFFFF")$`.
   - Text `$wg(gv(url), json, ".buckets.<key>.resets_in")$`, `#888888`.
5. Settings → Update: set the widget refresh to 5 min; Touch on root → Force Update.
