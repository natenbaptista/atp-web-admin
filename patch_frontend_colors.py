#!/usr/bin/env python3
"""Patch the minified webadmin JS so the Button Colour grid uses the 100-color palette.

The React source is not on the AMP box — only public/assets/index-*.js.
This script finds the hardcoded swatch array in that bundle and replaces it.

Usage:
  sudo python3 patch_frontend_colors.py /opt/enepath/webadmin
  sudo python3 patch_frontend_colors.py /home/atp/atp/deploy/webadmin
"""
from __future__ import annotations

import json
import re
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
PALETTE_PATHS = [
    HERE / "button-colors.json",
    Path("/opt/enepath/webadmin/public/button-colors.json"),
]


def load_palette() -> list[dict]:
    for p in PALETTE_PATHS:
        if p.is_file():
            return json.loads(p.read_text())
    raise SystemExit("button-colors.json not found next to this script")


def find_bundles(root: Path) -> list[Path]:
    candidates = []
    for folder in (root / "public" / "assets", root / "dist" / "assets", root / "static"):
        if folder.is_dir():
            candidates.extend(sorted(folder.glob("index-*.js")))
            candidates.extend(sorted(folder.glob("main-*.js")))
            candidates.extend(sorted(folder.glob("*.js")))
    # unique, prefer index-*.js
    seen = []
    for c in candidates:
        if c not in seen and c.stat().st_size > 10_000:
            seen.append(c)
    return seen


def palette_js_objects(palette: list[dict]) -> str:
    """Compact JS array of {id,label,hex} — closest to a typical React swatch list."""
    parts = []
    for c in palette:
        hid = c["id"].replace("\\", "\\\\").replace('"', '\\"')
        lab = c["label"].replace("\\", "\\\\").replace('"', '\\"')
        hx = c["hex"]
        parts.append(f'{{id:"{hid}",label:"{lab}",hex:"{hx}"}}')
    return "[" + ",".join(parts) + "]"


def palette_js_hex_list(palette: list[dict]) -> str:
    return "[" + ",".join(f'"{c["hex"]}"' for c in palette) + "]"


def palette_js_names(palette: list[dict]) -> str:
    return "[" + ",".join(f'"{c["label"]}"' for c in palette) + "]"


# Known small built-in CSS-name lists used by simple colour grids
CSS_NAME_NEEDLE = re.compile(
    r'\[(?:"(?:red|black|blue|green|yellow|orange|purple|pink|brown|white|gray|grey|cyan|magenta|navy|teal|olive|maroon|lime|aqua|silver|fuchsia|indigo|violet|gold|coral|salmon|khaki|crimson|tomato|orchid|plum|tan|peru|sienna|chocolate|navy)"\s*,\s*){6,}',
    re.I,
)

# Array of {name/label/id, hex/value/color} objects that looks like a swatch list
OBJ_SWATCH_NEEDLE = re.compile(
    r'\[(?:\{(?:id|name|label|value|color|hex):"[^"]{1,40}",(?:id|name|label|value|color|hex):"[^"]{1,40}"(?:,[^}]{0,80})?\}\s*,\s*){6,}',
    re.I,
)

# Array of #RRGGBB strings (at least 8)
HEX_ARR_NEEDLE = re.compile(
    r'\[(?:"#[0-9A-Fa-f]{3,8}"\s*,\s*){7,}"#[0-9A-Fa-f]{3,8}"\]'
)

# Named CSS colors as a compact comma list inside an array — also catch indianred etc.
NAMED_BLOCK = re.compile(
    r'\["(?:aliceblue|antiquewhite|aqua|aquamarine|azure|beige|bisque|black|blanchedalmond|blue)","[^"]+"'
)


def replace_first_array(js: str, start: int) -> tuple[str, str] | None:
    """From start (index of '['), find matching ']' at depth 0 and return (full, inner)."""
    if start < 0 or start >= len(js) or js[start] != "[":
        return None
    depth = 0
    in_str = False
    esc = False
    quote = ""
    for i in range(start, min(len(js), start + 200_000)):
        ch = js[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == quote:
                in_str = False
            continue
        if ch in ('"', "'"):
            in_str = True
            quote = ch
            continue
        if ch == "[":
            depth += 1
        elif ch == "]":
            depth -= 1
            if depth == 0:
                return js[start : i + 1], js[start + 1 : i]
    return None


def looks_like_color_array(text: str) -> bool:
    low = text.lower()
    hits = 0
    for w in (
        "red",
        "blue",
        "green",
        "yellow",
        "orange",
        "purple",
        "pink",
        "black",
        "#ff",
        "#00",
        "crimson",
        "navy",
        "teal",
    ):
        if w in low:
            hits += 1
    return hits >= 4


def patch_js(js: str, palette: list[dict]) -> tuple[str, str]:
    """Return (new_js, how). Raises if nothing found."""
    new_arr = palette_js_objects(palette)
    # Preferred live-bundle format (enePath iz picker): {name,hex}
    namehex = "[" + ",".join(
        '{{name:"{}",hex:"{}"}}'.format(c["label"].replace('"','\\"'), c["hex"])
        for c in palette
    ) + "]"

    # 0) Known AMP bundle: const oz=[{name:"IndianRed",hex:"#CD5C5C"}, ...]
    i = js.find("const oz=[")
    if i >= 0:
        replaced = replace_first_array(js, js.find("[", i))
        if replaced and looks_like_color_array(replaced[0]):
            old = replaced[0]
            if "indian red" in old and old.count("{name:") >= 90:
                return js, "already-patched"
            return js[: js.find("[", i)] + namehex + js[js.find("[", i) + len(old) :], "const-oz-namehex"

    # 1) Already patched?
    if "indian red" in js and "light coral" in js and js.count("{name:") >= 90:
        return js, "already-patched"

    # 2) Hex array
    m = HEX_ARR_NEEDLE.search(js)
    if m:
        old = m.group(0)
        if looks_like_color_array(old):
            return js[: m.start()] + palette_js_hex_list(palette) + js[m.end() :], "hex-array"

    # 3) CSS name string array
    m = CSS_NAME_NEEDLE.search(js)
    if m:
        replaced = replace_first_array(js, m.start())
        if replaced:
            old, _ = replaced
            if looks_like_color_array(old) and len(old) < 80_000:
                return js[: m.start()] + palette_js_names(palette) + js[m.start() + len(old) :], "css-name-array"

    # 4) Object swatch array
    m = OBJ_SWATCH_NEEDLE.search(js)
    if m:
        replaced = replace_first_array(js, m.start())
        if replaced:
            old, _ = replaced
            if looks_like_color_array(old) and len(old) < 80_000:
                return js[: m.start()] + new_arr + js[m.start() + len(old) :], "object-swatch-array"

    # 5) Search for Button Colour nearby, then nearest array
    for needle in (
        "Button Colour",
        "Button Color",
        "buttonColour",
        "buttonColor",
        "button_colour",
        "button_color",
    ):
        idx = js.find(needle)
        if idx < 0:
            idx = js.lower().find(needle.lower())
        if idx < 0:
            continue
        window = js[max(0, idx - 2000) : idx + 8000]
        rel = window.find("[")
        if rel >= 0:
            abs_i = max(0, idx - 2000) + rel
            replaced = replace_first_array(js, abs_i)
            if replaced and looks_like_color_array(replaced[0]) and len(replaced[0]) < 80_000:
                old = replaced[0]
                return js[:abs_i] + new_arr + js[abs_i + len(old) :], f"near:{needle}"

    # 6) Last resort: look for a short list of well-known hex swatches (material / basic)
    m = re.search(r'\["#(?:F44336|e74c3c|ff0000|FF0000)"', js)
    if m:
        replaced = replace_first_array(js, m.start())
        if replaced and len(replaced[0]) < 20_000:
            old = replaced[0]
            return js[: m.start()] + palette_js_hex_list(palette) + js[m.start() + len(old) :], "material-hex"

    raise RuntimeError("could not locate a colour swatch array in the bundle")


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: patch_frontend_colors.py /path/to/webadmin", file=sys.stderr)
        return 2
    root = Path(sys.argv[1])
    palette = load_palette()
    bundles = find_bundles(root)
    if not bundles:
        print(f"no JS bundles under {root}", file=sys.stderr)
        return 1
    patched_any = False
    for b in bundles:
        js = b.read_text(encoding="utf-8", errors="surrogateescape")
        try:
            new_js, how = patch_js(js, palette)
        except RuntimeError as e:
            print(f"skip {b}: {e}")
            continue
        if how == "already-patched":
            print(f"ok  {b}: already patched")
            patched_any = True
            continue
        bak = b.with_suffix(b.suffix + ".bak-colors")
        if not bak.exists():
            shutil.copy2(b, bak)
        b.write_text(new_js, encoding="utf-8", errors="surrogateescape")
        print(f"ok  {b}: replaced via {how}  ({len(js)} -> {len(new_js)} bytes)")
        patched_any = True
        # only first successful bundle — usually one index-*.js
        break
    if not patched_any:
        print("FAILED: no bundle patched", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
