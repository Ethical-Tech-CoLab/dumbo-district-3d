#!/usr/bin/env python3
"""Render a fixture sheet of every tree prototype, across seasons and maturities.

The point is to be able to look at the whole vocabulary at once. A district has 1,306 trees scattered
across two kilometres of street, so a change to the seasonal palette or the maturity curve is
impossible to judge from inside the scene: you would have to walk to a ginkgo in November to find out
whether ginkgos look right in November.

This lays them all out on a grid instead -- every genus, every season, at three maturities -- as a
self-contained page next to the review sheet. Same idea as the photo review sheet: put the thing a
person needs to judge in front of them, rather than making them go and find it.

Reads `props.json`, so it always shows what the build actually produced rather than what this script
believes it should have.
"""

from __future__ import annotations

import html
import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
PROPS = REPO_ROOT / "viewer" / "public" / "district" / "props.json"
OUT = REPO_ROOT / "viewer" / "public" / "trees" / "index.html"

SEASONS = ("spring", "summer", "autumn", "winter")
MODULE_ID = "dumbo-district"

# The three points on the maturity curve build_scene_dressing.py produces, as heights in metres.
# These are the medians of the three bands, not invented: young trees run 2.2-4.0 m, establishing
# 4.0-9.0 m, and mature up to 24 m.
MATURITY = [
    ("young", 3.0, "newly planted, dbh under 6 in"),
    ("establishing", 6.5, "dbh 6-14 in"),
    ("mature", 15.0, "dbh over 14 in"),
]

# One shared vertical scale across every cell, so a mature plane towers over a whip exactly as it
# does in the scene.
MAX_HEIGHT_M = max(height for _, height, _ in MATURITY)

TRUNK = "#5a4634"


def canopy_svg(label: str, height_m: float, spread_m: float, colour: str, bare: bool) -> str:
    """One tree, drawn to the same proportions the viewer uses.

    Deliberately a redraw of the viewer's procedural form rather than a screenshot: a fixture that
    cannot be generated without a GPU is a fixture nobody regenerates.

    All three maturities share one scale, because the whole point of the trio is to show that a
    newly planted whip and a mature plane are *not* the same size. Scaling each to fit its own box
    would draw them identically, which is exactly the defect this sheet exists to expose.
    """
    width, box = 92, 168
    scale = (box - 14) / MAX_HEIGHT_M
    h = height_m * scale
    spread = spread_m * scale
    ground = box - 8
    trunk_h = h * (0.52 if bare else 0.42)
    trunk_w = max(1.6, spread * (0.055 if bare else 0.045))

    parts = [
        f'<rect x="{width / 2 - trunk_w / 2:.1f}" y="{ground - trunk_h:.1f}" '
        f'width="{trunk_w:.1f}" height="{trunk_h:.1f}" fill="{TRUNK}" rx="1"/>'
    ]

    if bare:
        for dx, dy in ((-0.30, 0.30), (0.30, 0.30), (-0.16, 0.45), (0.16, 0.45)):
            parts.append(
                f'<line x1="{width / 2:.1f}" y1="{ground - trunk_h:.1f}" '
                f'x2="{width / 2 + spread * dx:.1f}" y2="{ground - trunk_h - h * dy:.1f}" '
                f'stroke="{colour}" stroke-width="1.6" stroke-linecap="round"/>'
            )
    else:
        parts.append(
            f'<ellipse cx="{width / 2:.1f}" cy="{ground - h * 0.58:.1f}" '
            f'rx="{spread * 0.34:.1f}" ry="{spread * 0.27:.1f}" fill="{colour}"/>'
        )
        parts.append(
            f'<ellipse cx="{width / 2:.1f}" cy="{ground - h * 0.80:.1f}" '
            f'rx="{spread * 0.25:.1f}" ry="{spread * 0.21:.1f}" fill="{colour}" opacity="0.92"/>'
        )

    return (
        f'<svg viewBox="0 0 {width} {box}" width="{width}" height="{box}" role="img" '
        f'aria-label="{html.escape(label)}">' + "".join(parts) + "</svg>"
    )


def main() -> int:
    if not PROPS.exists():
        raise SystemExit("props.json not built; run build_scene_dressing.py --props first")
    props = json.loads(PROPS.read_text(encoding="utf-8"))

    trees = [p for p in props.get("prototypes", []) if p.get("kind") == "tree"]
    counts: dict[str, int] = {}
    for instance in props.get("instances", []):
        if str(instance.get("p", "")).startswith("tree_"):
            counts[instance["p"]] = counts.get(instance["p"], 0) + 1
    trees.sort(key=lambda p: -counts.get(p["prototype_id"], 0))

    rows = []
    for prototype in trees:
        spec = (prototype.get("extensions") or {}).get(MODULE_ID) or {}
        foliage = spec.get("seasonal_foliage") or {}
        deciduous = spec.get("deciduous", True)
        spread = (prototype.get("size_m") or [9, 9, 9])[0]
        count = counts.get(prototype["prototype_id"], 0)

        cells = []
        for season in SEASONS:
            colour = foliage.get(season, "#4e6c3c")
            bare = season == "winter" and deciduous
            trio = "".join(
                canopy_svg(
                    f"{prototype['label']} {season} {name}",
                    height,
                    # Crown spread follows the tree's own height, as it does in the viewer, where a
                    # single uniform instance scale drives both. A young whip with a mature crown
                    # would be a drawing of nothing.
                    spread * (height / MAX_HEIGHT_M),
                    colour,
                    bare,
                )
                for name, height, _ in MATURITY
            )
            cells.append(
                f'<td><div class="trio">{trio}</div>'
                f'<div class="swatch"><i style="background:{colour}"></i>{colour}'
                f'{" · bare" if bare else ""}</div></td>'
            )

        rows.append(
            f"<tr><th scope=\"row\"><b>{html.escape(prototype['label'])}</b>"
            f"<span class=\"id\">{html.escape(prototype['prototype_id'])}</span>"
            f"<span class=\"n\">{count} in scene</span>"
            f"<span class=\"n\">{'deciduous' if deciduous else 'evergreen'}</span></th>"
            + "".join(cells)
            + "</tr>"
        )

    total = sum(counts.values())
    page = f"""<!doctype html>
<html lang="en">
<meta charset="utf-8">
<title>DUMBO tree fixtures</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
  :root {{ color-scheme: dark; }}
  body {{ margin:0; background:#0d1117; color:#e6edf3;
         font:14px/1.5 ui-sans-serif,system-ui,-apple-system,"Segoe UI",sans-serif; }}
  header {{ padding:18px 22px; border-bottom:1px solid #30363d; position:sticky; top:0;
            background:#0d1117ee; backdrop-filter:blur(6px); }}
  h1 {{ margin:0 0 4px; font-size:18px; }}
  p  {{ margin:4px 0 0; color:#9aa7b2; max-width:76ch; }}
  table {{ border-collapse:collapse; margin:18px 22px 40px; }}
  th, td {{ border:1px solid #30363d; padding:8px 10px; vertical-align:top; }}
  thead th {{ position:sticky; top:78px; background:#161b22; text-transform:capitalize;
              font-size:13px; letter-spacing:.02em; }}
  tbody th {{ text-align:left; background:#11161d; white-space:nowrap; }}
  tbody th b {{ display:block; font-size:14px; }}
  .id {{ display:block; color:#7d8892; font:12px ui-monospace,monospace; margin-top:2px; }}
  .n  {{ display:block; color:#9aa7b2; font-size:12px; }}
  .trio {{ display:flex; gap:2px; align-items:flex-end; }}
  .swatch {{ margin-top:6px; color:#9aa7b2; font:12px ui-monospace,monospace;
             display:flex; align-items:center; gap:6px; }}
  .swatch i {{ width:12px; height:12px; border-radius:2px; display:inline-block;
               border:1px solid #30363d; }}
  a {{ color:#58a6ff; }}
</style>
<header>
  <h1>DUMBO tree fixtures</h1>
  <p>Every tree prototype the build produced, across four seasons and the three maturities the
     Forestry census actually contains. Each trio is young · establishing · mature, drawn to the
     proportions the viewer uses. {len(trees)} prototypes, {total} trees placed.</p>
  <p>Position, species and trunk diameter are grade <b>A</b> from the NYC Forestry census
     (<code>DSRC-009</code>). Canopy form and seasonal colour are plausible for the genus rather than
     measured, so the rendered prop stays graded <b>C</b>. Summer foliage is additionally pulled
     towards the colour measured from photographs of DUMBO.
     <a href="../index.html">Back to the viewer</a></p>
</header>
<table>
  <thead><tr><th>Genus</th>{''.join(f'<th>{s}</th>' for s in SEASONS)}</tr></thead>
  <tbody>{''.join(rows)}</tbody>
</table>
</html>
"""

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(page, encoding="utf-8")
    print(f"wrote {OUT.relative_to(REPO_ROOT)}")
    print(f"  {len(trees)} tree prototypes, {total} instances, {len(SEASONS)} seasons")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
