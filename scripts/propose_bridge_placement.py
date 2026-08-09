"""
Propose a georeferenced placement for the Manhattan Bridge, and emit a stand-in module manifest.

Why this exists
---------------
`manhattan-bridge-3d/GEOMETRY-CONTROL.md` authors the bridge in its own frame: origin at the midpoint
of the main span, +X toward Brooklyn, +Z up, z = 0 at mean high water. Its open question OQ-009 records
that the real-world azimuth and geodetic anchor of that frame are unregistered.

Until the bridge team registers them, the district cannot place the bridge. Rather than guess, this
script derives a placement from the bridge's mapped centerline and publishes it as **provisional**,
confidence D, tagged with DOQ-001. It is a starting point for the bridge team to ratify or correct,
not a survey claim, and the viewer labels it as such.

It also writes a placeholder `bridge-manifest.json` so the district's optional dependency on the
bridge module resolves today. When the bridge team publishes a real manifest, point the district at it
and delete this file: nothing else changes.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from datetime import datetime, timezone
from pathlib import Path

from district_control import AGENT_ID, DistrictControl
from ingest_sources import _overpass

REPO_ROOT = Path(__file__).resolve().parent.parent
OUT = REPO_ROOT / "viewer" / "public" / "modules" / "manhattan-bridge"
RAW = REPO_ROOT / "data" / "streets" / "osm-manhattan-bridge.raw.json"

CONTRACT_VERSION = "1.0.0"
BRIDGE_MODULE = "manhattan-bridge"
FRAME_ID = "nyc-harbor-enu"

# From manhattan-bridge-3d/GEOMETRY-CONTROL.md. Quoted, not re-derived: this repository does not own
# these numbers and must never appear to.
BRIDGE_MAIN_SPAN_FT = 1470.0      # CTL-005
BRIDGE_TOWER_HEIGHT_FT = 322.0    # CTL-007
BRIDGE_TOTAL_LENGTH_FT = 6855.0   # CTL-001
FT = 0.3048


def fetch_centerline() -> list[dict]:
    if RAW.is_file():
        return json.loads(RAW.read_text(encoding="utf-8"))

    query = (
        "[out:json][timeout:180];"
        '(way["man_made"="bridge"]["name"="Manhattan Bridge"](40.700,-74.005,40.716,-73.980);'
        'way["highway"]["name"="Manhattan Bridge"](40.700,-74.005,40.716,-73.980);'
        'way["bridge"]["name"~"^Manhattan Bridge"](40.700,-74.005,40.716,-73.980););'
        "out geom tags;"
    )
    result = _overpass(query)
    ways = [e for e in result.get("elements", []) if e.get("geometry")]

    RAW.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(ways, indent=1)
    RAW.write_text(text, encoding="utf-8")

    # Same audit sidecar every other fetch writes, so the source register stays honest.
    RAW.with_suffix(RAW.suffix + ".source.json").write_text(
        json.dumps(
            {
                "source_id": "DSRC-007",
                "query": query,
                "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "fetched_by": AGENT_ID,
                "bytes": len(text),
                "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
                "record_count": len(ways),
                "note": (
                    "OpenStreetMap contributors, ODbL. Manhattan Bridge centreline, used ONLY to "
                    "derive a provisional placement (DOQ-001 / OQ-009). Not bridge geometry."
                ),
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    return ways


def principal_axis(points: list[tuple[float, float]]) -> tuple[float, tuple[float, float]]:
    """
    Best-fit direction of a point set, by the eigenvector of the 2x2 covariance matrix.

    Returns (azimuth_deg_from_north, centroid). Solved in closed form because a 2x2 symmetric
    eigenproblem does not need an iterative solver and a dependency-free script is worth keeping.
    """
    n = len(points)
    cx = sum(p[0] for p in points) / n
    cy = sum(p[1] for p in points) / n

    sxx = sum((p[0] - cx) ** 2 for p in points) / n
    syy = sum((p[1] - cy) ** 2 for p in points) / n
    sxy = sum((p[0] - cx) * (p[1] - cy) for p in points) / n

    # Principal direction of a 2x2 covariance matrix.
    theta = 0.5 * math.atan2(2.0 * sxy, sxx - syy)
    dx, dy = math.cos(theta), math.sin(theta)

    azimuth = math.degrees(math.atan2(dx, dy)) % 360.0
    return azimuth, (cx, cy)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write", action="store_true", help="Write the placeholder manifest.")
    args = parser.parse_args()

    control = DistrictControl()
    ways = fetch_centerline()
    if not ways:
        raise SystemExit("no Manhattan Bridge centerline found; cannot propose a placement")

    points: list[tuple[float, float]] = []
    for way in ways:
        for point in way["geometry"]:
            x, y, _ = control.geodetic_to_enu(float(point["lon"]), float(point["lat"]))
            points.append((x, y))
    print(f"centerline points : {len(points)} from {len(ways)} way(s)")

    azimuth, centroid = principal_axis(points)
    # The bridge's +X points toward Brooklyn, which is the south-east end of the axis. Choose the
    # sense of the fitted axis that has a positive component toward the Brooklyn end of the data.
    brooklyn_end = min(points, key=lambda p: p[1])
    manhattan_end = max(points, key=lambda p: p[1])
    axis_dx = brooklyn_end[0] - manhattan_end[0]
    axis_dy = brooklyn_end[1] - manhattan_end[1]
    axis_azimuth = math.degrees(math.atan2(axis_dx, axis_dy)) % 360.0
    if abs(((axis_azimuth - azimuth + 180) % 360) - 180) > 90:
        azimuth = (azimuth + 180) % 360

    span_m = math.dist(brooklyn_end, manhattan_end)
    lon, lat, _ = control.enu_to_geodetic(centroid[0], centroid[1])

    # The bridge frame's z = 0 is mean high water. The district frame is NAVD88.
    mhw_offset = control.value_m("DCTL-010")

    # yaw_deg in the shared contract rotates the module's +X onto scene East, counter-clockwise
    # looking down. The bridge's +X runs along the axis toward Brooklyn, whose compass azimuth we
    # just measured; converting a compass azimuth to a CCW-from-East angle is 90 - azimuth.
    yaw_deg = (90.0 - azimuth) % 360.0

    print(f"fitted axis       : azimuth {azimuth:.2f} deg from north (toward Brooklyn)")
    print(f"yaw for contract  : {yaw_deg:.2f} deg CCW from East")
    print(f"mapped extent     : {span_m:.0f} m  (bridge CTL-001 total is {BRIDGE_TOTAL_LENGTH_FT * FT:.0f} m)")
    print(f"centroid          : lon {lon:.6f} lat {lat:.6f}")
    print(f"scene translation : [{centroid[0]:.1f}, {centroid[1]:.1f}, {mhw_offset:.2f}]")

    placement = {
        "frame": FRAME_ID,
        "translation_m": [round(centroid[0], 2), round(centroid[1], 2), round(mhw_offset, 3)],
        "yaw_deg": round(yaw_deg, 3),
        "scale": 1,
        "confidence": "D",
        "provisional": True,
        "open_questions": ["OQ-009", "DOQ-001"],
        "notes": (
            "PROPOSED BY dumbo-district-3d, NOT RATIFIED BY THE BRIDGE TEAM. "
            f"Azimuth {azimuth:.2f} deg from north is the principal axis of the bridge centerline as "
            "mapped by OpenStreetMap (DSRC-007), which is community mapping and not a survey. The "
            "translation is the centroid of that mapped centerline, which approximates but is not "
            "proven to equal the midpoint of the main span that manhattan-bridge-3d uses as its "
            "origin. The z term is DCTL-010, converting the bridge's mean-high-water zero onto this "
            "frame's NAVD88 zero; that part IS grade A and should be adopted regardless of what "
            "happens to the horizontal terms."
        ),
    }

    manifest = {
        "contract_version": CONTRACT_VERSION,
        "module_id": BRIDGE_MODULE,
        "title": "Manhattan Bridge (placeholder manifest)",
        "subtitle": "Stand-in published by dumbo-district-3d so the optional dependency resolves",
        "module_version": "0.0.0",
        "owner": {"team": "Manhattan Bridge", "repository": "manhattan-bridge-3d"},
        "authoritative_for": [
            "bridge geometry",
            "bridge control dimensions",
            "bridge component taxonomy",
            "bridge photogrammetry",
            "bridge engineering detail",
        ],
        "georeference": {"url": "../../frames/nyc-harbor-enu.json"},
        "placement": placement,
        "lod_ladder": {
            "contract_version": CONTRACT_VERSION,
            "module_id": BRIDGE_MODULE,
            "ladder_id": "manhattan-bridge-ladder",
            "levels": [
                {
                    "level": 0,
                    "name": "control skeleton",
                    "intent": "inspect",
                    "max_geometric_error_m": 0.01,
                    "representation": "cad_solid",
                    "payload_format": "glb",
                    "selectable": True,
                    "carries_metadata": True,
                    "notes": "Mirrors the existing control_skeleton.glb. Not shipped by this placeholder.",
                },
                {
                    "level": 2,
                    "name": "district proxy",
                    "intent": "context",
                    "max_geometric_error_m": 8.0,
                    "representation": "block",
                    "payload_format": "none",
                    "selectable": False,
                    "carries_metadata": False,
                    "notes": (
                        "NOT SHIPPED. The district renders a labelled wireframe placeholder at this "
                        "level until the bridge team exports a real proxy."
                    ),
                },
            ],
            "selection": {
                "policy": "screen_space_error",
                "default_sse_budget_px": 12,
                "mode_sse_budget_px": {"inspect": 2, "walk": 12, "map": 48, "tour": 8},
                "hysteresis": 0.15,
            },
        },
        "modes": ["inspect", "walk", "map", "tour"],
        "proxy": {
            "asset_id": f"urn:d3d:{BRIDGE_MODULE}:bridge_proxy",
            "max_level": 2,
            "notes": (
                "Placeholder. Caps how far a consuming viewer may refine bridge content while the "
                "bridge is scenery rather than the subject."
            ),
        },
        "handoff": {
            "supported": True,
            "target_mode": "inspect",
            "preserve_camera": True,
            "entry_points": [
                {
                    "entry_id": "brooklyn_tower",
                    "label": "Inspect the Brooklyn tower",
                    "focus_asset": f"urn:d3d:{BRIDGE_MODULE}:tower_brooklyn_envelope",
                },
                {
                    "entry_id": "brooklyn_anchorage",
                    "label": "Inspect the Brooklyn anchorage",
                    "focus_asset": f"urn:d3d:{BRIDGE_MODULE}:anchorage_brooklyn",
                },
            ],
        },
        "attribution": ["Manhattan Bridge model: manhattan-bridge-3d"],
        "not_implemented_yet": [
            "THIS IS A PLACEHOLDER MANIFEST written by dumbo-district-3d, not by the bridge team",
            "no geometry is published; the district draws a labelled wireframe envelope instead",
            "placement is provisional and unratified (OQ-009 / DOQ-001)",
        ],
        "provenance": {
            "module_id": "dumbo-district",
            "generated_by": AGENT_ID,
            "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "source_documents": [{"path": control.path.name, "sha256": control.sha256}],
        },
    }

    # Envelope the district draws in place of real geometry, derived from the bridge team's own
    # published control dimensions so the placeholder is at least the right size. It lives under
    # `extensions` because it is a district convention, not part of the shared manifest contract.
    manifest["extensions"] = {
        "dumbo-district": {
            "placeholder_envelope": {
                "length_m": round(BRIDGE_TOTAL_LENGTH_FT * FT, 1),
                "main_span_m": round(BRIDGE_MAIN_SPAN_FT * FT, 1),
                "tower_height_m": round(BRIDGE_TOWER_HEIGHT_FT * FT, 1),
                "deck_width_m": 36.0,
                "source": "manhattan-bridge-3d/GEOMETRY-CONTROL.md CTL-001, CTL-005, CTL-007",
            }
        }
    }

    if args.write:
        OUT.mkdir(parents=True, exist_ok=True)
        path = OUT / "bridge-manifest.json"
        path.write_text(json.dumps(manifest, indent=1), encoding="utf-8")
        print(f"\nwrote {path.relative_to(REPO_ROOT)}")
    else:
        print("\n(dry run; pass --write to emit the placeholder manifest)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
