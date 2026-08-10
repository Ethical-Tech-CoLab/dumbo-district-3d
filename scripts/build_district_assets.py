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

from district_control import (
    AGENT_ID,
    DistrictControl,
    distance_point_to_segment,
    point_in_ring,
)

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


def polygon_centroid(points: list[tuple[float, float]]) -> tuple[float, float]:
    """Area-weighted centroid of an open ring already in local ENU metres.

    Safe to compute directly here only because the coordinates are local: the same shoelace moments
    in raw lon/lat cancel catastrophically and produce centroids hundreds of metres away. Falls back
    to the vertex mean for a degenerate ring, which at least stays inside the footprint.
    """
    area2 = 0.0
    mx = my = 0.0
    n = len(points)
    for i in range(n):
        x1, y1 = points[i]
        x2, y2 = points[(i + 1) % n]
        cross = x1 * y2 - x2 * y1
        area2 += cross
        mx += (x1 + x2) * cross
        my += (y1 + y2) * cross
    if abs(area2) < 1e-9:
        return (sum(p[0] for p in points) / n, sum(p[1] for p in points) / n)
    return (mx / (3.0 * area2), my / (3.0 * area2))


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


class Dem:
    """The ingested bare-earth DEM, if there is one.

    Held as an object rather than a bare array because two very different consumers need it: the
    terrain mesh, and the handful of buildings whose own dataset does not say what elevation they
    stand at. Both must sample exactly the same surface.
    """

    def __init__(self, doc: dict, grid: list[list[float]]) -> None:
        self.cell = doc["cell_m"]
        self.ox, self.oy = doc["origin_xy_m"]
        self.cols = doc["cols"]
        self.rows = doc["rows"]
        self.grid = grid

    @classmethod
    def load(cls, control: DistrictControl) -> "Dem | None":
        """Read the DEM, insisting it describes exactly the grid we are about to build.

        A DEM that silently disagreed about spacing or origin would place the terrain a block away
        from the buildings standing on it, sloping the wrong way, with nothing in the output to say
        so. A mismatch is therefore a hard error telling the operator to re-run the ingest, never a
        quiet fallback.
        """
        path = DATA / "terrain" / "dem.raw.json"
        if not path.exists():
            return None
        raw = json.loads(path.read_text(encoding="utf-8"))

        cell = control.value_m("DCTL-070")
        ox, oy, span_x, span_y = control.tile_extent
        cols = int(math.ceil(span_x / cell)) + 1
        rows = int(math.ceil(span_y / cell)) + 1

        expected = (cell, [ox, oy], cols, rows)
        actual = (raw.get("cell_m"), raw.get("origin_xy_m"), raw.get("cols"), raw.get("rows"))
        if expected != actual:
            raise SystemExit(
                f"DEM grid {actual} does not match the ground grid {expected}. "
                "Re-run: python scripts/ingest_sources.py --dem"
            )
        if raw.get("vertical_datum") != "NAVD88":
            raise SystemExit(f"DEM vertical datum is {raw.get('vertical_datum')!r}, expected NAVD88")

        values = raw["values"]
        if len(values) != cols * rows:
            raise SystemExit(f"DEM has {len(values)} values, expected {cols * rows}")

        grid = [values[r * cols:(r + 1) * cols] for r in range(rows)]
        holes = _fill_nodata(grid, cols, rows)
        if holes:
            print(f"    DEM: filled {holes} no-data cells from neighbours")
        rounded = [[round(float(v), 2) for v in line] for line in grid]
        return cls(raw, rounded)

    def at(self, x: float, y: float) -> float | None:
        """Nearest-cell elevation, or None outside the grid."""
        col = int(round((x - self.ox) / self.cell))
        row = int(round((y - self.oy) / self.cell))
        if 0 <= col < self.cols and 0 <= row < self.rows:
            return self.grid[row][col]
        return None


def _fill_nodata(grid: list[list[float | None]], cols: int, rows: int) -> int:
    """Replace nulls with the mean of their known neighbours, repeating until the holes close."""
    holes = [(r, c) for r in range(rows) for c in range(cols) if grid[r][c] is None]
    if not holes:
        return 0
    total = len(holes)
    for _ in range(12):
        if not holes:
            break
        remaining = []
        for r, c in holes:
            known = [
                grid[r + dr][c + dc]
                for dr, dc in ((-1, 0), (1, 0), (0, -1), (0, 1))
                if 0 <= r + dr < rows and 0 <= c + dc < cols and grid[r + dr][c + dc] is not None
            ]
            if known:
                grid[r][c] = sum(known) / len(known)
            else:
                remaining.append((r, c))
        holes = remaining
    for r, c in holes:
        grid[r][c] = 0.0
    return total


def build_buildings(control: DistrictControl, dem: "Dem | None" = None) -> tuple[list[dict], dict]:
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
        "ground_from_dataset": 0,
        "ground_from_dem": 0,
        "ground_unknown": 0,
    }
    deferred_ground: list[dict] = []

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

        # The ring is already in local ENU metres, where the shoelace moments are numerically safe,
        # so the centroid is computed here rather than trusting the one carried on the record.
        cx, cy = polygon_centroid(outer)

        source_basis = ["official_dataset"]
        control_refs = ["DCTL-001", "DCTL-002", "DCTL-061"]
        open_questions: list[str] = []

        # Ground elevation. The footprint dataset encodes "unknown" as zero, and DUMBO rises to
        # 23 m, so taking that at face value planted seven buildings up to 18 m below the street
        # they stand on. The old interpolated ground could never reveal this, because it was built
        # from these same values: the error was baked into the surface used to check it. An
        # independent DEM is what makes the defect visible, and what repairs it.
        if ground_ft is not None and ground_ft > 0:
            base_z = ground_ft * FT
            ground_basis = "dataset"
            stats["ground_from_dataset"] += 1
        elif dem is not None and (sampled := dem.at(cx, cy)) is not None:
            base_z = sampled
            ground_basis = "dem"
            source_basis.append("official_dataset")
            control_refs.append("DCTL-074")
            stats["ground_from_dem"] += 1
        else:
            base_z = 0.0
            ground_basis = "unknown"
            open_questions.append("DOQ-003")
            stats["ground_unknown"] += 1
            deferred_ground.append({"bin": bin_id})

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

        attributes: dict[str, object] = {
            "bin": bin_id,
            "bbl": bbl or None,
            "height_roof_m": round(height_m, 2),
            "ground_elevation_m": round(base_z, 2),
            "ground_elevation_basis": ground_basis,
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
                "attribution_required": False,
                "attribution_text": "Tidal datums: NOAA CO-OPS station 8518750",
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
            {
                "source_id": "DSRC-011",
                "title": "NYC Building Footprints, Lower Manhattan frontage",
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
                    "Same dataset as DSRC-001, queried across the river for the skyline visible "
                    "from DUMBO. Delivered as silhouette blocks, so the rendered geometry is "
                    "graded B even though the source is A. See DOQ-008."
                ),
            },
            {
                "source_id": "DSRC-012",
                "title": "OpenStreetMap ferry routes and terminals",
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
                    "East River ferry route lines and landings, including the real Pier 11 to "
                    "DUMBO/Fulton Ferry service. Vessels follow these lines; their speed and "
                    "spacing are nominal, not a timetable. See DOQ-009."
                ),
            },
            {
                "source_id": "DSRC-010",
                "title": "NYC Planimetric Database: sidewalk, roadbed, curbs, plazas, parks, boardwalk",
                "tier": "A",
                "publisher": "NYC Office of Technology and Innovation, via NYC Open Data",
                "url": "https://data.cityofnewyork.us/resource/52n9-sdep.json",
                "accessed": "2026-08-10",
                "license": "NYC Open Data Terms of Use",
                "attribution_required": True,
                "attribution_text": "Planimetric surfaces: NYC Open Data (OTI)",
                "native_crs": "EPSG:4326",
                "grants_confidence": "A",
                "verified": True,
                "notes": (
                    "Surveyed surfaces underfoot: pavement, carriageway, plaza, park and boardwalk "
                    "as traced polygons, plus surveyed kerb lines. Replaces the widened-centreline "
                    "approximation and closes DOQ-006. The kerb height applied to the lines is a "
                    "convention (DCTL-080), not a survey."
                ),
            },
            {
                "source_id": "DSRC-013",
                "title": "USGS 3DEP 1 m bare-earth digital elevation model",
                "tier": "A",
                "publisher": "U.S. Geological Survey, 3D Elevation Program",
                "url": (
                    "https://elevation.nationalmap.gov/arcgis/rest/services/"
                    "3DEPElevation/ImageServer"
                ),
                "accessed": "2026-08-09",
                "license": "Public domain (U.S. Government work)",
                "attribution_required": True,
                "attribution_text": "Elevation: USGS 3D Elevation Program (3DEP)",
                "native_crs": "EPSG:4326 query, EPSG:3857 service",
                "vertical_datum": "NAVD88",
                "units": "m",
                "positional_accuracy_m": 1.0,
                "grants_confidence": "A",
                "verified": True,
                "notes": (
                    "Bare-earth terrain sampled on the district ground grid at 8 m spacing from a "
                    "1 m source raster. Buildings are already removed, so this is the pavement "
                    "rather than the roofline. Cross-checked every build against building "
                    "ground_elevation from DSRC-003; the build fails if they disagree beyond "
                    "DCTL-075. Retires DOQ-003."
                ),
            },
            {
                "source_id": "DSRC-014",
                "title": "NYC Planimetric Database: Hydrography",
                "tier": "A",
                "publisher": "NYC Office of Technology and Innovation, via NYC Open Data",
                "url": "https://data.cityofnewyork.us/resource/pjs3-c3z5.json",
                "accessed": "2026-08-09",
                "license": "NYC Open Data Terms of Use",
                "attribution_required": True,
                "attribution_text": "Hydrography: NYC Open Data (OTI)",
                "native_crs": "EPSG:4326",
                "grants_confidence": "A",
                "verified": True,
                "notes": (
                    "Water body polygons including the East River and Navy Yard Basin. Supplies the "
                    "land/water mask for the terrain mesh, replacing the district scope boundary "
                    "which was never a shoreline. See DOQ-005."
                ),
            },
            {
                "source_id": "DSRC-015",
                "title": "Openly-licensed photographs of DUMBO",
                "tier": "C",
                "publisher": "Wikimedia Commons contributors",
                "url": "https://commons.wikimedia.org/",
                "accessed": "2026-08-09",
                "license": "Mixed: CC0-1.0, public domain, CC-BY-2.0/3.0/4.0, CC-BY-SA-2.0/3.0/4.0",
                "attribution_required": True,
                "attribution_text": "Photographs: Wikimedia Commons contributors, see photo-survey.json",
                "grants_confidence": "B",
                "verified": True,
                "notes": (
                    "First found-imagery campaign: 336 fetched, 62 kept after human review. Every one carries an explicit reuse "
                    "licence recorded with its credit line; anything without one is rejected rather "
                    "than assumed. Share-alike images are marked derive_appearance: colours are "
                    "measured from them, the images themselves are never vendored into this "
                    "repository. Photographs never grant A. See DOQ-007 and PHOTO-SURVEY.md."
                ),
            },
            {
                "source_id": "DSRC-016",
                "title": "OpenStreetMap street furniture",
                "tier": "B",
                "publisher": "OpenStreetMap contributors",
                "url": "https://www.openstreetmap.org/",
                "accessed": "2026-08-10",
                "license": "ODbL-1.0",
                "attribution_required": True,
                "attribution_text": "Street furniture: © OpenStreetMap contributors, ODbL",
                "grants_confidence": "A",
                "verified": True,
                "notes": (
                    "Railings, lamps, benches, bollards, bike racks, bins, hydrants and signals. "
                    "7.4 km of barrier line, which is what gives the Brooklyn Bridge Park promenade "
                    "an edge instead of ending at the water with nothing there. Note that not one "
                    "feature in the district uses the documented barrier=railing tag: the waterfront "
                    "guard rail is barrier=fence with fence_type=railing, so the ingest reads "
                    "fence_type first. Position grade A; heights, colours and forms are conventional "
                    "per type and graded C."
                ),
            },
        ],
        "provenance": provenance(control),
    }


def _required_attributions(control: DistrictControl) -> list[str]:
    """Every attribution line the register can supply, in register order, de-duplicated.

    Derived rather than hand-listed. A viewer is contractually obliged to display these, so a list
    maintained by hand becomes a licence exposure the moment a source is added and the list is not —
    which is exactly what had happened to street trees, elevation and hydrography.

    Any source carrying an `attribution_text` is credited, not only those where
    `attribution_required` is true. USGS and NOAA works are public domain and oblige nothing, but
    naming who measured the ground you are standing on costs a line of text.

    Several sources share a line — both OpenStreetMap entries want the same ODbL credit — so the set
    is collapsed while preserving order, keeping the footer stable between builds instead of
    reshuffling with dictionary iteration.
    """
    seen: dict[str, None] = {}
    for source in build_source_register(control)["sources"]:
        if source.get("attribution_text"):
            seen.setdefault(source["attribution_text"], None)
    return list(seen)


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
            "district terrain",
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
        # Derived from the source register rather than hand-listed. A viewer is contractually obliged
        # to display every one of these, so a list maintained by hand is a licence risk the moment a
        # source is added and the list is not: exactly what happened when trees, elevation and
        # hydrography arrived. Ask the register instead, and the obligation cannot fall behind.
        "attribution": _required_attributions(control),
        "not_implemented_yet": [
            "roof form modelling; all roofs are flat at the dataset roof height (DOQ-002)",
            "district photogrammetry (deliberately out of scope for Phase 1)",
            "interior spaces",
            "facade appearance from imagery; it is inferred from building class (DOQ-007)",
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


def build_ground_grid(
    control: DistrictControl, buildings: list[dict], index: dict, dem: "Dem | None" = None,
) -> dict:
    """
    Build the ground height surface.

    Preferred path: sample the USGS 3DEP 1 m bare-earth DEM (DSRC-013), ingested to
    data/terrain/dem.raw.json. Bare earth is what a walker stands on, already stripped of buildings,
    and it arrives in NAVD88 metres so nothing has to be transformed.

    Fallback path: if the DEM has not been ingested, interpolate from building base elevations as
    before and grade the result C under DOQ-003. A fresh clone that has not run the ingest step still
    builds and still runs; it simply tells the truth about how well it knows the ground.

    Land and water come from the city's hydrography polygons (DSRC-014) when available, so the
    shoreline is sourced rather than inferred from the project's own scope boundary.
    """
    cell = control.value_m("DCTL-070")
    ox, oy, span_x, span_y = control.tile_extent
    cols = int(math.ceil(span_x / cell)) + 1
    rows = int(math.ceil(span_y / cell)) + 1

    dem_grid = dem.grid if dem is not None else None
    if dem_grid is not None:
        heights = dem_grid
        confidence = "A"
        source_refs = ["DSRC-013"]
        control_refs = ["DCTL-070", "DCTL-074", "DCTL-075", "DCTL-076"]
        open_questions: list[str] = []
        method = "usgs_3dep_1m_bare_earth"
        notes = (
            "Sampled from the USGS 3DEP 1 m bare-earth DEM in NAVD88 metres, bilinear. Grade A, "
            "cross-checked every build against building ground_elevation (DSRC-003)."
        )
    else:
        print("    no DEM ingested; falling back to interpolation from building base elevations")
        heights = _interpolate_ground(control, buildings, ox, oy, cell, cols, rows)
        confidence = "C"
        source_refs = ["DSRC-001", "DSRC-003"]
        control_refs = ["DCTL-070", "DCTL-071", "DCTL-072", "DCTL-073"]
        open_questions = ["DOQ-003"]
        method = "idw_building_base_elevations"
        notes = (
            "Inverse-distance interpolation of building base elevations. Grade C: the samples are "
            "authoritative, the surface between them is inferred. Run "
            "`python scripts/ingest_sources.py --dem` to replace this with DSRC-013."
        )

    land, land_source = _land_mask(control, ox, oy, cell, cols, rows)
    land_cells = sum(sum(line) for line in land)

    agreement = (
        _dem_agreement(control, buildings, heights, ox, oy, cell, cols, rows)
        if dem_grid is not None else None
    )

    land_heights = [
        heights[r][c] for r in range(rows) for c in range(cols) if land[r][c]
    ] or [v for line in heights for v in line]
    flat = [v for line in heights for v in line]

    document = {
        "contract_version": CONTRACT_VERSION,
        "module_id": MODULE_ID,
        "frame_id": FRAME_ID,
        "origin_xy_m": [ox, oy],
        "cell_m": cell,
        "cols": cols,
        "rows": rows,
        "vertical_datum": "NAVD88",
        "confidence": confidence,
        "method": method,
        "source_refs": source_refs + ([land_source] if land_source else []),
        "control_refs": control_refs,
        "open_questions": open_questions,
        "notes": notes,
        "min_m": round(min(flat), 2),
        "max_m": round(max(flat), 2),
        "land_min_m": round(min(land_heights), 2),
        "land_max_m": round(max(land_heights), 2),
        "heights": heights,
        "land": land,
        "land_cells": land_cells,
        "land_mask_source": land_source or "district_boundary",
        "provenance": provenance(control),
    }
    if agreement:
        document["dem_agreement_m"] = agreement
    return document



def _interpolate_ground(
    control: DistrictControl, buildings: list[dict],
    ox: float, oy: float, cell: float, cols: int, rows: int,
) -> list[list[float]]:
    """Fallback surface: inverse-distance weighting of building base elevations."""
    radius = control.value_m("DCTL-071")
    neighbours = int(control.value("DCTL-072"))
    power = control.value("DCTL-073")

    samples = [(b["centroid"][0], b["centroid"][1], b["base_z"]) for b in buildings]
    if not samples:
        raise SystemExit("no building samples and no DEM; cannot build a ground surface")

    radius2 = radius * radius
    heights: list[list[float]] = []
    for row in range(rows):
        y = oy + row * cell
        line: list[float] = []
        for col in range(cols):
            x = ox + col * cell
            near = [
                (d2, sz) for sx, sy, sz in samples
                if (d2 := (sx - x) ** 2 + (sy - y) ** 2) <= radius2
            ]
            if not near:
                # Outside the sampled area. Fall back to the single nearest sample so the surface
                # stays continuous instead of collapsing to zero.
                _, sz = min(((sx - x) ** 2 + (sy - y) ** 2, sz) for sx, sy, sz in samples)
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
    return heights


def _land_mask(
    control: DistrictControl, ox: float, oy: float, cell: float, cols: int, rows: int,
) -> tuple[list[list[int]], str | None]:
    """Classify every cell as land or water.

    Preferred: the city's hydrography polygons, rasterised. Cells inside a water body are water and
    everything else is land, which lets the piers of Brooklyn Bridge Park read as what they are —
    land standing out over the river — instead of being cut off at a boundary line.

    Fallback: the district boundary, as before. Honest but crude; the boundary was drawn to scope the
    project, not to trace a shoreline.
    """
    path = REPO_ROOT / "data" / "terrain" / "hydrography.raw.json"
    if path.exists():
        records = json.loads(path.read_text(encoding="utf-8"))
        water = _rasterise_water(control, records, ox, oy, cell, cols, rows)
        wet = sum(sum(line) for line in water)
        if wet:
            print(f"    land mask from hydrography: {wet} water cells of {cols * rows}")
            return [[0 if water[r][c] else 1 for c in range(cols)] for r in range(rows)], "DSRC-014"
        print("    hydrography covered no cells; falling back to the district boundary")

    ring_enu = [control.geodetic_to_enu(lon, lat)[:2] for lon, lat in control.boundary_ring]
    apron = cell * 1.5
    land: list[list[int]] = []
    for row in range(rows):
        y = oy + row * cell
        line: list[int] = []
        for col in range(cols):
            x = ox + col * cell
            inside = point_in_ring((x, y), ring_enu)
            if not inside:
                nearest = min(
                    distance_point_to_segment((x, y), ring_enu[i], ring_enu[i + 1])
                    for i in range(len(ring_enu) - 1)
                )
                inside = nearest <= apron
            line.append(1 if inside else 0)
        land.append(line)
    return land, None


def _rasterise_water(
    control: DistrictControl, records: list[dict],
    ox: float, oy: float, cell: float, cols: int, rows: int,
) -> list[list[int]]:
    """Scanline-fill water polygons onto the grid.

    Testing every cell against every edge would be 29,025 x ~4,400 point-in-polygon operations. A
    scanline fill is one pass per grid row instead, so the cost falls to rows x edges. Each polygon
    is filled independently under the even-odd rule — which handles its holes correctly — and the
    results are OR-ed, so two overlapping water bodies cannot cancel each other out.
    """
    water = [[0] * cols for _ in range(rows)]
    for record in records:
        geom = record.get("the_geom") or {}
        if geom.get("type") == "MultiPolygon":
            polygons = geom.get("coordinates", [])
        elif geom.get("type") == "Polygon":
            polygons = [geom.get("coordinates", [])]
        else:
            continue
        for polygon in polygons:
            edges: list[tuple[float, float, float, float]] = []
            for ring in polygon:
                pts = [control.geodetic_to_enu(lon, lat)[:2] for lon, lat, *_ in ring]
                for i in range(len(pts) - 1):
                    (x0, y0), (x1, y1) = pts[i], pts[i + 1]
                    if y0 != y1:
                        edges.append((x0, y0, x1, y1))
            if not edges:
                continue
            for row in range(rows):
                y = oy + row * cell
                crossings = sorted(
                    x0 + (y - y0) * (x1 - x0) / (y1 - y0)
                    for x0, y0, x1, y1 in edges
                    if (y0 <= y < y1) or (y1 <= y < y0)
                )
                for i in range(0, len(crossings) - 1, 2):
                    start = max(0, int(math.ceil((crossings[i] - ox) / cell)))
                    end = min(cols - 1, int(math.floor((crossings[i + 1] - ox) / cell)))
                    for col in range(start, end + 1):
                        water[row][col] = 1
    return water


def _dem_agreement(
    control: DistrictControl, buildings: list[dict], heights: list[list[float]],
    ox: float, oy: float, cell: float, cols: int, rows: int,
) -> dict:
    """Cross-check the DEM against an independent measurement of the same quantity.

    `ground_elevation` on each building footprint is a grade A NAVD88 elevation produced by a
    different agency from a different survey. Comparing it to the DEM at the same place is the one
    check that can catch a wrong vertical datum, wrong units, or a misregistered sampling grid — all
    of which produce a perfectly plausible-looking terrain that is quietly, uniformly wrong.

    Two statistics, because they fail differently and a single threshold would conflate them:

    **Bias** is the signed median. It is the systematic-error detector. A datum slip moves every
    sample the same way, so a 0.59 m MHW-for-NAVD88 mistake shows up here as a 0.59 m offset and
    nowhere else. DCTL-075 bounds it.

    **Spread** is the 95th percentile of the absolute difference. It catches misregistration, which
    scatters rather than shifts: a grid offset by a block drags samples onto the wrong side of
    DUMBO's slope and the disagreement fans out while the median may barely move. DCTL-076 bounds
    it, and is necessarily looser, because the two quantities are not quite the same thing —
    `ground_elevation` is the *lowest* point of a building, and a large building on a slope will
    legitimately sit below a DEM sample taken at its centroid.

    Buildings whose base came from the DEM are excluded: a value cannot corroborate itself.
    """
    diffs: list[float] = []
    for building in buildings:
        if building["attributes"].get("ground_elevation_basis") != "dataset":
            continue
        cx, cy = building["centroid"]
        col = int(round((cx - ox) / cell))
        row = int(round((cy - oy) / cell))
        if 0 <= col < cols and 0 <= row < rows:
            diffs.append(heights[row][col] - building["base_z"])
    if not diffs:
        return {}

    signed = sorted(diffs)
    absolute = sorted(abs(d) for d in diffs)
    n = len(diffs)

    def pct(values: list[float], p: float) -> float:
        return round(values[min(n - 1, int(p * n))], 3)

    result = {
        "samples": n,
        "bias_m": pct(signed, 0.5),
        "abs_median_m": pct(absolute, 0.5),
        "p95_m": pct(absolute, 0.95),
        "max_m": round(absolute[-1], 3),
        "over_3m": sum(1 for d in absolute if d > 3.0),
        "compared_against": "DSRC-003 ground_elevation",
    }

    bias_limit = control.value_m("DCTL-075")
    spread_limit = control.value_m("DCTL-076")
    print(f"    DEM vs building ground_elevation over {n} buildings:")
    print(f"      bias {result['bias_m']:+.3f} m (limit ±{bias_limit}) · "
          f"p95 {result['p95_m']:.3f} m (limit {spread_limit}) · max {result['max_m']:.2f} m")

    if abs(result["bias_m"]) > bias_limit:
        raise SystemExit(
            f"DEM is systematically offset from building ground elevations: bias "
            f"{result['bias_m']:+.3f} m exceeds DCTL-075 limit ±{bias_limit} m. "
            "Check the vertical datum and the units."
        )
    if result["p95_m"] > spread_limit:
        raise SystemExit(
            f"DEM disagrees too widely with building ground elevations: p95 {result['p95_m']} m "
            f"exceeds DCTL-076 limit {spread_limit} m. Check the grid registration."
        )
    return result


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
    dem = Dem.load(control)
    if dem is None:
        print("    no DEM ingested (run: python scripts/ingest_sources.py --dem)")
    buildings, stats = build_buildings(control, dem)
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
    ground = build_ground_grid(control, buildings, index, dem)
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
