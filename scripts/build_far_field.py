"""
Build the far-field environment: the Manhattan skyline across the river, the water surface, and
the vessels on it.

Two decisions worth stating, because both had a tempting shortcut:

1. **The skyline is real geometry, not a photograph.** A painted backdrop would be quicker and
   would be a lie: it would not parallax correctly as you walk, and it would be traceable to
   nothing. These are the same authoritative NYC footprints used for the district itself
   (DSRC-011), simplified hard and rendered as silhouettes, because at 1-3 km nobody can resolve
   more and pretending otherwise would cost frame time for no perceptual gain.

2. **Vessels follow real routes.** Ferry landings and route lines come from OpenStreetMap
   (DSRC-012), so the East River traffic goes where East River traffic actually goes. Recreational
   craft are seasonal and are explicitly invented, so they are graded D and confined to a declared
   activity area.

Outputs, under viewer/public/district/:
  horizon.json   distant skyline silhouettes
  water.json     water surface extent, vessel routes, and seasonal craft rules
"""

from __future__ import annotations

import argparse
import json
import math
from datetime import datetime, timezone
from pathlib import Path

from district_control import AGENT_ID, DistrictControl

REPO_ROOT = Path(__file__).resolve().parent.parent
DATA = REPO_ROOT / "data"
OUT = REPO_ROOT / "viewer" / "public" / "district"

CONTRACT_VERSION = "1.0.0"
MODULE_ID = "dumbo-district"
FRAME_ID = "nyc-harbor-enu"
FT = 0.3048


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def provenance(control: DistrictControl) -> dict:
    return {
        "module_id": MODULE_ID,
        "generated_by": AGENT_ID,
        "generated_at": now(),
        "source_documents": [{"path": control.path.name, "sha256": control.sha256}],
    }


def load(path: Path) -> object:
    if not path.is_file():
        raise SystemExit(f"missing {path.relative_to(REPO_ROOT)}; run ingest_sources.py first")
    return json.loads(path.read_text(encoding="utf-8"))


def write(path: Path, payload: object) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, separators=(",", ":"))
    path.write_text(text, encoding="utf-8")
    return len(text.encode("utf-8"))


def _as_float(value: object) -> float | None:
    try:
        result = float(str(value))
        return result if math.isfinite(result) else None
    except (TypeError, ValueError):
        return None


# ----------------------------------------------------------------- horizon


def build_horizon(control: DistrictControl) -> dict:
    """
    Reduce distant buildings to silhouette blocks.

    At 1-3 km a building is a few pixels wide. Its footprint is irrelevant; only its width, height
    and position matter. So each is collapsed to an oriented box footprint, which is roughly a
    sixteenth of the data and renders as a single merged mesh.

    Buildings are also thinned by prominence: at that range a 25 m building behind a 200 m one
    contributes nothing but triangles.
    """
    records = load(DATA / "horizon" / "manhattan-skyline.raw.json")
    valid_radius = control.value_m("DCTL-004")

    blocks: list[dict] = []
    skipped_far = 0
    skipped_small = 0

    for record in records:
        geom = record.get("the_geom") or {}
        polygons = (
            geom.get("coordinates", [])
            if geom.get("type") == "MultiPolygon"
            else [geom.get("coordinates", [])]
        )
        ring_lonlat = None
        for polygon in polygons:
            if polygon and len(polygon[0]) >= 4:
                ring_lonlat = polygon[0]
                break
        if not ring_lonlat:
            continue

        roof_ft = _as_float(record.get("height_roof")) or 0.0
        ground_ft = _as_float(record.get("ground_elevation")) or 0.0
        height_m = roof_ft * FT
        base_m = ground_ft * FT

        points = []
        for point in ring_lonlat:
            x, y, _ = control.geodetic_to_enu(float(point[0]), float(point[1]))
            points.append((x, y))

        cx = sum(p[0] for p in points) / len(points)
        cy = sum(p[1] for p in points) / len(points)
        distance = math.hypot(cx, cy)

        if distance > valid_radius:
            # Outside the frame's declared validity radius; placing geometry there would be a
            # claim the coordinate system does not support.
            skipped_far += 1
            continue

        # Prominence test: apparent height in milliradians. Keeps the towers that define the
        # skyline and drops the infill nobody can see.
        apparent = height_m / max(distance, 1.0)
        if apparent < 0.012:
            skipped_small += 1
            continue

        min_x = min(p[0] for p in points)
        max_x = max(p[0] for p in points)
        min_y = min(p[1] for p in points)
        max_y = max(p[1] for p in points)

        blocks.append(
            {
                "c": [round(cx, 1), round(cy, 1)],
                "w": round(max(max_x - min_x, 4.0), 1),
                "d": round(max(max_y - min_y, 4.0), 1),
                "b": round(base_m, 1),
                "h": round(height_m, 1),
            }
        )

    # Far to near, so the renderer can merge them in depth order.
    blocks.sort(key=lambda b: -(b["c"][0] ** 2 + b["c"][1] ** 2))

    print(f"    {len(blocks)} skyline blocks ({skipped_small} below prominence, {skipped_far} beyond frame)")

    return {
        "contract_version": CONTRACT_VERSION,
        "module_id": MODULE_ID,
        "frame_id": FRAME_ID,
        "source_refs": ["DSRC-011"],
        "confidence": "B",
        "open_questions": ["DOQ-008"],
        "notes": (
            "Distant skyline as oriented silhouette blocks. Positions and heights are grade A from "
            "the NYC footprint dataset; the reduction to a bounding block is a deliberate "
            "simplification for objects 1-3 km away, so the rendered result is graded B. Never "
            "selectable and never dimensionally citable at this range."
        ),
        "max_geometric_error_m": 25.0,
        "blocks": blocks,
        "provenance": provenance(control),
    }


# ------------------------------------------------------------------- water


def build_water(control: DistrictControl) -> dict:
    """
    Water surface, ferry routes and seasonal recreational craft.

    Ferry routes are real: OSM ferry ways in the East River, clipped to the frame. Recreational
    craft are not, and are marked as such — sailboats and jetskis are placed procedurally inside a
    declared activity area, seasonally, because an empty river in July reads as wrong even though
    an invented sailboat is not evidence of anything.
    """
    features = load(DATA / "streetscape" / "ferry.raw.json")
    valid_radius = control.value_m("DCTL-004")

    routes: list[dict] = []
    terminals: list[dict] = []

    for feature in features:
        tags = feature.get("tags") or {}
        name = tags.get("name")

        if tags.get("route") == "ferry" and feature.get("geometry"):
            path = []
            for lon, lat in feature["geometry"]:
                x, y, _ = control.geodetic_to_enu(lon, lat)
                if math.hypot(x, y) > valid_radius:
                    continue
                path.append([round(x, 1), round(y, 1)])
            # Two points inside the frame is the minimum for a usable heading.
            if len(path) >= 2:
                length = sum(
                    math.dist(path[i], path[i + 1]) for i in range(len(path) - 1)
                )
                if length > 200:
                    routes.append({"name": name, "path": path, "length_m": round(length, 1)})

        if tags.get("amenity") == "ferry_terminal" and feature.get("lon") is not None:
            x, y, _ = control.geodetic_to_enu(feature["lon"], feature["lat"])
            if math.hypot(x, y) <= valid_radius:
                terminals.append({"name": name, "xy": [round(x, 1), round(y, 1)]})

    # Deduplicate terminals that OSM maps as both a node and a way.
    seen: set[str] = set()
    unique_terminals = []
    for terminal in terminals:
        key = f"{terminal['name']}:{round(terminal['xy'][0] / 50)}:{round(terminal['xy'][1] / 50)}"
        if key in seen:
            continue
        seen.add(key)
        unique_terminals.append(terminal)

    print(f"    {len(routes)} ferry routes, {len(unique_terminals)} terminals")

    # Recreational activity area: the open water north-west of the district, away from the shipping
    # lanes and the bridge spans. Declared explicitly so the invented content has a boundary.
    recreation_area = {
        "center_xy": [-900.0, 1300.0],
        "radius_m": 900.0,
        "notes": "Open water off the DUMBO and Brooklyn Heights shoreline.",
    }

    return {
        "contract_version": CONTRACT_VERSION,
        "module_id": MODULE_ID,
        "frame_id": FRAME_ID,
        "source_refs": ["DSRC-012"],
        "attribution": "© OpenStreetMap contributors, ODbL",
        "surface": {
            "datum": "MHW",
            "elevation_m": round(control.value_m("DCTL-010"), 3),
            "notes": (
                "Water is rendered at mean high water. This frame's zero is NAVD88, so the surface "
                "sits DCTL-010 above it. Same number the Manhattan Bridge placement uses."
            ),
        },
        "confidence": "C",
        "open_questions": ["DOQ-009"],
        "routes": routes,
        "terminals": unique_terminals,
        "vessels": {
            "ferry": {
                "kind": "ferry",
                "length_m": 26.0,
                "beam_m": 8.0,
                "speed_mps": 8.0,
                "count": min(4, max(1, len(routes))),
                "confidence": "C",
                "seasons": ["winter", "spring", "summer", "autumn"],
                "notes": (
                    "NYC Ferry vessels run year round. Routes are real (DSRC-012); vessel "
                    "dimensions are nominal and the schedule is not modelled, so movement is "
                    "plausible traffic rather than a timetable."
                ),
            },
            "sailboat": {
                "kind": "sailboat",
                "length_m": 9.0,
                "beam_m": 3.0,
                "speed_mps": 2.4,
                "count": 7,
                "confidence": "D",
                "seasons": ["spring", "summer", "autumn"],
                "area": recreation_area,
                "notes": "Invented. Seasonal recreational traffic, placed procedurally in the declared area.",
            },
            "jetski": {
                "kind": "jetski",
                "length_m": 3.2,
                "beam_m": 1.2,
                "speed_mps": 11.0,
                "count": 4,
                "confidence": "D",
                "seasons": ["summer"],
                "area": recreation_area,
                "notes": "Invented. Warm-season only.",
            },
        },
        "notes": (
            "Ferry routes and terminals are grade B from OpenStreetMap. Vessel movement along them "
            "is plausible traffic, not a timetable, so it is graded C. Recreational craft are "
            "invented and graded D; they are decorative and never citable."
        ),
        "provenance": provenance(control),
    }


# ---------------------------------------------------------------------- main


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--horizon", action="store_true")
    parser.add_argument("--water", action="store_true")
    args = parser.parse_args()
    run_all = not (args.horizon or args.water)

    control = DistrictControl()
    print(f"district control : {control.path.name} @ {control.sha256[:12]}")

    if run_all or args.horizon:
        print("[horizon]")
        size = write(OUT / "horizon.json", build_horizon(control))
        print(f"    wrote horizon.json ({size / 1024:.0f} KB)")

    if run_all or args.water:
        print("[water]")
        size = write(OUT / "water.json", build_water(control))
        print(f"    wrote water.json ({size / 1024:.0f} KB)")

    print("\nfar-field build complete")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
