"""
Build the DUMBO district's browser payloads from the ingested sources.

Reads:
  data/footprints/footprints.raw.json   DSRC-001, clipped to the district
  data/pluto/pluto.raw.json             DSRC-002, joined on BBL
  data/streets/osm-ways.raw.json        DSRC-007, walk network
  data/streets/osm-landmarks.raw.json   DSRC-007, named places used as tour targets
  data/tiles/tile-index.json            from build_boundaries.py

Writes, all under viewer/public/district/:
  georeference.json      the shared scene frame, per georeference.schema.json
  lod.json               the district LOD ladder
  source-register.json   evidence, per source-confidence.schema.json
  asset-registry.json    landmarks, the district tileset, and the bridge proxy reference
  tile-index.json        tile index with populated content
  district-manifest.json the module manifest, the whole consumable surface
  walk-network.json      pedestrian graph used for routing and ground snapping
  tiles/t_c_r.lodN.json  per-tile building payloads
  build-report.json      counts, confidence histogram, and every open question hit

Payload format note. Levels 0-2 ship as JSON footprint rings plus heights, not GLB. For extruded
footprints that is the smaller and more honest representation: a GLB would bake the extrusion, roughly
triple the bytes, and throw away the ability to re-extrude at a different level. The shared contract
allows `payload_format: "json"` with `representation: "extruded_footprint"` precisely so a module can
make this trade explicitly rather than silently.
"""

from __future__ import annotations

import argparse
import json
import math
from datetime import datetime, timezone
from pathlib import Path

from district_control import AGENT_ID, DistrictControl, point_in_ring

REPO_ROOT = Path(__file__).resolve().parent.parent
DATA = REPO_ROOT / "data"
OUT = REPO_ROOT / "viewer" / "public" / "district"

CONTRACT_VERSION = "1.0.0"
MODULE_ID = "dumbo-district"
FRAME_ID = "nyc-harbor-enu"
LADDER_ID = "dumbo-district-ladder"
BRIDGE_MODULE = "manhattan-bridge"
BRIDGE_PROXY_URN = f"urn:d3d:{BRIDGE_MODULE}:bridge_proxy"

FT = 0.3048  # international foot, DCTL-061

# Which LOD levels are built for each fidelity zone.
ZONE_LEVELS = {
    "hero": [0, 1, 2],
    "walkable": [1, 2],
    "context": [2],
    "outside": [],
}

# Ring simplification tolerance per level, in meters.
SIMPLIFY_M = {0: 0.0, 1: 1.0, 2: 4.0}


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def provenance(control: DistrictControl, extra: list[dict] | None = None) -> dict:
    docs = [{"path": control.path.name, "sha256": control.sha256}]
    docs.extend(extra or [])
    return {
        "module_id": MODULE_ID,
        "generated_by": AGENT_ID,
        "generated_at": now(),
        "source_documents": docs,
    }


def write_json(path: Path, payload: object, *, compact: bool = False) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, separators=(",", ":")) if compact else json.dumps(payload, indent=1)
    path.write_text(text, encoding="utf-8")
    return len(text.encode("utf-8"))


# ------------------------------------------------------------------ geometry


def simplify(points: list[tuple[float, float]], tolerance: float) -> list[tuple[float, float]]:
    """Ramer-Douglas-Peucker on an open ring."""
    if tolerance <= 0 or len(points) < 3:
        return points

    def rdp(pts: list[tuple[float, float]]) -> list[tuple[float, float]]:
        if len(pts) < 3:
            return pts
        ax, ay = pts[0]
        bx, by = pts[-1]
        dx, dy = bx - ax, by - ay
        norm = math.hypot(dx, dy)
        worst = 0.0
        index = 0
        for i in range(1, len(pts) - 1):
            px, py = pts[i]
            if norm == 0.0:
                distance = math.hypot(px - ax, py - ay)
            else:
                distance = abs(dy * px - dx * py + bx * ay - by * ax) / norm
            if distance > worst:
                worst = distance
                index = i
        if worst <= tolerance:
            return [pts[0], pts[-1]]
        return rdp(pts[: index + 1])[:-1] + rdp(pts[index:])

    return rdp(points)


def ring_area(points: list[tuple[float, float]]) -> float:
    total = 0.0
    for i in range(len(points)):
        x1, y1 = points[i]
        x2, y2 = points[(i + 1) % len(points)]
        total += x1 * y2 - x2 * y1
    return total / 2.0


def oriented_box(points: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """
    Minimum-area rectangle by rotating-calipers over edge directions.

    Used for LOD2 context massing: a block that preserves the building's footprint area and
    orientation while costing four vertices.
    """
    if len(points) < 3:
        return points
    best = None
    for i in range(len(points)):
        x1, y1 = points[i]
        x2, y2 = points[(i + 1) % len(points)]
        angle = math.atan2(y2 - y1, x2 - x1)
        c, s = math.cos(-angle), math.sin(-angle)
        xs = [p[0] * c - p[1] * s for p in points]
        ys = [p[0] * s + p[1] * c for p in points]
        w = max(xs) - min(xs)
        h = max(ys) - min(ys)
        if best is None or w * h < best[0]:
            best = (w * h, angle, min(xs), max(xs), min(ys), max(ys))
    _, angle, x0, x1b, y0, y1b = best
    c, s = math.cos(angle), math.sin(angle)
    corners = [(x0, y0), (x1b, y0), (x1b, y1b), (x0, y1b)]
    return [(x * c - y * s, x * s + y * c) for x, y in corners]


# ------------------------------------------------------------------ buildings


def load_json(path: Path) -> object:
    if not path.is_file():
        raise SystemExit(f"missing input {path.relative_to(REPO_ROOT)}; run ingest_sources.py first")
    return json.loads(path.read_text(encoding="utf-8"))


def build_buildings(control: DistrictControl) -> tuple[list[dict], dict]:
    footprints = load_json(DATA / "footprints" / "footprints.raw.json")
    pluto_rows = load_json(DATA / "pluto" / "pluto.raw.json")

    pluto = {}
    for row in pluto_rows:
        bbl = str(row.get("bbl") or "").split(".")[0]
        if bbl:
            pluto[bbl] = row

    storey = control.value_m("DCTL-062")
    fallback_height = control.value_m("DCTL-063")

    buildings: list[dict] = []
    stats = {
        "height_from_dataset": 0,
        "height_from_floors": 0,
        "height_fallback": 0,
        "pluto_joined": 0,
        "skipped_no_geometry": 0,
    }

    for record in footprints:
        geom = record.get("the_geom") or {}
        polygons = (
            geom.get("coordinates", [])
            if geom.get("type") == "MultiPolygon"
            else [geom.get("coordinates", [])]
        )
        rings_enu: list[list[tuple[float, float]]] = []
        for polygon in polygons:
            if not polygon:
                continue
            ring = polygon[0]
            converted = []
            for point in ring:
                x, y, _ = control.geodetic_to_enu(float(point[0]), float(point[1]))
                converted.append((x, y))
            # Drop the closing duplicate; payloads store open rings.
            if len(converted) > 1 and math.dist(converted[0], converted[-1]) < 1e-6:
                converted.pop()
            if len(converted) >= 3:
                rings_enu.append(converted)
        if not rings_enu:
            stats["skipped_no_geometry"] += 1
            continue

        outer = max(rings_enu, key=lambda r: abs(ring_area(r)))
        if ring_area(outer) < 0:
            outer = outer[::-1]

        bin_id = str(record.get("bin") or "").strip() or f"doitt_{record.get('doitt_id')}"
        bbl = str(record.get("mappluto_bbl") or record.get("base_bbl") or "").split(".")[0]
        lot = pluto.get(bbl)
        if lot:
            stats["pluto_joined"] += 1

        ground_ft = _as_float(record.get("ground_elevation"))
        roof_ft = _as_float(record.get("height_roof"))
        floors = _as_float((lot or {}).get("numfloors"))

        base_z = (ground_ft or 0.0) * FT

        source_basis = ["official_dataset"]
        control_refs = ["DCTL-001", "DCTL-002", "DCTL-061"]
        open_questions: list[str] = []

        if roof_ft and roof_ft > 0:
            height_m = roof_ft * FT
            confidence = "A"
            stats["height_from_dataset"] += 1
            height_basis = "height_roof"
        elif floors and floors > 0:
            height_m = floors * storey
            confidence = "C"
            source_basis = ["official_dataset", "procedural"]
            control_refs.append("DCTL-062")
            open_questions.append("DOQ-002")
            stats["height_from_floors"] += 1
            height_basis = "pluto_floors"
        else:
            height_m = fallback_height
            confidence = "D"
            source_basis = ["inferred"]
            control_refs.append("DCTL-063")
            open_questions.append("DOQ-002")
            stats["height_fallback"] += 1
            height_basis = "fallback"

        if ground_ft is None:
            # No registered ground elevation: the base sits on the frame's zero plane.
            open_questions.append("DOQ-003")

        centroid = record.get("_centroid")
        if centroid:
            cx, cy, _ = control.geodetic_to_enu(float(centroid[0]), float(centroid[1]))
        else:
            cx = sum(p[0] for p in outer) / len(outer)
            cy = sum(p[1] for p in outer) / len(outer)

        attributes: dict[str, object] = {
            "bin": bin_id,
            "bbl": bbl or None,
            "height_roof_m": round(height_m, 2),
            "ground_elevation_m": round(base_z, 2),
            "roof_elevation_m": round(base_z + height_m, 2),
            "footprint_area_m2": round(abs(ring_area(outer)), 1),
            "construction_year": _as_int(record.get("construction_year")),
            "geom_source": record.get("geom_source"),
            "height_basis": height_basis,
        }
        if lot:
            attributes.update(
                {
                    "address": lot.get("address"),
                    "owner": lot.get("ownername"),
                    "building_class": lot.get("bldgclass"),
                    "land_use": lot.get("landuse"),
                    "num_floors": _as_float(lot.get("numfloors")),
                    "residential_units": _as_int(lot.get("unitsres")),
                    "total_units": _as_int(lot.get("unitstotal")),
                    "year_built": _as_int(lot.get("yearbuilt")),
                    "lot_area_m2": round((_as_float(lot.get("lotarea")) or 0) * 0.092903, 1),
                    "zoning": lot.get("zonedist1"),
                }
            )

        buildings.append(
            {
                "local_id": f"bldg_{bin_id}",
                "ring": outer,
                "centroid": (cx, cy),
                "base_z": base_z,
                "height_m": height_m,
                "confidence": confidence,
                "source_basis": source_basis,
                "control_refs": control_refs,
                "open_questions": open_questions,
                "attributes": {k: v for k, v in attributes.items() if v is not None},
                "display_name": (lot or {}).get("address") or f"Building {bin_id}",
            }
        )

    return buildings, stats


def _as_float(value: object) -> float | None:
    try:
        result = float(str(value))
        return result if math.isfinite(result) else None
    except (TypeError, ValueError):
        return None


def _as_int(value: object) -> int | None:
    result = _as_float(value)
    if result is None or result == 0:
        return None
    return int(result)


# --------------------------------------------------------------------- tiles


def assign_tiles(buildings: list[dict], index: dict) -> dict[str, list[dict]]:
    size = index["scheme"]["tile_size_m"]
    ox, oy = index["scheme"]["origin_xy_m"]
    grouped: dict[str, list[dict]] = {}
    for building in buildings:
        cx, cy = building["centroid"]
        col = int(math.floor((cx - ox) / size))
        row = int(math.floor((cy - oy) / size))
        grouped.setdefault(f"t_{col}_{row}", []).append(building)
    return grouped


def build_tile_payloads(
    control: DistrictControl, buildings: list[dict], index: dict
) -> tuple[dict, dict]:
    grouped = assign_tiles(buildings, index)
    tiles_dir = OUT / "tiles"
    if tiles_dir.exists():
        for stale in tiles_dir.glob("*.json"):
            stale.unlink()

    written = 0
    total_bytes = 0
    confidence_hist: dict[str, int] = {}
    for building in buildings:
        confidence_hist[building["confidence"]] = confidence_hist.get(building["confidence"], 0) + 1

    for tile in index["tiles"]:
        members = grouped.get(tile["tile_id"], [])
        tile["asset_count"] = len(members)
        tile["content"] = []
        if not members:
            continue

        levels = ZONE_LEVELS.get(tile["zone"], [])
        origin = tile["bbox"]["min"]

        for level in levels:
            tolerance = SIMPLIFY_M[level]
            entries = []
            for building in members:
                ring = building["ring"]
                if level == 2:
                    shaped = oriented_box(ring)
                else:
                    shaped = simplify(ring, tolerance)
                    if len(shaped) < 3:
                        shaped = oriented_box(ring)
                # Tile-local coordinates keep magnitudes small and the JSON short.
                local = [
                    [round(p[0] - origin[0], 2), round(p[1] - origin[1], 2)] for p in shaped
                ]
                entry: dict[str, object] = {
                    "id": building["local_id"],
                    "ring": local,
                    "base": round(building["base_z"], 2),
                    "h": round(building["height_m"], 2),
                    "c": building["confidence"],
                }
                if level <= 1:
                    entry["name"] = building["display_name"]
                    entry["attrs"] = building["attributes"]
                    entry["basis"] = building["source_basis"]
                    entry["ctl"] = building["control_refs"]
                    if building["open_questions"]:
                        entry["oq"] = building["open_questions"]
                entries.append(entry)

            payload = {
                "contract_version": CONTRACT_VERSION,
                "module_id": MODULE_ID,
                "frame_id": FRAME_ID,
                "tile_id": tile["tile_id"],
                "level": level,
                "representation": "extruded_footprint" if level <= 1 else "block",
                "origin_m": [origin[0], origin[1], 0.0],
                "carries_metadata": level <= 1,
                "buildings": entries,
            }
            name = f"{tile['tile_id']}.lod{level}.json"
            size = write_json(tiles_dir / name, payload, compact=True)
            written += 1
            total_bytes += size
            tile["content"].append(
                {
                    "level": level,
                    "url": name,
                    "format": "json",
                    "byte_size": size,
                    "max_geometric_error_m": LEVEL_ERROR[level],
                }
            )

    # Tiles the Manhattan Bridge overhangs must declare it, so the streamer keeps the bridge
    # resident whenever a visitor can see it. Bridge geometry itself stays in the bridge module.
    bridge_tiles = mark_bridge_tiles(control, index)

    report = {
        "tile_payloads_written": written,
        "tile_payload_bytes": total_bytes,
        "tiles_with_content": sum(1 for t in index["tiles"] if t["content"]),
        "confidence_histogram": confidence_hist,
        "tiles_declaring_bridge": bridge_tiles,
    }
    return index, report


LEVEL_ERROR = {0: 0.2, 1: 2.0, 2: 8.0, 3: 25.0}


def mark_bridge_tiles(control: DistrictControl, index: dict) -> int:
    """
    Flag tiles that the Manhattan Bridge crosses.

    The corridor is taken from the provisional bridge placement (DOQ-001): a line from the Brooklyn
    anchorage north-west across the river. It is deliberately generous, because the cost of an
    over-broad flag is a proxy loaded slightly early, and the cost of a missing one is an invisible
    bridge.
    """
    a = control.geodetic_to_enu(-73.99000, 40.70050)[:2]
    b = control.geodetic_to_enu(-73.98680, 40.70900)[:2]
    corridor = 120.0

    from district_control import distance_point_to_segment

    count = 0
    for tile in index["tiles"]:
        min_xy = tile["bbox"]["min"]
        max_xy = tile["bbox"]["max"]
        centre = ((min_xy[0] + max_xy[0]) / 2.0, (min_xy[1] + max_xy[1]) / 2.0)
        if distance_point_to_segment(centre, a, b) <= corridor:
            tile["foreign_assets"] = [BRIDGE_PROXY_URN]
            count += 1
    return count


# ---------------------------------------------------------------- walk graph


def build_walk_network(control: DistrictControl) -> dict:
    """
    Pedestrian graph from OSM ways, in scene coordinates.

    Used for two things: routing a tour leg that arrives without a path, and snapping the walking
    camera to a plausible ground line. Ways that a pedestrian may not use are dropped.
    """
    ways = load_json(DATA / "streets" / "osm-ways.raw.json")
    ring = control.boundary_ring

    walkable = {
        "footway", "pedestrian", "path", "steps", "living_street", "residential",
        "service", "unclassified", "tertiary", "secondary", "primary", "track", "cycleway",
    }

    nodes: dict[tuple[int, int], int] = {}
    positions: list[list[float]] = []
    edges: list[dict] = []

    def node_id(x: float, y: float) -> int:
        key = (int(round(x * 10)), int(round(y * 10)))
        existing = nodes.get(key)
        if existing is not None:
            return existing
        index = len(positions)
        nodes[key] = index
        positions.append([round(x, 2), round(y, 2)])
        return index

    for way in ways:
        tags = way.get("tags") or {}
        highway = tags.get("highway")
        if highway not in walkable:
            continue
        if tags.get("foot") == "no" or tags.get("access") in {"private", "no"}:
            continue
        geometry = way.get("geometry") or []
        if len(geometry) < 2:
            continue

        chain: list[int] = []
        for point in geometry:
            lon, lat = float(point["lon"]), float(point["lat"])
            if not point_in_ring((lon, lat), ring):
                chain.append(-1)
                continue
            x, y, _ = control.geodetic_to_enu(lon, lat)
            chain.append(node_id(x, y))

        kind = "footway" if highway in {"footway", "pedestrian", "path", "steps", "cycleway"} else "street"
        for i in range(len(chain) - 1):
            a, b = chain[i], chain[i + 1]
            if a < 0 or b < 0 or a == b:
                continue
            ax, ay = positions[a]
            bx, by = positions[b]
            edges.append(
                {
                    "a": a,
                    "b": b,
                    "len": round(math.hypot(bx - ax, by - ay), 2),
                    "kind": kind,
                    "name": tags.get("name"),
                    "stairs": highway == "steps",
                }
            )

    return {
        "contract_version": CONTRACT_VERSION,
        "module_id": MODULE_ID,
        "frame_id": FRAME_ID,
        "source_refs": ["DSRC-007"],
        "attribution": "© OpenStreetMap contributors, ODbL",
        "nodes": positions,
        "edges": edges,
        "provenance": provenance(control),
    }


# ---------------------------------------------------------------- landmarks


LANDMARK_KEEP = {
    "attraction", "museum", "artwork", "viewpoint", "gallery", "theatre",
    "arts_centre", "marketplace", "pier", "park", "memorial", "monument", "ferry_terminal",
}


def build_landmarks(control: DistrictControl) -> list[dict]:
    raw = load_json(DATA / "streets" / "osm-landmarks.raw.json")
    ring = control.boundary_ring
    out: list[dict] = []
    seen: set[str] = set()

    for feature in raw:
        tags = feature.get("tags") or {}
        kinds = {
            tags.get("tourism"), tags.get("historic"), tags.get("leisure"),
            tags.get("amenity"), tags.get("man_made"),
        }
        if not (kinds & LANDMARK_KEEP):
            continue
        lon, lat = float(feature["lon"]), float(feature["lat"])
        if not point_in_ring((lon, lat), ring):
            continue

        slug = _slug(feature["name"])
        local_id = f"landmark_{slug}"
        if local_id in seen:
            continue
        seen.add(local_id)

        x, y, _ = control.geodetic_to_enu(lon, lat)
        category = "park" if tags.get("leisure") == "park" else "landmark"
        out.append(
            {
                "asset_id": f"urn:d3d:{MODULE_ID}:{local_id}",
                "kind": "single",
                "tags": ["landmark", "photo_spot"],
                "metadata": {
                    "asset_id": f"urn:d3d:{MODULE_ID}:{local_id}",
                    "module_id": MODULE_ID,
                    "local_id": local_id,
                    "display_name": feature["name"],
                    "category": category,
                    "taxonomy": {"system": "landmarks", "path": ["landmarks", category]},
                    "source_basis": ["official_dataset"],
                    "source_refs": ["DSRC-007"],
                    "confidence": "B",
                    "basis_confidence": "B",
                    "control_refs": ["DCTL-001", "DCTL-002"],
                    "review_status": "unreviewed",
                    "last_modified_by": AGENT_ID,
                    "units": "meters",
                    "anchor": {"frame": FRAME_ID, "xyz": [round(x, 2), round(y, 2), 0.0]},
                    "attributes": {
                        "osm_type": feature["osm_type"],
                        "osm_id": feature["osm_id"],
                        "kind": next(iter(kinds & LANDMARK_KEEP)),
                    },
                    "notes": (
                        "Position from OpenStreetMap, graded B: consistent community mapping, not a "
                        "survey. Used as a tour target and a map label, never as control geometry."
                    ),
                },
            }
        )
    return out


def _slug(name: str) -> str:
    out = []
    for ch in name.lower():
        if ch.isalnum():
            out.append(ch)
        elif out and out[-1] != "_":
            out.append("_")
    return "".join(out).strip("_")[:48] or "unnamed"


# ------------------------------------------------------------------ documents


def build_georeference(control: DistrictControl) -> dict:
    lon, lat, height = control.anchor
    return {
        "contract_version": CONTRACT_VERSION,
        "frame_id": FRAME_ID,
        "kind": "enu",
        "units": "meters",
        "axes": {"x": "east", "y": "north", "z": "up", "handedness": "right"},
        "anchor": {
            "lon": lon,
            "lat": lat,
            "height_m": height,
            "vertical_datum": "NAVD88",
            "horizontal_crs": "EPSG:4326",
            "rationale": (
                "Frozen by convention (DCTL-001..003). A round-numbered point near the district "
                "centroid, chosen so DUMBO coordinates stay small and float32 precision stays "
                "comfortable in the browser. Not a survey monument."
            ),
        },
        "vertical_datum_offsets_m": {
            key: round(value, 4) for key, value in control.vertical_datum_offsets().items()
        },
        "valid_radius_m": control.value_m("DCTL-004"),
        "max_planar_error_m": control.value_m("DCTL-005"),
        "ellipsoid": {
            "name": "WGS84",
            "semi_major_axis_m": 6378137.0,
            "inverse_flattening": 298.257223563,
        },
        "render_convention": {
            "gltf_up_axis": "Y",
            "scene_to_render": "(x, y, z) -> (x, z, -y)",
        },
        "source_refs": ["DSRC-005", "DSRC-006"],
        "confidence": "A",
        "notes": (
            "MHW offset DCTL-010 reconciles this frame with manhattan-bridge-3d, whose geometry is "
            "authored against mean high water. Consumers placing bridge geometry MUST apply it."
        ),
        "provenance": provenance(control),
    }


def build_ladder(control: DistrictControl) -> dict:
    return {
        "contract_version": CONTRACT_VERSION,
        "module_id": MODULE_ID,
        "ladder_id": LADDER_ID,
        "levels": [
            {
                "level": 0,
                "name": "hero",
                "intent": "traverse",
                "max_geometric_error_m": LEVEL_ERROR[0],
                "representation": "extruded_footprint",
                "payload_format": "json",
                "triangle_budget": 40000,
                "selectable": True,
                "carries_metadata": True,
                "streamed": True,
                "typical_distance_m": {"min": 0, "max": 120},
                "notes": (
                    "Full-resolution footprint ring extruded to the dataset roof height. Error is "
                    "dominated by the flat-roof assumption, not by the plan geometry."
                ),
            },
            {
                "level": 1,
                "name": "walkable",
                "intent": "traverse",
                "max_geometric_error_m": LEVEL_ERROR[1],
                "representation": "extruded_footprint",
                "payload_format": "json",
                "triangle_budget": 16000,
                "selectable": True,
                "carries_metadata": True,
                "streamed": True,
                "typical_distance_m": {"min": 80, "max": 400},
                "notes": "Ring simplified with a 1 m Douglas-Peucker tolerance.",
            },
            {
                "level": 2,
                "name": "context",
                "intent": "context",
                "max_geometric_error_m": LEVEL_ERROR[2],
                "representation": "block",
                "payload_format": "json",
                "triangle_budget": 4000,
                "selectable": False,
                "carries_metadata": False,
                "streamed": True,
                "typical_distance_m": {"min": 300, "max": 1200},
                "notes": "Minimum-area oriented box preserving footprint area and orientation.",
            },
            {
                "level": 3,
                "name": "silhouette",
                "intent": "silhouette",
                "max_geometric_error_m": LEVEL_ERROR[3],
                "representation": "map_polygon",
                "payload_format": "geojson",
                "triangle_budget": None,
                "selectable": False,
                "carries_metadata": False,
                "streamed": False,
                "typical_distance_m": {"min": 1000},
                "notes": "Map view only. Not rendered in 3D.",
            },
        ],
        "selection": {
            "policy": "screen_space_error",
            "default_sse_budget_px": 12,
            "mode_sse_budget_px": {"inspect": 2, "walk": 12, "map": 48, "tour": 8},
            "hysteresis": 0.15,
        },
        "notes": (
            "One axis, four levels. A viewer mode changes only the screen-space error budget, never "
            "the ladder. See digital-3d-shared-contracts/VIEWER-MODES.md section 3.2."
        ),
    }


def build_source_register(control: DistrictControl) -> dict:
    return {
        "contract_version": CONTRACT_VERSION,
        "module_id": MODULE_ID,
        "grades": {
            "A": "Derived from an authoritative published dataset, an official dimension, or an archival drawing.",
            "B": "Derived from consistent imagery or community mapping combined with known control geometry.",
            "C": "Derived from a reconstructed attribute, an aligned mesh, or photogrammetry.",
            "D": "Inferred, decorative, or placeholder. Never citable as a dimension.",
        },
        "weakest_link_rule": True,
        "tier_rule": "No Tier C source may override Tier A geometry.",
        "sources": [
            {
                "source_id": "DSRC-001",
                "title": "NYC Building Footprints",
                "tier": "A",
                "publisher": "NYC Office of Technology and Innovation, via NYC Open Data",
                "url": "https://data.cityofnewyork.us/resource/5zhs-2jue.json",
                "accessed": "2026-08-09",
                "license": "NYC Open Data Terms of Use",
                "attribution_required": True,
                "attribution_text": "Building footprints: NYC Open Data (OTI)",
                "native_crs": "EPSG:2263",
                "vertical_datum": "NAVD88",
                "units": "feet",
                "positional_accuracy_m": 0.61,
                "grants_confidence": "A",
                "verified": True,
                "notes": (
                    "Plan geometry and roof heights. Positional accuracy is the publisher's stated "
                    "+/- 2 ft for photogrammetrically captured features (ASPRS Class 1). "
                    "height_roof is a height above ground_elevation, not an elevation."
                ),
            },
            {
                "source_id": "DSRC-002",
                "title": "NYC MapPLUTO / PLUTO tax lot attributes",
                "tier": "A",
                "publisher": "NYC Department of City Planning, via NYC Open Data",
                "url": "https://data.cityofnewyork.us/resource/64uk-42ks.json",
                "accessed": "2026-08-09",
                "license": "NYC Open Data Terms of Use",
                "attribution_required": True,
                "attribution_text": "Lot attributes: NYC Department of City Planning (PLUTO)",
                "native_crs": "EPSG:2263",
                "units": "feet",
                "grants_confidence": "A",
                "verified": True,
                "notes": (
                    "Attributes only: address, owner, class, land use, floors, year built. Never a "
                    "geometry source. Floor counts fall back to grade C via DCTL-062."
                ),
            },
            {
                "source_id": "DSRC-003",
                "title": "NYC building footprint published metadata",
                "tier": "A",
                "publisher": "City of New York, nyc-geo-metadata",
                "url": "https://github.com/CityOfNewYork/nyc-geo-metadata/blob/main/Metadata/Metadata_BuildingFootprints.md",
                "accessed": "2026-08-09",
                "license": "NYC Open Data Terms of Use",
                "grants_confidence": "A",
                "verified": True,
                "notes": (
                    "Authoritative attribute definitions. The source of the statement that "
                    "HEIGHTROOF is measured above GROUNDELEV and that GROUNDELEV is NAVD88."
                ),
            },
            {
                "source_id": "DSRC-004",
                "title": "NYC Neighborhood Tabulation Areas 2020",
                "tier": "A",
                "publisher": "NYC Department of City Planning, via NYC Open Data",
                "url": "https://data.cityofnewyork.us/resource/9nt8-h7nd.json",
                "accessed": "2026-08-09",
                "license": "NYC Open Data Terms of Use",
                "grants_confidence": "A",
                "verified": True,
                "notes": (
                    "Context only. BK0202 is 'Downtown Brooklyn-DUMBO-Boerum Hill' and is much "
                    "larger than this project's subject, so it is NOT used as the boundary."
                ),
            },
            {
                "source_id": "DSRC-005",
                "title": "NOAA CO-OPS tidal datums, station 8518750 (The Battery, NY)",
                "tier": "A",
                "publisher": "NOAA National Ocean Service",
                "url": "https://api.tidesandcurrents.noaa.gov/mdapi/prod/webapi/stations/8518750/datums.json",
                "accessed": "2026-08-09",
                "license": "Public domain (US Government work)",
                "vertical_datum": "MHW",
                "units": "meters",
                "grants_confidence": "A",
                "verified": True,
                "notes": (
                    "Epoch 1983-2001, relative to station datum: MHW 2.44, NAVD88 1.85. "
                    "Yields DCTL-010, MHW = NAVD88 + 0.59 m."
                ),
            },
            {
                "source_id": "DSRC-006",
                "title": "NYSAPLS tide datum reference for station 8518750",
                "tier": "B",
                "publisher": "New York State Association of Professional Land Surveyors",
                "url": "https://cdn.ymaws.com/www.nysapls.org/resource/resmgr/2019_conference/handouts/horbas,_peter_tide_datums_01.pdf",
                "accessed": "2026-08-09",
                "license": "Conference handout, cited not redistributed",
                "grants_confidence": "B",
                "verified": True,
                "notes": "Independent corroboration: MHW 0.596 m above NAVD88. Agrees with DSRC-005.",
            },
            {
                "source_id": "DSRC-007",
                "title": "OpenStreetMap street, footway and landmark data",
                "tier": "B",
                "publisher": "OpenStreetMap contributors, via Overpass API",
                "url": "https://overpass-api.de/api/interpreter",
                "accessed": "2026-08-09",
                "license": "ODbL-1.0",
                "attribution_required": True,
                "attribution_text": "© OpenStreetMap contributors",
                "native_crs": "EPSG:4326",
                "grants_confidence": "B",
                "verified": True,
                "notes": (
                    "Walk network and landmark names and positions. ODbL requires attribution "
                    "wherever this is rendered; the viewer displays it unconditionally."
                ),
            },
            {
                "source_id": "DSRC-008",
                "title": "NYC digital elevation model and LiDAR",
                "tier": "A",
                "publisher": "NYC Office of Technology and Innovation",
                "license": "NYC Open Data Terms of Use",
                "grants_confidence": "A",
                "verified": False,
                "notes": (
                    "NOT YET INGESTED. Registered so DOQ-003, the missing terrain surface, names a "
                    "real remedy rather than a wish. Phase 2."
                ),
            },
            {
                "source_id": "DSRC-009",
                "title": "NYC Forestry Management System street trees",
                "tier": "A",
                "publisher": "NYC Parks, via NYC Open Data",
                "url": "https://data.cityofnewyork.us/resource/hn5i-inap.json",
                "accessed": "2026-08-09",
                "license": "NYC Open Data Terms of Use",
                "attribution_required": True,
                "attribution_text": "Street trees: NYC Parks Forestry Management System",
                "native_crs": "EPSG:4326",
                "units": "inches",
                "grants_confidence": "A",
                "verified": True,
                "notes": (
                    "Position, species and trunk diameter (dbh, inches) per street tree. Positions "
                    "and species are grade A; the rendered canopy is a procedural form for the "
                    "genus and is therefore graded C."
                ),
            },
        ],
        "provenance": provenance(control),
    }


def build_manifest(control: DistrictControl, bridge_manifest_url: str) -> dict:
    lon, lat, _ = control.anchor
    west, south, east, north = control.bbox
    return {
        "contract_version": CONTRACT_VERSION,
        "module_id": MODULE_ID,
        "title": "DUMBO District Digital Twin",
        "subtitle": "Walkable neighbourhood model, source-governed, streamed",
        "module_version": "1.0.0",
        "owner": {"team": "DUMBO District", "repository": "dumbo-district-3d"},
        "authoritative_for": [
            "DUMBO neighbourhood geospatial model",
            "building footprints and massing within the district boundary",
            "street and footway network",
            "waterfront and park extents",
            "district terrain (planned)",
            "property metadata",
            "tile streaming and LOD management for the district",
            "walking experience and district navigation",
        ],
        "depends_on": [
            {
                "module_id": BRIDGE_MODULE,
                "manifest_url": bridge_manifest_url,
                "required": False,
            }
        ],
        "georeference": {"url": "./georeference.json"},
        "lod_ladder": {"url": "./lod.json"},
        "asset_registry_url": "./asset-registry.json",
        "tile_index_url": "./tile-index.json",
        "source_register_url": "./source-register.json",
        "modes": ["walk", "map", "tour"],
        "handoff": {"supported": False},
        "attribution": [
            "Building footprints and lot data: NYC Open Data (OTI, DCP)",
            "Street trees: NYC Parks Forestry Management System",
            "© OpenStreetMap contributors, ODbL",
            "Tidal datums: NOAA CO-OPS station 8518750",
        ],
        "not_implemented_yet": [
            "terrain surface from NYC DEM and LiDAR (DOQ-003); ground is currently flat per building base",
            "district photogrammetry (deliberately out of scope for Phase 1)",
            "interior spaces",
            "roof form modelling; all roofs are flat at the dataset roof height",
            "ratified Manhattan Bridge placement (DOQ-001); the current one is provisional",
        ],
        "provenance": provenance(control),
    }


def build_asset_registry(control: DistrictControl, landmarks: list[dict]) -> dict:
    west, south, east, north = control.bbox
    assets: list[dict] = [
        {
            "asset_id": f"urn:d3d:{MODULE_ID}:district_tileset",
            "kind": "tileset",
            "tile_index_url": "./tile-index.json",
            "bbox_geodetic": [west, south, east, north],
            "tags": ["district", "buildings"],
        },
        {
            "asset_id": f"urn:d3d:{MODULE_ID}:manhattan_bridge_reference",
            "kind": "proxy",
            "represents": BRIDGE_PROXY_URN,
            "tags": ["foreign", "bridge"],
            "metadata": {
                "asset_id": f"urn:d3d:{MODULE_ID}:manhattan_bridge_reference",
                "module_id": MODULE_ID,
                "local_id": "manhattan_bridge_reference",
                "display_name": "Manhattan Bridge (owned by manhattan-bridge-3d)",
                "category": "reference",
                "source_basis": ["inferred"],
                "confidence": "D",
                "control_refs": [],
                "open_questions": ["DOQ-001"],
                "review_status": "unreviewed",
                "last_modified_by": AGENT_ID,
                "notes": (
                    "This district owns NO bridge geometry. This entry exists only so a URN "
                    "reference resolves and so the viewer can show a labelled placeholder when the "
                    "bridge module is unavailable. Replace with the bridge team's published proxy."
                ),
            },
        },
    ]
    assets.extend(landmarks)

    return {
        "contract_version": CONTRACT_VERSION,
        "module_id": MODULE_ID,
        "frame_id": FRAME_ID,
        "ladder_id": LADDER_ID,
        "base_url": "./",
        "assets": assets,
        "provenance": provenance(control),
    }


# ---------------------------------------------------------------------- main


def build_ground_grid(control: DistrictControl, buildings: list[dict], index: dict) -> dict:
    """
    Interpolate a ground height surface from building base elevations.

    `ground_elevation` on each footprint is, per DSRC-003, the lowest elevation at that building's
    ground level in NAVD88. Those are grade A point samples of the real terrain. This blends them
    with inverse-distance weighting into a coarse grid so that a walking camera, and anything else
    that needs to know where the pavement is, follows DUMBO's actual rise from the waterfront rather
    than floating twelve metres under it.

    The samples are grade A; the surface between them is not. Anything derived from this grid is
    graded C and carries DOQ-003, which is retired when a real DEM is ingested (DSRC-008).
    """
    cell = control.value_m("DCTL-070")
    radius = control.value_m("DCTL-071")
    neighbours = int(control.value("DCTL-072"))
    power = control.value("DCTL-073")

    scheme = index["scheme"]
    ox, oy = scheme["origin_xy_m"]
    span_x = scheme["grid_size"][0] * scheme["tile_size_m"]
    span_y = scheme["grid_size"][1] * scheme["tile_size_m"]

    cols = int(math.ceil(span_x / cell)) + 1
    rows = int(math.ceil(span_y / cell)) + 1

    samples = [(b["centroid"][0], b["centroid"][1], b["base_z"]) for b in buildings]
    if not samples:
        raise SystemExit("no building samples; cannot interpolate a ground surface")

    radius2 = radius * radius
    heights: list[list[float]] = []
    for row in range(rows):
        y = oy + row * cell
        line: list[float] = []
        for col in range(cols):
            x = ox + col * cell
            near: list[tuple[float, float]] = []
            for sx, sy, sz in samples:
                d2 = (sx - x) ** 2 + (sy - y) ** 2
                if d2 <= radius2:
                    near.append((d2, sz))
            if not near:
                # Outside the sampled area, usually over water. Fall back to the single nearest
                # sample so the surface stays continuous instead of collapsing to zero.
                d2, sz = min(((sx - x) ** 2 + (sy - y) ** 2, sz) for sx, sy, sz in samples)
                line.append(round(sz, 2))
                continue
            near.sort(key=lambda item: item[0])
            chosen = near[:neighbours]
            if chosen[0][0] < 1e-6:
                line.append(round(chosen[0][1], 2))
                continue
            weight_sum = 0.0
            value_sum = 0.0
            for d2, sz in chosen:
                w = 1.0 / (d2 ** (power / 2.0))
                weight_sum += w
                value_sum += w * sz
            line.append(round(value_sum / weight_sum, 2))
        heights.append(line)

    flat = [v for line in heights for v in line]
    return {
        "contract_version": CONTRACT_VERSION,
        "module_id": MODULE_ID,
        "frame_id": FRAME_ID,
        "origin_xy_m": [ox, oy],
        "cell_m": cell,
        "cols": cols,
        "rows": rows,
        "vertical_datum": "NAVD88",
        "confidence": "C",
        "source_refs": ["DSRC-001", "DSRC-003"],
        "control_refs": ["DCTL-070", "DCTL-071", "DCTL-072", "DCTL-073"],
        "open_questions": ["DOQ-003"],
        "notes": (
            "Inverse-distance interpolation of building base elevations. Grade C: the samples are "
            "authoritative, the surface between them is inferred. Replace with NYC DEM (DSRC-008)."
        ),
        "min_m": round(min(flat), 2),
        "max_m": round(max(flat), 2),
        "heights": heights,
        "provenance": provenance(control),
    }


def _canonical_frame_path() -> Path:
    return REPO_ROOT.parent / "digital-3d-shared-contracts" / "frames" / f"{FRAME_ID}.json"


def _canonical_frame() -> dict | None:
    """
    The canonical shared frame, if the contracts repository is checked out alongside.

    Returns None rather than failing when it is absent, so this repository still builds standalone.
    """
    path = _canonical_frame_path()
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def check_frame_matches_canonical(generated: dict) -> list[str]:
    """
    Verify the frame this build generated agrees with the canonical shared one.

    The frame anchor is frozen across the whole stack: every asset coordinate in every consuming
    module is expressed relative to it. If this repository's control document drifts away from the
    published frame, the bridge and the district silently stop agreeing about where things are. This
    turns that into a build error.
    """
    canonical = _canonical_frame()
    if canonical is None:
        return ["canonical frame not found; skipped cross-repository frame check"]

    problems: list[str] = []

    for key in ("lon", "lat", "height_m", "vertical_datum"):
        ours = generated["anchor"].get(key)
        theirs = canonical["anchor"].get(key)
        if isinstance(ours, float) or isinstance(theirs, float):
            if abs(float(ours) - float(theirs)) > 1e-9:
                problems.append(f"anchor.{key}: this repo {ours} != canonical {theirs}")
        elif ours != theirs:
            problems.append(f"anchor.{key}: this repo {ours!r} != canonical {theirs!r}")

    for datum, value in canonical.get("vertical_datum_offsets_m", {}).items():
        ours = generated.get("vertical_datum_offsets_m", {}).get(datum)
        if ours is None:
            problems.append(f"vertical_datum_offsets_m.{datum}: missing here, canonical has {value}")
        elif abs(float(ours) - float(value)) > 1e-6:
            problems.append(
                f"vertical_datum_offsets_m.{datum}: this repo {ours} != canonical {value}"
            )

    if generated["axes"] != canonical["axes"]:
        problems.append("axes differ from canonical frame")
    if generated["render_convention"] != canonical["render_convention"]:
        problems.append("render_convention differs from canonical frame")

    return problems


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--bridge-manifest",
        default="../modules/manhattan-bridge/bridge-manifest.json",
        help="URL the district uses to look for the Manhattan Bridge module manifest.",
    )
    args = parser.parse_args()

    control = DistrictControl()
    print(f"district control : {control.path.name} @ {control.sha256[:12]}")

    print("[buildings]")
    buildings, stats = build_buildings(control)
    print(f"    {len(buildings)} buildings")
    print(f"    heights: {stats}")

    print("[tiles]")
    index = load_json(DATA / "tiles" / "tile-index.json")
    index, tile_report = build_tile_payloads(control, buildings, index)
    print(f"    {tile_report['tile_payloads_written']} payloads, "
          f"{tile_report['tile_payload_bytes'] / 1024:.0f} KB total")
    print(f"    confidence: {tile_report['confidence_histogram']}")
    print(f"    tiles declaring the bridge: {tile_report['tiles_declaring_bridge']}")
    index["provenance"] = provenance(control)
    write_json(OUT / "tile-index.json", index)

    print("[walk network]")
    walk = build_walk_network(control)
    write_json(OUT / "walk-network.json", walk, compact=True)
    print(f"    {len(walk['nodes'])} nodes, {len(walk['edges'])} edges")

    print("[ground surface]")
    ground = build_ground_grid(control, buildings, index)
    write_json(OUT / "ground-grid.json", ground, compact=True)
    print(f"    {ground['cols']} x {ground['rows']} cells at {ground['cell_m']:g} m, "
          f"{ground['min_m']:.1f} to {ground['max_m']:.1f} m NAVD88")

    print("[landmarks]")
    landmarks = build_landmarks(control)
    print(f"    {len(landmarks)} landmark assets")

    print("[documents]")
    georeference = build_georeference(control)
    frame_problems = check_frame_matches_canonical(georeference)
    if frame_problems:
        fatal = [p for p in frame_problems if "skipped" not in p]
        for problem in frame_problems:
            print(f"    {'FAIL' if 'skipped' not in problem else 'note'}: {problem}")
        if fatal:
            print(
                "\nThe frame generated from DUMBO-GEOSPATIAL-CONTROL.md disagrees with the "
                "canonical shared frame. The anchor is frozen across the whole stack; fix one or "
                "the other before shipping."
            )
            return 1
    else:
        print("    frame matches the canonical shared frame")

    write_json(OUT / "georeference.json", georeference)
    write_json(OUT / "lod.json", build_ladder(control))
    write_json(OUT / "source-register.json", build_source_register(control))
    write_json(OUT / "asset-registry.json", build_asset_registry(control, landmarks))
    write_json(OUT / "district-manifest.json", build_manifest(control, args.bridge_manifest))

    # Serve the canonical shared frame alongside the district's own copy, so a consuming module can
    # reference the frame without depending on this module's documents. Copied byte-for-byte rather
    # than re-serialised, so a consumer can verify it against the contracts repository by hash.
    canonical_path = _canonical_frame_path()
    if canonical_path.is_file():
        served = REPO_ROOT / "viewer" / "public" / "frames" / f"{FRAME_ID}.json"
        served.parent.mkdir(parents=True, exist_ok=True)
        served.write_bytes(canonical_path.read_bytes())
        print(f"    copied canonical frame verbatim -> {served.relative_to(REPO_ROOT)}")

    # The boundary and hero zone are served to the viewer as well as kept in data/, because the map
    # view draws them and the walk view outlines them.
    for name in ("dumbo-district.geojson", "hero-zone.geojson"):
        source = DATA / "boundaries" / name
        if source.is_file():
            write_json(OUT / name, json.loads(source.read_text(encoding="utf-8")))

    report = {
        "generated_at": now(),
        "generated_by": AGENT_ID,
        "control_document": {"path": control.path.name, "sha256": control.sha256},
        "buildings": len(buildings),
        "height_sources": stats,
        "tiles": {
            "total": len(index["tiles"]),
            "with_content": tile_report["tiles_with_content"],
            "by_zone": _zone_counts(index),
        },
        "confidence_histogram": tile_report["confidence_histogram"],
        "payloads": {
            "count": tile_report["tile_payloads_written"],
            "bytes": tile_report["tile_payload_bytes"],
        },
        "walk_network": {"nodes": len(walk["nodes"]), "edges": len(walk["edges"])},
        "ground_grid": {
            "cols": ground["cols"],
            "rows": ground["rows"],
            "cell_m": ground["cell_m"],
            "min_m": ground["min_m"],
            "max_m": ground["max_m"],
            "confidence": ground["confidence"],
        },
        "landmarks": len(landmarks),
        "open_questions_touched": sorted(
            {oq for b in buildings for oq in b["open_questions"]} | {"DOQ-001", "DOQ-003"}
        ),
    }
    write_json(OUT / "build-report.json", report)
    print(f"    wrote {(OUT / 'build-report.json').relative_to(REPO_ROOT)}")
    print("\ndistrict build complete")
    return 0


def _zone_counts(index: dict) -> dict[str, int]:
    counts: dict[str, int] = {}
    for tile in index["tiles"]:
        counts[tile["zone"]] = counts.get(tile["zone"], 0) + 1
    return counts


if __name__ == "__main__":
    raise SystemExit(main())
