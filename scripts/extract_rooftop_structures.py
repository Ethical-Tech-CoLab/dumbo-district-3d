"""
Extract DUMBO's rooftop structures from the NYC DCP 3-D Building Model.

Closes DOQ-012. A DUMBO roofline seen from the Manhattan Bridge is bulkheads, stair houses, lift
overruns and timber water tanks; ours was bare decks. The DCP model carries them, because DOITT's
2014 aerial survey resolved roof structure rather than just roof outline.

How they are found. The model gives no "this is a bulkhead" flag -- it gives 15,322 roof polygons
for 8,530 buildings, each polygon planar and at its own height. A polygon whose centroid falls
inside a *lower* polygon is therefore something standing on that lower roof, and its height above
the roof is the difference. That is the whole classifier, and it is a property of the survey rather
than a guess about what buildings look like.

What is written. `data/roofs/rooftop-structures.json`, small and committed, so that the 671 MB
Rhino file is needed once rather than at every build. Each record carries the structure's oriented
footprint, its height above the roof it stands on, and the roof height itself.

Usage:
    python scripts/extract_rooftop_structures.py            # needs data/roofs/*.3dm
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

from district_control import AGENT_ID, DistrictControl, point_in_ring  # noqa: E402

MODEL = Path("data/roofs/NYC_3DModel_BK02.3dm")
OUT = Path("data/roofs/rooftop-structures.json")

# EPSG:2263 is in US survey feet, which is not the international foot. Over the ~2 km the district
# spans the difference is about 4 mm, but the constant is free and being wrong here would be silly.
US_SURVEY_FOOT_M = 1200.0 / 3937.0

# A structure has to clear its roof by this much to be one. Below it the difference is more likely
# to be a parapet step or survey noise than a stair house.
MIN_RISE_M = 1.2
# Above this it is not a rooftop structure, it is a taller part of the building, and modelling it as
# a box sitting on a roof would misdescribe it.
MAX_RISE_M = 20.0
# Ignore slivers. The smallest real thing up there is a scuttle hatch, and we do not want those.
MIN_AREA_M2 = 4.0
# A structure stands on a roof; it does not cover it. Without this, a taller building next door gets
# classified as a bulkhead whenever its centroid happens to fall inside an L-shaped neighbouring
# roof -- which produced a "rooftop structure" of 3,797 m2 on the first run.
MAX_HOST_AREA_FRACTION = 0.5
# And it has to actually sit within the roof's outline, not merely have its middle over it. Checked
# on the vertices rather than the centroid because that is what distinguishes a stair house standing
# on a roof from an adjacent building overlapping it in plan.
MIN_VERTICES_INSIDE_HOST = 0.8


def polygon_area(points: list[tuple[float, float]]) -> float:
    total = 0.0
    n = len(points)
    for i in range(n):
        x1, y1 = points[i]
        x2, y2 = points[(i + 1) % n]
        total += x1 * y2 - x2 * y1
    return abs(total) / 2.0


def centroid(points: list[tuple[float, float]]) -> tuple[float, float]:
    area = 0.0
    cx = 0.0
    cy = 0.0
    n = len(points)
    for i in range(n):
        x1, y1 = points[i]
        x2, y2 = points[(i + 1) % n]
        cross = x1 * y2 - x2 * y1
        area += cross
        cx += (x1 + x2) * cross
        cy += (y1 + y2) * cross
    if abs(area) < 1e-9:
        return (sum(p[0] for p in points) / n, sum(p[1] for p in points) / n)
    area *= 0.5
    return (cx / (6 * area), cy / (6 * area))


def convex_hull(points: list[tuple[float, float]]) -> list[tuple[float, float]]:
    pts = sorted(set(points))
    if len(pts) <= 2:
        return pts

    def cross(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])

    lower: list[tuple[float, float]] = []
    for p in pts:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], p) <= 0:
            lower.pop()
        lower.append(p)
    upper: list[tuple[float, float]] = []
    for p in reversed(pts):
        while len(upper) >= 2 and cross(upper[-2], upper[-1], p) <= 0:
            upper.pop()
        upper.append(p)
    return lower[:-1] + upper[:-1]


def min_area_rect(points: list[tuple[float, float]]) -> tuple[float, float, float, tuple[float, float]]:
    """Smallest enclosing rectangle, by rotating calipers over the hull edges.

    Returns (length, width, yaw_degrees, centre). An axis-aligned bounding box would be wrong here:
    DUMBO's street grid is about 29 degrees off north, so every bulkhead on it would come out
    inflated and square-on to a grid it does not belong to.
    """
    hull = convex_hull(points)
    if len(hull) < 3:
        xs = [p[0] for p in points]
        ys = [p[1] for p in points]
        return (max(xs) - min(xs), max(ys) - min(ys), 0.0, (sum(xs) / len(xs), sum(ys) / len(ys)))

    best = None
    n = len(hull)
    for i in range(n):
        x1, y1 = hull[i]
        x2, y2 = hull[(i + 1) % n]
        edge = math.hypot(x2 - x1, y2 - y1)
        if edge < 1e-9:
            continue
        ux, uy = (x2 - x1) / edge, (y2 - y1) / edge
        us = [p[0] * ux + p[1] * uy for p in hull]
        vs = [-p[0] * uy + p[1] * ux for p in hull]
        du = max(us) - min(us)
        dv = max(vs) - min(vs)
        area = du * dv
        if best is None or area < best[0]:
            cu = (max(us) + min(us)) / 2
            cv = (max(vs) + min(vs)) / 2
            cx = cu * ux - cv * uy
            cy = cu * uy + cv * ux
            best = (area, du, dv, math.degrees(math.atan2(uy, ux)), (cx, cy))
    assert best is not None
    _, du, dv, yaw, centre = best
    return (du, dv, yaw, centre)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", type=Path, default=MODEL)
    parser.add_argument("--out", type=Path, default=OUT)
    args = parser.parse_args()

    if not args.model.exists():
        print(f"model not found: {args.model}")
        print("Download nyc_3dmodel_bk02.zip from the NYC DCP 3-D model page (DSRC-020) and unzip")
        print("it into data/roofs/. It is 92 MB zipped, 672 MB expanded, and deliberately untracked.")
        return 1

    import rhino3dm
    from pyproj import Transformer

    control = DistrictControl()
    ring = [control.geodetic_to_enu(lon, lat)[:2] for lon, lat in control.boundary_ring]
    to_wgs = Transformer.from_crs("EPSG:2263", "EPSG:4326", always_xy=True)

    print(f"[read] {args.model} ({args.model.stat().st_size / 1e6:.0f} MB)")
    model = rhino3dm.File3dm.Read(str(args.model))
    layer = {i: layer_.Name for i, layer_ in enumerate(model.Layers)}

    polygons = []
    for obj in model.Objects:
        if layer.get(obj.Attributes.LayerIndex) != "Building_RoofTop":
            continue
        geom = obj.Geometry
        box = geom.GetBoundingBox()
        # Reject on the centroid before converting every vertex: 15,322 polygons is a lot of
        # projection work to do for the ~7% that turn out to be in the district.
        lon, lat = to_wgs.transform((box.Min.X + box.Max.X) / 2, (box.Min.Y + box.Max.Y) / 2)
        cx, cy, _ = control.geodetic_to_enu(lon, lat)
        if not point_in_ring((cx, cy), ring):
            continue
        pts = []
        for i in range(geom.PointCount):
            p = geom.Point(i)
            plon, plat = to_wgs.transform(p.X, p.Y)
            ex, ey, _ = control.geodetic_to_enu(plon, plat)
            pts.append((ex, ey))
        if len(pts) >= 3 and pts[0] == pts[-1]:
            pts = pts[:-1]
        if len(pts) < 3:
            continue
        polygons.append(
            {
                "xy": pts,
                "z_m": box.Min.Z * US_SURVEY_FOOT_M,
                "area_m2": polygon_area(pts),
                "centroid": centroid(pts),
            }
        )

    print(f"[district] {len(polygons)} roof polygons inside the boundary")

    structures = []
    rejected = {"too_small": 0, "no_host": 0, "rise": 0, "covers_host": 0, "not_contained": 0}
    for poly in polygons:
        if poly["area_m2"] < MIN_AREA_M2:
            rejected["too_small"] += 1
            continue
        # The roof it stands on is the highest polygon that contains it and sits below it.
        host = None
        for other in polygons:
            if other is poly or other["z_m"] >= poly["z_m"] - MIN_RISE_M:
                continue
            if not point_in_ring(poly["centroid"], other["xy"]):
                continue
            if host is None or other["z_m"] > host["z_m"]:
                host = other
        if host is None:
            rejected["no_host"] += 1
            continue
        rise = poly["z_m"] - host["z_m"]
        if rise < MIN_RISE_M or rise > MAX_RISE_M:
            rejected["rise"] += 1
            continue
        if poly["area_m2"] > host["area_m2"] * MAX_HOST_AREA_FRACTION:
            rejected["covers_host"] += 1
            continue
        inside = sum(1 for p in poly["xy"] if point_in_ring(p, host["xy"]))
        if inside < len(poly["xy"]) * MIN_VERTICES_INSIDE_HOST:
            rejected["not_contained"] += 1
            continue
        length, width, yaw, centre = min_area_rect(poly["xy"])
        if length < width:
            length, width = width, length
            yaw += 90.0
        structures.append(
            {
                "xy": [round(centre[0], 2), round(centre[1], 2)],
                "roof_z_m": round(host["z_m"], 2),
                "height_m": round(rise, 2),
                "length_m": round(length, 2),
                "width_m": round(width, 2),
                "yaw_deg": round(((yaw + 180) % 360) - 180, 1),
                "area_m2": round(poly["area_m2"], 1),
            }
        )

    structures.sort(key=lambda s: (-s["height_m"], s["xy"]))
    print(f"[structures] {len(structures)} standing on a lower roof")
    print(f"    rejected: {rejected}")
    if structures:
        heights = sorted(s["height_m"] for s in structures)
        areas = sorted(s["area_m2"] for s in structures)
        print(
            f"    height {heights[0]:.1f}..{heights[-1]:.1f} m, median {heights[len(heights)//2]:.1f}"
        )
        print(f"    plan area {areas[0]:.0f}..{areas[-1]:.0f} m2, median {areas[len(areas)//2]:.0f}")

    digest = hashlib.sha256(args.model.read_bytes()).hexdigest()
    document = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "generated_by": AGENT_ID,
        "source_refs": ["DSRC-020"],
        "source_basis": ["official_dataset"],
        "confidence": "B",
        "notes": (
            "Rooftop structures over DUMBO, extracted from the NYC DCP 3-D Building Model. Position, "
            "plan extent, orientation and height are measured from the model, which derives from "
            "DOITT's 2014 aerial survey. Graded B rather than A because the survey is from 2014 and "
            "because each structure is reduced to its minimum-area rectangle: a stair bulkhead is a "
            "box to within a few tens of centimetres, a water tank on a steel frame is not. What "
            "kind of structure each one is was not surveyed and is not claimed."
        ),
        "source_model": {
            "path": str(args.model).replace("\\", "/"),
            "sha256": digest,
            "bytes": args.model.stat().st_size,
        },
        "criteria": {
            "min_rise_m": MIN_RISE_M,
            "max_rise_m": MAX_RISE_M,
            "min_area_m2": MIN_AREA_M2,
            "max_host_area_fraction": MAX_HOST_AREA_FRACTION,
            "min_vertices_inside_host": MIN_VERTICES_INSIDE_HOST,
        },
        "count": len(structures),
        "structures": structures,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(document, indent=1), encoding="utf-8")
    print(f"[wrote] {args.out} ({args.out.stat().st_size / 1024:.0f} KB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
