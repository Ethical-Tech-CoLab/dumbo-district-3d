"""
Brooklyn and Williamsburg Bridges as district context.

Why these are built here and the Manhattan Bridge is not
-------------------------------------------------------
The Manhattan Bridge has an owning module. `manhattan-bridge-3d` authors it to CAD tolerance, and
this district shows whatever proxy that module publishes. DUMBO-SCOPE.md is explicit that bridge
towers, cables and decks are not ours, and that remains true.

Nobody owns the Brooklyn or Williamsburg Bridges. They are, however, unavoidable: the Brooklyn
Bridge closes the view north-west from Fulton Ferry and is half of why anyone stands there, and the
Williamsburg Bridge sits on the horizon up-river. Leaving them out does not make the view neutral,
it makes it wrong.

So they are built the way the Manhattan skyline is built (DSRC-011): **context geometry, graded C,
derived from the mapped centreline and never claiming to be a survey**. The centreline, the tower
positions and the deck height come from OpenStreetMap. Everything else -- cable sag, tower form,
deck section -- is a conventional suspension-bridge form fitted to those measurements.

If either bridge ever gets its own module, this is replaced exactly the way the Manhattan Bridge's
placeholder was: point the district at the module's manifest and delete the generated file. The
viewer already prefers a module proxy over local context for the Manhattan Bridge and does the same
here.

Usage:
    python scripts/build_bridges.py            # fetch if needed, then build
    python scripts/build_bridges.py --offline  # build from the cached fetch only
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(Path(__file__).resolve().parent))

from district_control import AGENT_ID, DistrictControl  # noqa: E402
from ingest_sources import _overpass  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent
OUT = REPO_ROOT / "viewer" / "public" / "district" / "bridges.json"
RAW = REPO_ROOT / "data" / "streets" / "osm-context-bridges.raw.json"

CONTRACT_VERSION = "1.0.0"
MODULE_ID = "dumbo-district"
FRAME_ID = "nyc-harbor-enu"
FT = 0.3048

# Published dimensions, used to check that what came back from OSM is the bridge we asked for and to
# supply the two numbers OSM does not carry. Both are widely published engineering figures rather
# than measurements this project made, which is part of why the result is graded C.
BRIDGES = {
    "brooklyn": {
        "name": "Brooklyn Bridge",
        "osm_name": "Brooklyn Bridge",
        "bbox": (40.698, -74.002, 40.712, -73.985),
        # Main span 1,595.5 ft; towers 276.5 ft above mean high water; deck ~127 ft at mid-span.
        "main_span_m": 1595.5 * FT,
        # Main span plus two 930 ft side spans: the whole suspended structure between anchorages.
        "suspended_m": (1595.5 + 2 * 930.0) * FT,
        "tower_height_m": 276.5 * FT,
        "deck_height_m": 127.0 * FT,
        "deck_width_m": 26.0,
        "tower_colour": "#8f8378",
        "cable_colour": "#b9b2a4",
        "deck_colour": "#7d7466",
        # Masonry towers with two pointed arches: the reason it does not look like its neighbours.
        "tower_form": "masonry_arch",
        "notes_extra": "Masonry towers with twin pointed arches, and the diagonal stays that make its web.",
    },
    "williamsburg": {
        "name": "Williamsburg Bridge",
        "osm_name": "Williamsburg Bridge",
        "bbox": (40.703, -73.985, 40.718, -73.955),
        # Main span 1,600 ft; towers 335 ft; deck 135 ft above mean high water.
        "main_span_m": 1600.0 * FT,
        # Main span plus two 596 ft side spans.
        "suspended_m": (1600.0 + 2 * 596.0) * FT,
        "tower_height_m": 335.0 * FT,
        "deck_height_m": 135.0 * FT,
        "deck_width_m": 36.0,
        "tower_colour": "#8a8d90",
        "cable_colour": "#9aa0a4",
        "deck_colour": "#6f7275",
        "tower_form": "steel_lattice",
        "notes_extra": "Steel lattice towers, unclad, which is what distinguishes it at distance.",
    },
}


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def fetch(offline: bool) -> dict:
    if offline or RAW.exists():
        if RAW.exists():
            return json.loads(RAW.read_text(encoding="utf-8"))
        raise SystemExit(f"--offline given but {RAW} does not exist")

    collected: dict[str, list] = {}
    for key, spec in BRIDGES.items():
        south, west, north, east = spec["bbox"]
        query = (
            "[out:json][timeout:60];("
            f'way["bridge"]["name"="{spec["osm_name"]}"]({south},{west},{north},{east});'
            f'way["man_made"="bridge"]["name"="{spec["osm_name"]}"]({south},{west},{north},{east});'
            ");out geom tags;"
        )
        result = _overpass(query)
        ways = [e for e in result.get("elements", []) if e.get("geometry")]
        print(f"    {spec['name']}: {len(ways)} ways")
        collected[key] = ways

    RAW.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(collected, indent=1)
    RAW.write_text(text, encoding="utf-8")
    RAW.with_suffix(RAW.suffix + ".source.json").write_text(
        json.dumps(
            {
                "source_id": "DSRC-007",
                "fetched_at": now(),
                "fetched_by": AGENT_ID,
                "bytes": len(text),
                "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
                "note": (
                    "OpenStreetMap contributors, ODbL. Centrelines of the Brooklyn and Williamsburg "
                    "Bridges, used to place district context geometry. Neither bridge has an owning "
                    "module; if one appears, this is replaced by that module's proxy."
                ),
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    return collected


def longest_centreline(ways: list) -> list[tuple[float, float]]:
    """The longest open roadway way, which is the deck's centreline.

    Not simply the longest way. OSM maps each of these bridges as a `man_made=bridge` *area* as
    well as its carriageways, and that area is the longest thing that comes back -- it traces the
    whole structure and closes, so its end-to-end distance is zero. Resampling around it produced a
    deck that ran around the bridge's outline and towers 40 m apart instead of 486.

    So: a way is a centreline only if it carries a road and does not return to where it started.
    """
    best: list[tuple[float, float]] = []
    best_len = 0.0
    for way in ways:
        tags = way.get("tags") or {}
        if not tags.get("highway"):
            continue
        pts = [(float(p["lon"]), float(p["lat"])) for p in way.get("geometry", [])]
        if len(pts) < 2:
            continue
        length = sum(math.hypot(b[0] - a[0], b[1] - a[1]) for a, b in zip(pts, pts[1:]))
        span = math.hypot(pts[-1][0] - pts[0][0], pts[-1][1] - pts[0][1])
        # A closed or doubling-back way is an outline, not a line to walk along.
        if length <= 0 or span < length * 0.5:
            continue
        if length > best_len:
            best_len = length
            best = pts
    return best


def resample(points: list[tuple[float, float]], count: int) -> list[tuple[float, float]]:
    """Even spacing along the polyline, so deck segments do not bunch where the survey was dense."""
    if len(points) < 2:
        return points
    spans = []
    for a, b in zip(points, points[1:]):
        spans.append((a, b, math.hypot(b[0] - a[0], b[1] - a[1])))
    total = sum(s[2] for s in spans)
    if total <= 0:
        return points
    out = []
    for i in range(count):
        target = total * i / (count - 1)
        travelled = 0.0
        for a, b, length in spans:
            if travelled + length >= target or (a, b, length) is spans[-1]:
                t = (target - travelled) / length if length else 0.0
                t = max(0.0, min(1.0, t))
                out.append((a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t))
                break
            travelled += length
    return out


def find_towers(
    ways: list,
    control: DistrictControl,
    main_span_m: float,
    suspended_m: float,
    sampled: list[tuple[float, float]],
) -> tuple[list[tuple[float, float]], str]:
    """Where the towers stand, measured rather than guessed where the data allows.

    Two routes, in order of how much they are actually measuring:

    1. OSM sometimes maps the main span as its own way, because the carriageway is split at each
       tower. Williamsburg has one of exactly 488 m against a published 1,600 ft. When a way's
       length matches the published main span, its endpoints *are* the towers and nothing needs
       inferring.

    2. Otherwise, centre the main span on the suspended structure. Brooklyn's longest roadway way
       is 1,054 m against a published 1,595.5 ft main span plus two 930 ft side spans -- 1,053 m --
       so that way is the suspended structure and the towers sit at the side-span offsets.

    Returns the positions and which route produced them, so the note on the record can say.
    """
    for way in ways:
        tags = way.get("tags") or {}
        if not tags.get("highway"):
            continue
        pts = [(float(p["lon"]), float(p["lat"])) for p in way.get("geometry", [])]
        if len(pts) < 2:
            continue
        enu = [control.geodetic_to_enu(lon, lat)[:2] for lon, lat in pts]
        length = sum(math.hypot(b[0] - a[0], b[1] - a[1]) for a, b in zip(enu, enu[1:]))
        if abs(length - main_span_m) <= main_span_m * 0.08:
            return [enu[0], enu[-1]], "main span mapped as its own way"

    # Fall back to the side-span offset along the resampled deck.
    total = sum(math.hypot(b[0] - a[0], b[1] - a[1]) for a, b in zip(sampled, sampled[1:]))
    if total <= 0:
        return [], "unavailable"
    side = max(0.0, (min(suspended_m, total) - main_span_m) / 2)
    positions = []
    for target in (side, side + main_span_m):
        travelled = 0.0
        placed = sampled[0]
        for a, b in zip(sampled, sampled[1:]):
            length = math.hypot(b[0] - a[0], b[1] - a[1])
            if travelled + length >= target:
                f = (target - travelled) / length if length else 0.0
                placed = (a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f)
                break
            travelled += length
        positions.append(placed)
    return positions, "main span centred on the suspended structure"


def build(control: DistrictControl, raw: dict) -> dict:
    bridges = []
    for key, spec in BRIDGES.items():
        ways = raw.get(key) or []
        centre = longest_centreline(ways)
        if len(centre) < 2:
            print(f"    {spec['name']}: no centreline, skipped")
            continue

        # Project, then resample in metres. Resampling in degrees would space the deck differently
        # east-west than north-south, which at this latitude is a 24% error.
        projected = [control.geodetic_to_enu(lon, lat)[:2] for lon, lat in centre]
        sampled = resample(projected, 48)

        total = sum(
            math.hypot(b[0] - a[0], b[1] - a[1]) for a, b in zip(sampled, sampled[1:])
        )

        # Deck profile. A suspension bridge's roadway is not flat: it rises to mid-span. Modelled as
        # a parabola between the abutments, peaking at the published mid-span clearance, which is
        # the one vertical measurement OSM does not carry and the eye immediately notices.
        deck = []
        for i, (x, y) in enumerate(sampled):
            t = i / (len(sampled) - 1)
            # Flat approach ramps either end, rising over the middle two thirds.
            rise = max(0.0, 1 - ((t - 0.5) / 0.5) ** 2)
            z = control.mhw_to_navd88(spec["deck_height_m"] * (0.34 + 0.66 * rise))
            deck.append([round(x, 2), round(y, 2), round(z, 2)])

        # Towers, measured from the data where the data says so.
        tower_xy, tower_basis = find_towers(
            ways, control, spec["main_span_m"], spec["suspended_m"], sampled
        )
        towers = [
            {
                "xy": [round(x, 2), round(y, 2)],
                "height_m": round(control.mhw_to_navd88(spec["tower_height_m"]), 2),
            }
            for x, y in tower_xy
        ]

        bridges.append(
            {
                "id": key,
                "name": spec["name"],
                "deck": deck,
                "deck_width_m": spec["deck_width_m"],
                "towers": towers,
                "tower_form": spec["tower_form"],
                "tower_basis": tower_basis,
                "tower_basis": tower_basis,
                "colours": {
                    "tower": spec["tower_colour"],
                    "cable": spec["cable_colour"],
                    "deck": spec["deck_colour"],
                },
                "length_m": round(total, 1),
                "main_span_m": round(spec["main_span_m"], 1),
                "confidence": "C",
                "source_basis": ["official_dataset", "procedural"],
                "source_refs": ["DSRC-007"],
                "notes": (
                    f"{spec['name']} as district context, not a survey. The centreline and the "
                    f"tower positions derive from OpenStreetMap; the deck profile, cable sag and "
                    f"tower form are a conventional suspension-bridge shape fitted to published "
                    f"dimensions (main span {spec['main_span_m'] / FT:.0f} ft, towers "
                    f"{spec['tower_height_m'] / FT:.0f} ft). {spec['notes_extra']} Graded C: it is "
                    f"the right bridge in the right place at the right size, and nothing about it "
                    f"is dimensionally citable. If this bridge ever gets its own module, that "
                    f"module's proxy replaces this."
                ),
            }
        )
        print(
            f"    {spec['name']}: {total:.0f} m of deck, {len(towers)} towers, "
            f"{len(deck)} deck points"
        )

    return {
        "contract_version": CONTRACT_VERSION,
        "module_id": MODULE_ID,
        "frame_id": FRAME_ID,
        "attribution": "© OpenStreetMap contributors",
        "bridges": bridges,
        "provenance": {
            "generated_at": now(),
            "generated_by": AGENT_ID,
            "control_sha256": control.sha256,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--offline", action="store_true", help="build from the cached fetch only")
    args = parser.parse_args()

    control = DistrictControl()
    print("[context bridges]")
    raw = fetch(args.offline)
    document = build(control, raw)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(document, indent=1), encoding="utf-8")
    print(f"    wrote {OUT.name} ({OUT.stat().st_size / 1024:.0f} KB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
