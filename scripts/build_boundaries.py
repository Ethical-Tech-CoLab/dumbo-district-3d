"""
Build the district boundary, hero zone and tile grid, all from DUMBO-GEOSPATIAL-CONTROL.md.

Outputs:
  data/boundaries/dumbo-district.geojson   district boundary polygon (WGS84, for the map view)
  data/boundaries/hero-zone.geojson        hero fidelity corridors (WGS84)
  data/tiles/tile-index.json               tile index conforming to the shared tile-index schema

Nothing here is hand-tuned. Change the control document, re-run, and every artefact follows.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from district_control import (
    AGENT_ID,
    DistrictControl,
    distance_point_to_segment,
    point_in_ring,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
DATA = REPO_ROOT / "data"

CONTRACT_VERSION = "1.0.0"
MODULE_ID = "dumbo-district"
FRAME_ID = "nyc-harbor-enu"
LADDER_ID = "dumbo-district-ladder"


def _provenance(control: DistrictControl) -> dict:
    return {
        "module_id": MODULE_ID,
        "generated_by": AGENT_ID,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source_documents": [
            {"path": control.path.name, "sha256": control.sha256},
        ],
    }


def build_boundaries(control: DistrictControl) -> None:
    ring = control.boundary_ring
    district = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "geometry": {"type": "Polygon", "coordinates": [[list(p) for p in ring]]},
                "properties": {
                    "name": "DUMBO district project boundary",
                    "module_id": MODULE_ID,
                    "definition": "DUMBO-GEOSPATIAL-CONTROL.md section 2.1",
                    "control_sha256": control.sha256,
                    "confidence": "A",
                    "note": (
                        "Project scope definition, not an administrative boundary. "
                        "NYC NTA BK0202 is recorded as context in DSRC-004 and is deliberately not used."
                    ),
                    "vertices": [v.vertex_id for v in control.boundary],
                },
            }
        ],
    }
    _write_json(DATA / "boundaries" / "dumbo-district.geojson", district)

    half = control.value_m("DCTL-030")
    features = []
    for line in control.hero_lines:
        features.append(
            {
                "type": "Feature",
                "geometry": {
                    "type": "LineString",
                    "coordinates": [list(line.a), list(line.b)],
                },
                "properties": {
                    "hero_id": line.hero_id,
                    "name": line.name,
                    "halfwidth_m": half,
                    "confidence": "A",
                },
            }
        )
    _write_json(
        DATA / "boundaries" / "hero-zone.geojson",
        {"type": "FeatureCollection", "features": features},
    )


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=1), encoding="utf-8")
    print(f"    wrote {path.relative_to(REPO_ROOT)}")


def build_tile_grid(control: DistrictControl) -> dict:
    """
    Lay a square grid over the district in scene ENU meters.

    Tiles are classified by fidelity zone: 'hero' if they touch a hero corridor, 'walkable' if they
    intersect the district boundary, 'context' if they are merely nearby. The zone drives which LOD
    levels get built for the tile, not which level the viewer picks at runtime.
    """
    tile_size = control.value_m("DCTL-040")
    half = control.value_m("DCTL-030")

    ring_enu = [control.geodetic_to_enu(lon, lat)[:2] for lon, lat in control.boundary_ring]
    xs = [p[0] for p in ring_enu]
    ys = [p[1] for p in ring_enu]

    # Pad by one tile so context tiles exist around the edge.
    min_x = (min(xs) // tile_size - 1) * tile_size
    min_y = (min(ys) // tile_size - 1) * tile_size
    max_x = (max(xs) // tile_size + 2) * tile_size
    max_y = (max(ys) // tile_size + 2) * tile_size

    cols = int(round((max_x - min_x) / tile_size))
    rows = int(round((max_y - min_y) / tile_size))

    hero_segments = [
        (
            control.geodetic_to_enu(*line.a)[:2],
            control.geodetic_to_enu(*line.b)[:2],
        )
        for line in control.hero_lines
    ]

    ring_closed = [(p[0], p[1]) for p in ring_enu]

    tiles = []
    for col in range(cols):
        for row in range(rows):
            x0 = min_x + col * tile_size
            y0 = min_y + row * tile_size
            x1 = x0 + tile_size
            y1 = y0 + tile_size
            centre = ((x0 + x1) / 2.0, (y0 + y1) / 2.0)

            corners = [(x0, y0), (x1, y0), (x1, y1), (x0, y1), centre]
            inside = any(point_in_ring(c, ring_closed) for c in corners)

            hero_distance = min(
                distance_point_to_segment(centre, a, b) for a, b in hero_segments
            )
            # A tile counts as hero if any part of it can fall within the corridor half-width.
            hero = hero_distance <= half + tile_size * 0.7071

            if hero and inside:
                zone = "hero"
            elif inside:
                zone = "walkable"
            else:
                zone = "context"

            lon0, lat0, _ = control.enu_to_geodetic(x0, y0)
            lon1, lat1, _ = control.enu_to_geodetic(x1, y1)

            tiles.append(
                {
                    "tile_id": f"t_{col}_{row}",
                    "col": col,
                    "row": row,
                    "bbox": {
                        "frame": FRAME_ID,
                        "min": [round(x0, 3), round(y0, 3), -5.0],
                        "max": [round(x1, 3), round(y1, 3), 120.0],
                    },
                    "bbox_geodetic": [
                        round(lon0, 7), round(lat0, 7), round(lon1, 7), round(lat1, 7)
                    ],
                    "zone": zone,
                    "asset_count": 0,
                    "content": [],
                }
            )

    index = {
        "contract_version": CONTRACT_VERSION,
        "module_id": MODULE_ID,
        "frame_id": FRAME_ID,
        "ladder_id": LADDER_ID,
        "base_url": "./tiles/",
        "scheme": {
            "kind": "planar_grid",
            "tile_size_m": tile_size,
            "origin_xy_m": [round(min_x, 3), round(min_y, 3)],
            "grid_size": [cols, rows],
            "id_pattern": "t_{col}_{row}",
        },
        "streaming": {
            "load_radius_m": control.value_m("DCTL-041"),
            "unload_radius_m": control.value_m("DCTL-042"),
            "prefetch_along_heading_m": control.value_m("DCTL-043"),
            "max_concurrent_requests": 6,
        },
        "tiles": tiles,
        "provenance": _provenance(control),
    }

    counts: dict[str, int] = {}
    for tile in tiles:
        counts[tile["zone"]] = counts.get(tile["zone"], 0) + 1
    print(f"    grid {cols} x {rows} = {len(tiles)} tiles at {tile_size:g} m")
    print(f"    zones {counts}")

    _write_json(DATA / "tiles" / "tile-index.json", index)
    return index


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--boundaries", action="store_true")
    parser.add_argument("--tiles", action="store_true")
    args = parser.parse_args()
    run_all = not (args.boundaries or args.tiles)

    control = DistrictControl()
    print(f"district control : {control.path.name} @ {control.sha256[:12]}")

    if run_all or args.boundaries:
        print("[boundaries]")
        build_boundaries(control)
    if run_all or args.tiles:
        print("[tile grid]")
        build_tile_grid(control)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
