"""
Build walk-mode scene dressing: street trees, paving surfaces and facade appearance.

The principle throughout: **this repository owns the assets, the viewer owns nothing.** Everything
here is emitted as data conforming to shared contracts, so the same viewer renders a different
neighbourhood, or a bridge, by pointing at different files.

Outputs, all under viewer/public/district/:
  props.json          street trees as instanced props (scene-props.schema.json)
  paving.json         roadway and sidewalk surface polygons derived from the walk network
  facades.json        per-building facade appearance derived from PLUTO attributes

Sources:
  DSRC-009  NYC Forestry street tree points (species, trunk diameter)
  DSRC-007  OpenStreetMap ways (paving centrelines and widths)
  DSRC-002  MapPLUTO (building class and year, which drive facade appearance)
"""

from __future__ import annotations

import argparse
import hashlib
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

INCH = 0.0254

# Canopy proportions by genus, from typical mature form. These shape the procedural fallback; they
# are not measurements, which is why every tree prototype is graded C.
GENUS_FORM = {
    "Platanus":       {"label": "London plane",      "spread": 1.15, "height": 1.00, "tint": "#4a6b3a"},
    "Quercus":        {"label": "Oak",               "spread": 1.10, "height": 1.05, "tint": "#43653a"},
    "Gleditsia":      {"label": "Honey locust",      "spread": 0.95, "height": 0.95, "tint": "#5c7a42"},
    "Pyrus":          {"label": "Callery pear",      "spread": 0.75, "height": 0.80, "tint": "#557038"},
    "Tilia":          {"label": "Linden",            "spread": 0.90, "height": 1.00, "tint": "#4d6c3c"},
    "Ginkgo":         {"label": "Ginkgo",            "spread": 0.80, "height": 1.00, "tint": "#5a763f"},
    "Styphnolobium":  {"label": "Japanese pagoda",   "spread": 1.00, "height": 0.95, "tint": "#4f6d3d"},
    "Acer":           {"label": "Maple",             "spread": 1.00, "height": 0.95, "tint": "#476639"},
    "Zelkova":        {"label": "Zelkova",           "spread": 1.05, "height": 1.00, "tint": "#4c6a3b"},
}
DEFAULT_FORM = {"label": "Street tree", "spread": 0.95, "height": 0.95, "tint": "#4e6c3c"}

# Street furniture prototypes. Height and colour are conventional values, not measurements, which is
# why every one of these is graded C: OSM gives an authoritative *position*, and says nothing about
# what the thing looks like.
#
# The tag key is deliberately more specific than `barrier` alone. OSM in DUMBO tags the waterfront
# guard rail as `barrier=fence` + `fence_type=railing`, not `barrier=railing`, and a promenade railing
# and a substation's chain-link have no business rendering alike.
FURNITURE_FORM = {
    "railing":      {"kind": "fence",         "label": "Waterfront railing",   "height": 1.10, "tint": "#3c4348", "panel": 2.0},
    "fence_bars":   {"kind": "fence",         "label": "Bar fence",            "height": 1.80, "tint": "#3a3f42", "panel": 2.0},
    "fence_chain":  {"kind": "fence",         "label": "Chain-link fence",     "height": 2.10, "tint": "#6b7075", "panel": 2.5},
    "fence_wood":   {"kind": "fence",         "label": "Timber fence",         "height": 1.80, "tint": "#6d5a44", "panel": 2.0},
    "fence":        {"kind": "fence",         "label": "Fence",                "height": 1.80, "tint": "#4a4f52", "panel": 2.0},
    "wall_stone":   {"kind": "wall",          "label": "Stone wall",           "height": 1.20, "tint": "#7d7367", "panel": 2.0, "thickness": 0.35},
    "wall_brick":   {"kind": "wall",          "label": "Brick wall",           "height": 1.60, "tint": "#725140", "panel": 2.0, "thickness": 0.30},
    "wall":         {"kind": "wall",          "label": "Wall",                 "height": 1.40, "tint": "#8a8781", "panel": 2.0, "thickness": 0.30},
    "bench":        {"kind": "bench",         "label": "Bench",                "height": 0.90, "tint": "#6b5740", "size": 1.80},
    "lamp":         {"kind": "lamp",          "label": "Street lamp",          "height": 6.50, "tint": "#3f4448", "size": 0.30},
    "bollard":      {"kind": "bollard",       "label": "Bollard",              "height": 0.95, "tint": "#43474a", "size": 0.25},
    "bike_rack":    {"kind": "custom",        "label": "Bicycle parking",      "height": 0.85, "tint": "#4c5155", "size": 0.80},
    "bin":          {"kind": "bin",           "label": "Litter basket",        "height": 0.95, "tint": "#3d4b3f", "size": 0.60},
    "hydrant":      {"kind": "hydrant",       "label": "Fire hydrant",         "height": 0.80, "tint": "#9c3b32", "size": 0.30},
    "traffic_light": {"kind": "traffic_light", "label": "Traffic signal",      "height": 4.20, "tint": "#3a3e41", "size": 0.28},
    "flagpole":     {"kind": "custom",        "label": "Flagpole",             "height": 8.00, "tint": "#8d9095", "size": 0.20},
    "fountain":     {"kind": "custom",        "label": "Fountain",             "height": 0.70, "tint": "#6d7378", "size": 2.00},
}


def _furniture_line_form(tags: dict) -> str:
    """Which prototype a barrier way should use.

    `fence_type` is consulted before `barrier`, because it is the tag that actually distinguishes
    the promenade's railing from the substation's chain-link, and OSM here records both as
    `barrier=fence`.
    """
    barrier = (tags.get("barrier") or "").strip()
    fence_type = (tags.get("fence_type") or "").strip()
    material = (tags.get("material") or "").strip()

    if barrier in {"railing", "handrail", "guard_rail"}:
        return "railing"
    if barrier == "wall":
        if material in {"stone", "brick"}:
            return f"wall_{material}"
        return "wall"
    if fence_type in {"railing", "bars", "metal_bars"}:
        return "railing" if fence_type == "railing" else "fence_bars"
    if fence_type == "chain_link":
        return "fence_chain"
    if fence_type == "wood":
        return "fence_wood"
    return "fence"


def _furniture_point_form(tags: dict) -> str | None:
    """Which prototype an OSM node should use, or None to ignore it."""
    if tags.get("highway") == "street_lamp":
        return "lamp"
    if tags.get("highway") == "traffic_signals":
        return "traffic_light"
    if tags.get("amenity") == "bench":
        return "bench"
    if tags.get("amenity") == "bicycle_parking":
        return "bike_rack"
    if tags.get("amenity") == "waste_basket":
        return "bin"
    if tags.get("amenity") == "fountain":
        return "fountain"
    if tags.get("amenity") == "drinking_fountain":
        return "fountain"
    if tags.get("barrier") == "bollard":
        return "bollard"
    if tags.get("emergency") == "fire_hydrant":
        return "hydrant"
    if tags.get("man_made") == "flagpole":
        return "flagpole"
    return None


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


def write(path: Path, payload: object, *, compact: bool = True) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, separators=(",", ":")) if compact else json.dumps(payload, indent=1)
    path.write_text(text, encoding="utf-8")
    return len(text.encode("utf-8"))


# ------------------------------------------------------------------- trees


def build_props(control: DistrictControl, index: dict) -> dict:
    """
    Street trees as instanced props.

    Every tree is a real record from the NYC Forestry census: a real position, a real species and a
    real trunk diameter. Trunk diameter drives per-tree scale, so a street reads as a row of
    individuals of different ages rather than a row of clones. That is the difference between
    scenery that looks placed and scenery that looks copied.

    The geometry itself is procedural and graded C: the positions and species are authoritative,
    the canopy shape is a plausible form for that genus, not a measurement of that tree.
    """
    trees = load(DATA / "streetscape" / "trees.raw.json")
    ring = control.boundary_ring

    size = index["scheme"]["tile_size_m"]
    ox, oy = index["scheme"]["origin_xy_m"]

    used_genera: dict[str, int] = {}
    instances: list[dict] = []
    skipped = 0

    for record in trees:
        location = record.get("location") or {}
        coords = location.get("coordinates")
        if not coords or len(coords) < 2:
            skipped += 1
            continue
        lon, lat = float(coords[0]), float(coords[1])
        if not point_in_ring((lon, lat), ring):
            continue

        structure = (record.get("tpstructure") or "").strip()
        if structure in {"Stump", "Shaft"}:
            # Present in the data but not a tree any longer.
            continue

        genus = (record.get("genusspecies") or "").split(" ")[0] or "unknown"
        if genus not in GENUS_FORM:
            genus = "other"
        used_genera[genus] = used_genera.get(genus, 0) + 1

        dbh_in = _as_float(record.get("dbh")) or 6.0
        # Trunk diameter to canopy height: a rough forestry rule of thumb is that street trees run
        # about 0.55 m of height per inch of DBH, floored so saplings are still visible.
        height_m = max(3.0, min(22.0, dbh_in * 0.55))

        x, y, _ = control.geodetic_to_enu(lon, lat)
        col = int(math.floor((x - ox) / size))
        row = int(math.floor((y - oy) / size))

        instances.append(
            {
                "p": f"tree_{genus.lower()}",
                "xy": [round(x, 2), round(y, 2)],
                # Deterministic pseudo-random yaw from the record ID, so canopies are not all
                # identically oriented but the build stays reproducible.
                "r": (int(record.get("objectid") or 0) * 37) % 360,
                "s": round(height_m / 10.0, 3),
                "tile": f"t_{col}_{row}",
            }
        )

    prototypes = []
    for genus in sorted(used_genera):
        form = GENUS_FORM.get(genus, DEFAULT_FORM)
        prototypes.append(
            {
                "prototype_id": f"tree_{genus.lower()}",
                "kind": "tree",
                "label": form["label"],
                "format": "procedural",
                "size_m": [
                    round(10.0 * form["spread"], 2),
                    round(10.0 * form["spread"], 2),
                    round(10.0 * form["height"], 2),
                ],
                "billboard": False,
                "casts_shadow": False,
                "source_basis": ["official_dataset", "procedural"],
                "source_refs": ["DSRC-009"],
                "confidence": "C",
                "notes": (
                    f"{form['label']}. Position, species and trunk diameter are grade A from the "
                    "Forestry census; the canopy form is a plausible shape for the genus, not a "
                    f"measurement, so the prop is graded C. Foliage tint {form['tint']}."
                ),
            }
        )

    print(f"    {len(instances)} trees placed, {len(prototypes)} prototypes, {skipped} skipped")
    print(f"    genera: {dict(sorted(used_genera.items(), key=lambda kv: -kv[1])[:5])}")

    furniture_prototypes, furniture_instances = build_street_furniture(control, index)
    prototypes.extend(furniture_prototypes)
    instances.extend(furniture_instances)

    storefront_prototypes, storefront_instances = build_storefronts(control, index)
    prototypes.extend(storefront_prototypes)
    instances.extend(storefront_instances)

    return {
        "contract_version": CONTRACT_VERSION,
        "module_id": MODULE_ID,
        "frame_id": FRAME_ID,
        "prototypes": prototypes,
        "instances": instances,
        "provenance": provenance(control),
    }


def build_street_furniture(control: DistrictControl, index: dict) -> tuple[list[dict], list[dict]]:
    """Railings, lamps, benches and the rest, as instanced props.

    Railings matter more than the rest put together. The Brooklyn Bridge Park waterfront is fenced
    along almost its whole length, and a promenade rendered without one does not read as a
    promenade — it reads as a lawn that stops at the water, and a walker gets no sense of an edge
    they cannot cross.

    Barriers are lines, and the prop contract places points, so each way is walked at a fixed
    spacing and a panel dropped at every step, yawed to the segment it sits on. That keeps the
    existing schema and the existing instanced renderer, and it means a real railing model later is
    a change to one prototype's `url`.

    Positions are grade A from OSM. Heights and colours are conventional, so every prototype is
    graded C — the survey says a railing is *there*, not what it looks like.
    """
    path = DATA / "streets" / "osm-street-furniture.raw.json"
    if not path.exists():
        print("    no street furniture source; run ingest_sources.py --furniture")
        return [], []

    raw = load(path)
    ring = control.boundary_ring
    size = index["scheme"]["tile_size_m"]
    ox, oy = index["scheme"]["origin_xy_m"]

    used: dict[str, int] = {}
    instances: list[dict] = []

    def emit(form: str, x: float, y: float, yaw: float, scale: float = 1.0) -> None:
        col = int(math.floor((x - ox) / size))
        row = int(math.floor((y - oy) / size))
        used[form] = used.get(form, 0) + 1
        instance = {
            "p": f"prop_{form}",
            "xy": [round(x, 2), round(y, 2)],
            "r": round(yaw % 360, 1),
            "tile": f"t_{col}_{row}",
        }
        if abs(scale - 1.0) > 1e-3:
            instance["s"] = round(scale, 3)
        instances.append(instance)

    barrier_m = 0.0
    for line in raw.get("lines", []):
        form = _furniture_line_form(line.get("tags") or {})
        spacing = FURNITURE_FORM[form]["panel"]
        coords = line.get("geometry") or []
        # Project once, then walk in metres. Doing it the other way round means resampling in
        # degrees, where a step is a different length north-south than east-west.
        runs: list[list[tuple[float, float]]] = [[]]
        for lon, lat in coords:
            if not point_in_ring((lon, lat), ring):
                if runs[-1]:
                    runs.append([])
                continue
            x, y, _ = control.geodetic_to_enu(float(lon), float(lat))
            runs[-1].append((x, y))

        for run in runs:
            if len(run) < 2:
                continue
            # Walk the whole run by arc length rather than segment by segment. Stepping per segment
            # looks equivalent and is not: an OSM way often has vertices centimetres apart, and each
            # one would get its own panel squashed to fit, so a railing would come out as a row of
            # 5 cm stumps wherever the survey happened to be dense.
            spans = []
            for a, b in zip(run, run[1:]):
                length = math.hypot(b[0] - a[0], b[1] - a[1])
                if length > 1e-6:
                    spans.append((a, b, length))
            total = sum(s[2] for s in spans)
            if total < spacing * 0.4:
                continue
            barrier_m += total

            steps = max(1, math.ceil(total / spacing))
            step_len = total / steps
            for step in range(steps):
                target = (step + 0.5) * step_len
                travelled = 0.0
                for a, b, length in spans:
                    if travelled + length >= target:
                        t = (target - travelled) / length
                        yaw = math.degrees(math.atan2(b[1] - a[1], b[0] - a[0]))
                        # Deliberately not scaled to close the gap. Instance scale is uniform, so
                        # stretching a panel to fit would stretch its height too and the railing
                        # would ripple. Stepping shorter than the panel instead makes neighbours
                        # overlap slightly, which on a fence is invisible, whereas a gap is not.
                        emit(form, a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t, yaw)
                        break
                    travelled += length

    for point in raw.get("points", []):
        form = _furniture_point_form(point.get("tags") or {})
        if form is None:
            continue
        lon, lat = float(point["lon"]), float(point["lat"])
        if not point_in_ring((lon, lat), ring):
            continue
        x, y, _ = control.geodetic_to_enu(lon, lat)
        # Benches and racks have a direction; nothing in the data says which, so a deterministic
        # pseudo-random yaw from the OSM id at least stops a row of them facing identically.
        yaw = (int(point["osm_id"]) * 47) % 360
        emit(form, x, y, yaw)

    prototypes = []
    for form in sorted(used):
        spec = FURNITURE_FORM[form]
        footprint = spec.get("panel") or spec.get("size") or 0.5
        # size_m is [length, thickness, height]. The middle value only matters for the things that
        # are a run rather than an object — a wall 2 m thick would swallow the pavement behind it.
        thickness = spec.get("thickness", 0.12 if spec.get("panel") else footprint)
        prototypes.append(
            {
                "prototype_id": f"prop_{form}",
                "kind": spec["kind"],
                "label": spec["label"],
                "format": "procedural",
                "size_m": [round(footprint, 2), round(thickness, 2), round(spec["height"], 2)],
                "billboard": False,
                "casts_shadow": False,
                "source_basis": ["official_dataset", "procedural"],
                "source_refs": ["DSRC-016"],
                "confidence": "C",
                "notes": (
                    f"{spec['label']}. Position is grade A from OpenStreetMap; the height and form "
                    f"are conventional values rather than measurements, so the prop is graded C. "
                    f"Tint {spec['tint']}."
                ),
            }
        )

    print(f"    {len(instances)} furniture instances, {len(prototypes)} prototypes")
    print(f"    {barrier_m:,.0f} m of barrier line")
    print(f"    {dict(sorted(used.items(), key=lambda kv: -kv[1])[:6])}")
    return prototypes, instances


# Awning canvas, varied so a parade of shopfronts is not one colour. Assigned deterministically from
# the OSM id: DUMBO's storefront awnings are overwhelmingly dark canvas, and these are the four that
# actually recur along Front and Water Street.
AWNING_TINT = ["#2f4438", "#3a2f2c", "#4a2b2f", "#2c3644"]

# Ground floor of a DUMBO warehouse is tall; the awning hangs below the first-floor sill.
AWNING_HEIGHT_M = 3.0
AWNING_WIDTH_M = 3.4
AWNING_DEPTH_M = 1.5

# Past this a business and a building are not the same address. Deliberately generous: OSM often
# places a node at the middle of a unit rather than at its door.
STOREFRONT_MATCH_RADIUS_M = 30.0


def build_storefronts(control: DistrictControl, index: dict) -> tuple[list[dict], list[dict]]:
    """Awnings over the ground-floor businesses, hung on the facade they belong to.

    A DUMBO warehouse at street level is a row of shopfronts under a brick wall. Without them every
    building meets the pavement as a blank face, which is the single most obvious way the model still
    reads as a model.

    The work is in the placement rather than the geometry. An OSM node sits *somewhere inside* a
    business, so the awning is projected onto the nearest facade edge and turned to face outward,
    away from the footprint's interior. Hanging it at the node itself would leave awnings floating in
    the middle of rooms, and guessing the facing would put half of them inside the building.

    Graded D. The business is real and its building is real; that it has an awning at all, and the
    size and colour of that awning, are decoration.
    """
    path = DATA / "streets" / "osm-storefronts.raw.json"
    if not path.exists():
        print("    no storefront source; run ingest_sources.py --storefronts")
        return [], []

    shops = load(path)
    ring = control.boundary_ring
    size = index["scheme"]["tile_size_m"]
    ox, oy = index["scheme"]["origin_xy_m"]

    # Project every footprint edge once.
    edges: list[tuple[tuple[float, float], tuple[float, float], tuple[float, float]]] = []
    cell = 40.0
    grid: dict[tuple[int, int], list[int]] = {}
    for record in load(DATA / "footprints" / "footprints.raw.json"):
        for polygon in _rings_of(record.get("the_geom")):
            projected = [control.geodetic_to_enu(float(p[0]), float(p[1]))[:2] for p in polygon]
            if len(projected) < 3:
                continue
            cx = sum(p[0] for p in projected) / len(projected)
            cy = sum(p[1] for p in projected) / len(projected)
            for a, b in zip(projected, projected[1:]):
                if math.hypot(b[0] - a[0], b[1] - a[1]) < 1.0:
                    continue
                edges.append((a, b, (cx, cy)))
                key = (int(math.floor((a[0] + b[0]) / 2 / cell)), int(math.floor((a[1] + b[1]) / 2 / cell)))
                grid.setdefault(key, []).append(len(edges) - 1)

    instances: list[dict] = []
    used: dict[str, int] = {}
    unmatched = 0

    for shop in shops:
        lon, lat = float(shop["lon"]), float(shop["lat"])
        if not point_in_ring((lon, lat), ring):
            continue
        sx, sy, _ = control.geodetic_to_enu(lon, lat)

        best = None
        base = (int(math.floor(sx / cell)), int(math.floor(sy / cell)))
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for idx in grid.get((base[0] + dx, base[1] + dy), ()):
                    a, b, centroid = edges[idx]
                    distance = _point_segment_distance(sx, sy, a, b)
                    if best is None or distance < best[0]:
                        best = (distance, a, b, centroid)
        if best is None or best[0] > STOREFRONT_MATCH_RADIUS_M:
            unmatched += 1
            continue

        _, a, b, centroid = best
        ax, ay = a
        bx, by = b
        ex, ey = bx - ax, by - ay
        span = math.hypot(ex, ey)
        if span < 1e-6:
            continue
        # Foot of the perpendicular: where on this wall the shop actually is.
        t = max(0.0, min(1.0, ((sx - ax) * ex + (sy - ay) * ey) / (span * span)))
        px, py = ax + ex * t, ay + ey * t

        # Outward normal: the one that points away from the footprint's middle. Choosing by winding
        # would need every ring to wind the same way, and the published footprints do not.
        nx, ny = -ey / span, ex / span
        if (px + nx - centroid[0]) ** 2 + (py + ny - centroid[1]) ** 2 < (px - centroid[0]) ** 2 + (py - centroid[1]) ** 2:
            nx, ny = -nx, -ny

        variant = int(shop["osm_id"]) % len(AWNING_TINT)
        form = f"awning_{variant}"
        used[form] = used.get(form, 0) + 1
        # Yaw so that the prototype's local +Z lands on the outward normal, because the canopy is
        # authored projecting along +Z. Rotation about render +Y by t maps local +Z to scene
        # (sin t, -cos t), so t = atan2(nx, -ny); local +X then falls along the wall, as it must for
        # the canopy's width. Getting this backwards hangs every awning inside the building.
        yaw = math.degrees(math.atan2(nx, -ny))
        col = int(math.floor((px - ox) / size))
        row = int(math.floor((py - oy) / size))
        instances.append(
            {
                "p": f"prop_{form}",
                "xy": [round(px, 2), round(py, 2)],
                "r": round(yaw % 360, 1),
                "tile": f"t_{col}_{row}",
            }
        )

    prototypes = []
    for form in sorted(used):
        variant = int(form.rsplit("_", 1)[1])
        prototypes.append(
            {
                "prototype_id": f"prop_{form}",
                "kind": "awning",
                "label": "Shopfront awning",
                "format": "procedural",
                "size_m": [AWNING_WIDTH_M, AWNING_DEPTH_M, AWNING_HEIGHT_M],
                "billboard": False,
                "casts_shadow": False,
                "source_basis": ["official_dataset", "inferred"],
                "source_refs": ["DSRC-017", "DSRC-001"],
                "confidence": "D",
                "notes": (
                    "Awning over a ground-floor business. The business and the wall it hangs on are "
                    "real; that it has an awning, and its size and colour, are decoration, so this "
                    f"is graded D. Tint {AWNING_TINT[variant]}."
                ),
            }
        )

    print(f"    {len(instances)} awnings on {len(prototypes)} canvas variants, {unmatched} unmatched")
    return prototypes, instances


def _as_float(value: object) -> float | None:
    try:
        result = float(str(value))
        return result if math.isfinite(result) else None
    except (TypeError, ValueError):
        return None



    try:
        result = float(str(value))
        return result if math.isfinite(result) else None
    except (TypeError, ValueError):
        return None


# ------------------------------------------------------------------ paving


# Half-widths in meters by OSM highway class. Carriageway first, then the sidewalk strip either
# side. Values are typical for this district rather than surveyed, hence grade C.
ROAD_PROFILE = {
    "primary":       (9.0, 3.0),
    "secondary":     (8.0, 3.0),
    "tertiary":      (7.0, 3.0),
    "residential":   (6.0, 2.5),
    "living_street": (5.0, 2.0),
    "unclassified":  (5.5, 2.5),
    "service":       (3.5, 1.2),
    "pedestrian":    (4.5, 0.0),
    "footway":       (1.8, 0.0),
    "path":          (1.5, 0.0),
    "steps":         (1.5, 0.0),
    "cycleway":      (1.5, 0.0),
    "track":         (2.5, 0.0),
}

SURFACE_KIND = {
    "pedestrian": "plaza",
    "footway": "sidewalk",
    "path": "sidewalk",
    "steps": "steps",
    "cycleway": "cycleway",
}


# What OSM's surface values mean for rendering. Only the ones that actually look different are
# worth carrying: DUMBO's Belgian block is the whole point of this, and calling it out from the
# asphalt is the difference between "a street" and "*that* street".
SURFACE_MATERIAL = {
    "sett": "cobblestone",
    "cobblestone": "cobblestone",
    "cobblestone:flattened": "cobblestone",
    "unhewn_cobblestone": "cobblestone",
    "paving_stones": "paving_stones",
    "paving_stones:30": "paving_stones",
    "concrete": "concrete",
    "concrete:plates": "concrete",
    "concrete:lanes": "concrete",
    "asphalt": "asphalt",
    "wood": "wood",
    "gravel": "gravel",
    "fine_gravel": "gravel",
    "compacted": "gravel",
}

# Which OSM ways may speak for which surveyed polygon. Without this split a pavement polygon takes
# its material from the carriageway it happens to run beside, and every footway in DUMBO comes out
# asphalt.
_VEHICLE_WAYS = {
    "motorway", "trunk", "primary", "secondary", "tertiary", "residential",
    "unclassified", "service", "living_street", "road", "busway",
}
_FOOT_WAYS = {"footway", "path", "pedestrian", "steps", "cycleway", "track", "corridor"}

_SURFACE_MATCH = {
    "roadway": _VEHICLE_WAYS,
    "sidewalk": _FOOT_WAYS,
    "plaza": _FOOT_WAYS,
}
# park and boardwalk are deliberately absent. A park polygon is the lawn, not the path crossing it,
# and a boardwalk is wood by definition; letting either take its material from the nearest footway
# paved over both.

# Beyond this a surveyed polygon and an OSM way are not describing the same piece of ground.
SURFACE_MATCH_RADIUS_M = 18.0


def _attach_surfaces(control: DistrictControl, surfaces: list[dict]) -> dict[str, int]:
    """Give each surveyed polygon the material of the OSM way that runs through it.

    The survey traces where the ground is, to the centimetre, and says nothing about what it is made
    of. OSM says what it is made of and is vague about where. Joining them keeps the good half of
    each: surveyed geometry, tagged material.

    Matching is restricted by kind, which matters more than it looks. DUMBO's carriageways are
    Belgian block while the pavement beside them is concrete; a nearest-way join that ignored the
    distinction would hand each the other's material, and the cobblestone would end up on the
    footway.
    """
    path = DATA / "streets" / "osm-ways.raw.json"
    if not path.exists():
        return {}

    # Bucket way segments on a coarse grid so each polygon only tests its own neighbourhood.
    cell = 40.0
    grid: dict[tuple[int, int], list] = {}
    for way in load(path):
        tags = way.get("tags") or {}
        material = SURFACE_MATERIAL.get((tags.get("surface") or "").strip())
        if not material:
            continue
        highway = (tags.get("highway") or "").strip()
        if tags.get("footway") or tags.get("sidewalk"):
            highway = highway or "footway"
        if not highway:
            continue
        geometry = way.get("geometry") or []
        projected = [control.geodetic_to_enu(float(p["lon"]), float(p["lat"]))[:2] for p in geometry]
        for a, b in zip(projected, projected[1:]):
            entry = (a, b, material, highway)
            for key in {
                (int(math.floor(p[0] / cell)), int(math.floor(p[1] / cell)))
                for p in (a, b, ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2))
            }:
                grid.setdefault(key, []).append(entry)

    if not grid:
        return {}

    counts: dict[str, int] = {}
    for surface in surfaces:
        allowed = _SURFACE_MATCH.get(surface["kind"])
        if not allowed:
            continue
        ring = surface["ring"]
        cx = sum(p[0] for p in ring) / len(ring)
        cy = sum(p[1] for p in ring) / len(ring)

        best: tuple[float, str] | None = None
        base = (int(math.floor(cx / cell)), int(math.floor(cy / cell)))
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for a, b, material, highway in grid.get((base[0] + dx, base[1] + dy), ()):
                    if highway not in allowed:
                        continue
                    distance = _point_segment_distance(cx, cy, a, b)
                    if best is None or distance < best[0]:
                        best = (distance, material)

        if best and best[0] <= SURFACE_MATCH_RADIUS_M:
            surface["surface"] = best[1]
            counts[best[1]] = counts.get(best[1], 0) + 1
    return counts


def _point_segment_distance(px: float, py: float, a, b) -> float:
    ax, ay = a
    bx, by = b
    dx, dy = bx - ax, by - ay
    span = dx * dx + dy * dy
    if span < 1e-9:
        return math.hypot(px - ax, py - ay)
    t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / span))
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))


def build_paving(control: DistrictControl) -> dict:
    """    The ground surfaces a walker stands on, from the city's own planimetric survey.

    Until now these were derived: each street centreline was widened by a typical half-width for its
    class, with a strip either side for a pavement. That reads as a street from a distance and falls
    apart up close — kerb lines in the wrong place, junctions as a pile of overlapping quads, and no
    way to tell a plaza from a park from a carriageway.

    These are surveyed polygons instead (`DSRC-010`), which is what DOQ-006 asked for: pavement,
    carriageway, plaza, park and boardwalk each as their own traced shape. Kerbs come from the
    surveyed kerb *lines* in the same database and are extruded into a face with real height, which
    is the single strongest cue for reading a street at eye level.

    If the planimetric layers have not been ingested this falls back to the old centreline widening
    and says so, so a fresh clone still builds a street rather than a flat plane.
    """
    ring_enu = [control.geodetic_to_enu(lon, lat)[:2] for lon, lat in control.boundary_ring]
    surfaces: list[dict] = []
    counts: dict[str, int] = {}

    for label, kind in (
        ("parks", "park"),
        ("plazas", "plaza"),
        ("roadbed", "roadway"),
        ("sidewalks", "sidewalk"),
        ("boardwalk", "boardwalk"),
    ):
        path = DATA / "streetscape" / f"{label}.raw.json"
        if not path.exists():
            continue
        for record in load(path):
            for polygon in _rings_of(record.get("the_geom")):
                enu = _clip_ring(control, polygon, ring_enu)
                if len(enu) < 3:
                    continue
                # The survey traces kerbs to the centimetre. At walking distance a 0.25 m tolerance
                # is invisible and cuts the payload by roughly two thirds, which matters more.
                enu = _simplify(enu, 0.25)
                if len(enu) < 3:
                    continue
                surfaces.append({
                    "kind": kind,
                    "name": record.get("park_name") or None,
                    "ring": [[round(x, 2), round(y, 2)] for x, y in enu],
                })
                counts[kind] = counts.get(kind, 0) + 1

    if not surfaces:
        print("    no planimetric surfaces ingested; falling back to widened centrelines")
        return _build_paving_from_centrelines(control)

    kerbs = _build_kerbs(control, ring_enu)
    materials = _attach_surfaces(control, surfaces)
    print(f"    surfaces {counts}; {len(kerbs)} kerb segments")
    if materials:
        print(f"    materials from OSM: {dict(sorted(materials.items(), key=lambda kv: -kv[1]))}")

    return {
        "contract_version": CONTRACT_VERSION,
        "module_id": MODULE_ID,
        "frame_id": FRAME_ID,
        "source_refs": ["DSRC-010"],
        "confidence": "A",
        "attribution": "Planimetric surfaces: NYC Open Data (OTI)",
        "notes": (
            "Surveyed planimetric polygons for pavement, carriageway, plaza, park and boardwalk, "
            "with kerb faces extruded from surveyed kerb lines. Replaces the widened-centreline "
            "approximation and retires DOQ-006. Geometry is grade A; the kerb height applied to the "
            "lines is a single conventional value, DCTL-080."
        ),
        "kerb_height_m": control.value_m("DCTL-080"),
        "polygons": surfaces,
        "kerbs": kerbs,
        "provenance": provenance(control),
    }


def _simplify(points: list[tuple[float, float]], tolerance: float) -> list[tuple[float, float]]:
    """Ramer-Douglas-Peucker, iterative so a long surveyed ring cannot blow the stack."""
    if len(points) < 3:
        return points
    keep = [False] * len(points)
    keep[0] = keep[-1] = True
    stack = [(0, len(points) - 1)]
    while stack:
        first, last = stack.pop()
        if last <= first + 1:
            continue
        ax, ay = points[first]
        bx, by = points[last]
        dx, dy = bx - ax, by - ay
        span = math.hypot(dx, dy)
        worst, index = 0.0, -1
        for i in range(first + 1, last):
            px, py = points[i]
            if span < 1e-9:
                distance = math.hypot(px - ax, py - ay)
            else:
                distance = abs(dy * px - dx * py + bx * ay - by * ax) / span
            if distance > worst:
                worst, index = distance, i
        if worst > tolerance and index > 0:
            keep[index] = True
            stack.append((first, index))
            stack.append((index, last))
    return [p for p, k in zip(points, keep) if k]


def _rings_of(geom: object) -> list[list]:
    """Outer rings of a GeoJSON Polygon or MultiPolygon, holes ignored."""
    if not isinstance(geom, dict):
        return []
    kind = geom.get("type")
    if kind == "MultiPolygon":
        return [poly[0] for poly in geom.get("coordinates", []) if poly]
    if kind == "Polygon":
        coords = geom.get("coordinates", [])
        return [coords[0]] if coords else []
    return []


def _clip_ring(control: DistrictControl, ring: list, boundary: list) -> list[tuple[float, float]]:
    """Convert a lon/lat ring to scene ENU, dropping any that lies wholly outside the district.

    Kept deliberately simple: a polygon is either in or out, rather than being cut at the boundary.
    These are surveyed shapes and cutting them would invent edges that no survey recorded, which is
    worse than a pavement running a few metres past the line where the project stops caring.
    """
    enu = [control.geodetic_to_enu(float(p[0]), float(p[1]))[:2] for p in ring]
    if not enu:
        return []
    if any(point_in_ring(p, boundary) for p in enu):
        return enu
    return []


def _build_kerbs(control: DistrictControl, boundary: list) -> list[dict]:
    """Surveyed kerb lines, as polylines ready to be extruded into a face."""
    path = DATA / "streetscape" / "curbs.raw.json"
    if not path.exists():
        return []
    out: list[dict] = []
    for record in load(path):
        geom = record.get("the_geom") or {}
        lines = (geom.get("coordinates", []) if geom.get("type") == "MultiLineString"
                 else [geom.get("coordinates", [])] if geom.get("type") == "LineString" else [])
        for line in lines:
            enu = [control.geodetic_to_enu(float(p[0]), float(p[1]))[:2] for p in line]
            enu = [p for p in enu if point_in_ring(p, boundary)]
            if len(enu) < 2:
                continue
            enu = _simplify(enu, 0.25)
            if len(enu) < 2:
                continue
            out.append({"line": [[round(x, 2), round(y, 2)] for x, y in enu]})
    return out


def _build_paving_from_centrelines(control: DistrictControl) -> dict:
    """
    Fallback: turn street centrelines into paved surface quads.

    Widths are typical values by street class, so the geometry is derived rather than surveyed.
    Graded C and tracked as DOQ-006. Used only when the planimetric layers are absent.
    """
    ways = load(DATA / "streets" / "osm-ways.raw.json")
    ring = control.boundary_ring

    surfaces: list[dict] = []
    for way in ways:
        tags = way.get("tags") or {}
        highway = tags.get("highway")
        profile = ROAD_PROFILE.get(highway)
        if not profile:
            continue
        half_road, half_walk = profile
        kind = SURFACE_KIND.get(highway, "roadway")

        geometry = way.get("geometry") or []
        points: list[tuple[float, float]] = []
        for point in geometry:
            lon, lat = float(point["lon"]), float(point["lat"])
            if not point_in_ring((lon, lat), ring):
                if len(points) >= 2:
                    _emit_strip(surfaces, points, half_road, kind, tags)
                    if half_walk > 0:
                        _emit_sidewalks(surfaces, points, half_road, half_walk)
                points = []
                continue
            x, y, _ = control.geodetic_to_enu(lon, lat)
            points.append((x, y))

        if len(points) >= 2:
            _emit_strip(surfaces, points, half_road, kind, tags)
            if half_walk > 0:
                _emit_sidewalks(surfaces, points, half_road, half_walk)

    print(f"    {len(surfaces)} paving surfaces")
    return {
        "contract_version": CONTRACT_VERSION,
        "module_id": MODULE_ID,
        "frame_id": FRAME_ID,
        "source_refs": ["DSRC-007"],
        "attribution": "© OpenStreetMap contributors, ODbL",
        "confidence": "C",
        "open_questions": ["DOQ-006"],
        "notes": (
            "Paved surfaces derived by widening OSM centrelines with typical half-widths by street "
            "class. Centreline geometry is grade B; the widths are conventional values, so the "
            "surfaces are graded C. Replace with NYC planimetric sidewalk polygons to promote."
        ),
        "surfaces": surfaces,
        "provenance": provenance(control),
    }


def _emit_strip(
    out: list[dict],
    points: list[tuple[float, float]],
    half_width: float,
    kind: str,
    tags: dict,
) -> None:
    quads: list[list[float]] = []
    for i in range(len(points) - 1):
        ax, ay = points[i]
        bx, by = points[i + 1]
        dx, dy = bx - ax, by - ay
        length = math.hypot(dx, dy)
        if length < 0.4:
            continue
        nx, ny = -dy / length * half_width, dx / length * half_width
        quads.append(
            [
                round(ax + nx, 2), round(ay + ny, 2),
                round(bx + nx, 2), round(by + ny, 2),
                round(bx - nx, 2), round(by - ny, 2),
                round(ax - nx, 2), round(ay - ny, 2),
            ]
        )
    if quads:
        out.append({"kind": kind, "name": tags.get("name"), "quads": quads})


def _emit_sidewalks(
    out: list[dict],
    points: list[tuple[float, float]],
    half_road: float,
    half_walk: float,
) -> None:
    for side in (1, -1):
        quads: list[list[float]] = []
        inner = half_road * side
        outer = (half_road + half_walk) * side
        for i in range(len(points) - 1):
            ax, ay = points[i]
            bx, by = points[i + 1]
            dx, dy = bx - ax, by - ay
            length = math.hypot(dx, dy)
            if length < 0.4:
                continue
            ux, uy = -dy / length, dx / length
            quads.append(
                [
                    round(ax + ux * inner, 2), round(ay + uy * inner, 2),
                    round(bx + ux * inner, 2), round(by + uy * inner, 2),
                    round(bx + ux * outer, 2), round(by + uy * outer, 2),
                    round(ax + ux * outer, 2), round(ay + uy * outer, 2),
                ]
            )
        if quads:
            out.append({"kind": "sidewalk", "name": None, "quads": quads})


# ----------------------------------------------------------------- facades


# Facade appearance by PLUTO building class prefix. DUMBO is a converted warehouse district, so the
# palette is deliberately narrow: brick, painted brick, concrete, glass.
CLASS_STYLE = {
    "F": ("warehouse", "#8a5c46", 0.10),
    "E": ("warehouse", "#8f6249", 0.10),
    "L": ("loft", "#94664c", 0.16),
    "R": ("residential", "#9c7a63", 0.30),
    "D": ("residential", "#8e6f5c", 0.28),
    "C": ("residential", "#a07e66", 0.26),
    "O": ("office", "#7f8a92", 0.42),
    "K": ("retail", "#8d7663", 0.34),
    "G": ("garage", "#79736c", 0.06),
    "W": ("institutional", "#8b8478", 0.20),
    "Y": ("utility", "#6f6a64", 0.04),
    "Z": ("misc", "#7d766e", 0.10),
}
DEFAULT_STYLE = ("other", "#87705d", 0.20)


def build_facades(control: DistrictControl) -> dict:
    """
    Per-building facade appearance, derived from attributes this module already holds.

    No textures and no photogrammetry: this assigns each building a material family, a base colour
    and a window-band ratio from its PLUTO class and construction year. The viewer draws window
    bands procedurally from those numbers.

    The point is that the variation is *meaningful* rather than random. A 1900s warehouse gets deep
    brick with sparse punched openings; a converted loft gets larger bays; a modern office gets a
    high glazing ratio. Someone walking Water Street sees the district's actual character, and every
    facade can still be traced back to a registered attribute.
    """
    pluto_rows = load(DATA / "pluto" / "pluto.raw.json")
    footprints = load(DATA / "footprints" / "footprints.raw.json")

    lots = {}
    for row in pluto_rows:
        bbl = str(row.get("bbl") or "").split(".")[0]
        if bbl:
            lots[bbl] = row

    facades: dict[str, dict] = {}
    histogram: dict[str, int] = {}

    for record in footprints:
        bin_id = str(record.get("bin") or "").strip() or f"doitt_{record.get('doitt_id')}"
        local_id = f"bldg_{bin_id}"
        bbl = str(record.get("mappluto_bbl") or record.get("base_bbl") or "").split(".")[0]
        lot = lots.get(bbl)

        bldg_class = ((lot or {}).get("bldgclass") or "").strip().upper()
        family, base_color, glazing = CLASS_STYLE.get(bldg_class[:1], DEFAULT_STYLE)

        year = _as_float((lot or {}).get("yearbuilt")) or _as_float(record.get("construction_year"))
        era = "unknown"
        if year:
            if year < 1900:
                era, glazing = "pre1900", glazing * 0.75
            elif year < 1940:
                era, glazing = "prewar", glazing * 0.9
            elif year < 1980:
                era = "midcentury"
            else:
                era, glazing = "modern", min(0.62, glazing * 1.5)

        floors = _as_float((lot or {}).get("numfloors"))

        histogram[family] = histogram.get(family, 0) + 1
        facades[local_id] = {
            "family": family,
            "color": base_color,
            "glazing": round(min(0.7, max(0.02, glazing)), 3),
            "era": era,
            "basis": "inferred",
            **({"floors": int(floors)} if floors else {}),
        }

    observed = apply_observed_appearance(facades)
    designated = apply_designations(facades)

    print(f"    {len(facades)} facades; families {dict(sorted(histogram.items(), key=lambda kv: -kv[1])[:5])}")
    if designated:
        print(f"    {designated} facades carry the city's designated style and material")
    if observed:
        print(f"    {observed} facades observed from photographs and locked against the procedural pass")

    return {
        "contract_version": CONTRACT_VERSION,
        "module_id": MODULE_ID,
        "frame_id": FRAME_ID,
        "source_refs": (
            ["DSRC-001", "DSRC-002"]
            + (["DSRC-015"] if observed else [])
            + (["DSRC-018"] if designated else [])
        ),
        "confidence": "C",
        "observed_count": observed,
        "designated_count": designated,
        "open_questions": ["DOQ-007"],
        "notes": (
            "Facade appearance derived from PLUTO building class and construction year. The inputs "
            "are grade A; the mapping from class to material and glazing ratio is a convention, so "
            "the appearance is graded C. It describes the KIND of building, not its actual facade. "
            "Buildings in the city's landmark register take their material, style and bay rhythm "
            "from the designation report instead, which is an authoritative statement about that "
            "specific building rather than an inference from its tax class. Buildings carrying "
            "photographic evidence are marked basis=observed, take their colour from that "
            "photograph, and are never reassigned by the procedural pass."
        ),
        "styles": facades,
        "provenance": provenance(control),
    }


# What the city's designation reports say a building is made of, as a rendered colour. Brownstone and
# brick are not interchangeable and the register distinguishes them, so neither should this.
DESIGNATED_MATERIAL = {
    "Brick": ("#7d5544", "brick"),
    "Philadelphia Brick": ("#8a6350", "brick"),
    "Brownstone": ("#6b4a35", "brownstone"),
    "Stone": ("#8d8577", "stone"),
    "Limestone": ("#a89e8b", "stone"),
    "Marble": ("#b4b0a6", "stone"),
    "Granite": ("#8a8a86", "stone"),
    "Stucco": ("#a09384", "stucco"),
    "Wood Frame": ("#8b7a63", "timber"),
    "Reinforced Concrete": ("#918d85", "concrete"),
    "Concrete Block": ("#918d85", "concrete"),
    "Cement Block": ("#918d85", "concrete"),
    "Cast Iron": ("#5f6468", "iron"),
    "Terra Cotta": ("#9d6a4f", "terracotta"),
}

# Bay pitch in metres, by what the register says the building IS. This is the number that stops a
# facade reading as a box: a row house has a narrow two-bay front and a factory has wide industrial
# openings, and until now both were drawn with the same anonymous horizontal banding.
DESIGNATED_BAY_M = {
    "Row House": 2.6,
    "Carriage House": 3.0,
    "Apartment House": 3.2,
    "Flats Building": 3.2,
    "Tenement": 3.0,
    "Factory": 4.6,
    "Warehouse": 4.6,
    "Store Building": 4.0,
    "Office Building": 3.8,
    "Church": 5.0,
    "School": 4.0,
    "Garage": 4.4,
    "Stable": 3.6,
}

# Glazing by architectural style. A daylight factory is mostly window; a Federal row house is mostly
# wall with punched openings. The register names the style, so this is a lookup, not a guess.
DESIGNATED_STYLE_GLAZING = {
    "Daylight Factory": 0.46,
    "Industrial Neoclassical": 0.30,
    "American Round Arch": 0.24,
    "Romanesque Revival": 0.22,
    "Greek Revival": 0.16,
    "Federal": 0.14,
    "Anglo-Italianate": 0.18,
    "Italianate": 0.18,
    "Gothic Revival": 0.16,
    "Renaissance Revival": 0.20,
    "Neo-Grec": 0.18,
    "Queen Anne": 0.20,
    "Vernacular": 0.16,
    "Eclectic": 0.20,
}


def apply_designations(facades: dict[str, dict]) -> int:
    """Take material, style and bay rhythm from the city's landmark register where it has an opinion.

    PLUTO says what a building is *for*; the designation report says what it *is*. For 1,386
    buildings in this district the city has published the primary material, the architectural style,
    the building type and the date, per BIN, and that is strictly better evidence than a mapping from
    tax class — it is a statement about that building rather than about buildings like it.

    A photographed facade still wins. A colour measured from a photograph of the wall as it stands
    today beats a designation report's material name, because the report describes the fabric and the
    photograph describes the surface. Where both exist the photograph is left alone and only the bay
    rhythm, which no photograph currently supplies, is taken from the register.
    """
    path = DATA / "boundaries" / "designations.raw.json"
    if not path.exists():
        return 0

    applied = 0
    for record in load(path):
        bin_id = str(record.get("bin") or "").split(".")[0]
        entry = facades.get(f"bldg_{bin_id}")
        if not entry:
            continue

        build_type = (record.get("build_type") or "").strip()
        style_name = (record.get("style_prim") or "").strip()
        material = (record.get("mat_prim") or "").strip()

        bay = DESIGNATED_BAY_M.get(build_type)
        if bay:
            entry["bay_m"] = bay
        if style_name and style_name not in {"Not determined", "None"}:
            entry["designated_style"] = style_name
            glazing = DESIGNATED_STYLE_GLAZING.get(style_name)
            if glazing and entry.get("basis") != "observed":
                entry["glazing"] = glazing
        if build_type and build_type not in {"Not determined", "Vacant Lot"}:
            entry["designated_type"] = build_type

        mapped = DESIGNATED_MATERIAL.get(material)
        if mapped and entry.get("basis") != "observed":
            entry["color"], entry["material"] = mapped
            entry["basis"] = "designated"
        elif mapped:
            # Photograph wins on colour; the register still names the fabric.
            entry["material"] = mapped[1]

        if any(k in entry for k in ("bay_m", "designated_style", "designated_type")):
            entry["designation"] = record.get("hist_dist") or record.get("lm_new") or "Individual Landmark"
            applied += 1

    return applied


def apply_observed_appearance(facades: dict[str, dict]) -> int:
    """Override inferred appearance wherever a photograph actually shows the building.

    This is the point of the campaign. The procedural pass is a reasonable guess from a building's
    class and age, and it is *supposed* to be overwritten as soon as anything real turns up. A
    building that has been photographed must never be silently re-guessed on the next build, so its
    entry is marked `basis: "observed"` and carries the credit line for the image it came from.
    Anything reading these styles can treat `observed` as a lock.

    Absent a corpus this is a no-op and the district looks exactly as it did before, which is what
    lets the campaign be run incrementally rather than all at once.
    """
    survey_path = OUT / "photo-survey.json"
    palette_path = OUT / "photo-palette.json"
    if not survey_path.exists():
        return 0
    survey = json.loads(survey_path.read_text(encoding="utf-8"))
    palette = (json.loads(palette_path.read_text(encoding="utf-8"))
               if palette_path.exists() else {"surfaces": {}})

    brick = palette.get("surfaces", {}).get("brick") or {}
    # Colour measured from each individual photograph, so a building can wear the tone read off a
    # picture of itself rather than a district average.
    measured_by_observation = {s["observation_id"]: s["hex"]
                               for s in brick.get("samples", []) if s.get("observation_id")}
    fallback_tones = [s["hex"] for s in brick.get("samples", [])] or (
        [brick["mean_hex"]] if brick.get("mean_hex") else []
    )

    # Best evidence per building: prefer the closest, then the highest grade.
    best: dict[str, tuple[float, str, dict]] = {}
    grade_rank = {"A": 0, "B": 1, "C": 2, "D": 3}
    for observation in survey.get("observations", []):
        grade = (observation.get("review") or {}).get("grants_confidence", "C")
        for subject in observation.get("observes", []):
            if not any(a in ("facade_material", "facade_colour", "window_pattern")
                       for a in subject.get("aspect", [])):
                continue
            local_id = subject["asset_id"].rsplit(":", 1)[-1]
            key = (subject.get("distance_m", 999.0), grade)
            current = best.get(local_id)
            if current is None or (key[0], grade_rank.get(key[1], 3)) < (current[0], grade_rank.get(current[1], 3)):
                best[local_id] = (key[0], grade, observation)

    changed = 0
    from_own_photo = 0
    for local_id, (distance, grade, observation) in best.items():
        style = facades.get(local_id)
        if style is None:
            continue
        style["basis"] = "observed"
        style["observed_grade"] = grade
        style["observation_id"] = observation["observation_id"]
        style["attribution_text"] = observation.get("attribution_text")
        style["observed_distance_m"] = distance

        own = measured_by_observation.get(observation["observation_id"])
        if own:
            style["color"] = own
            style["colour_source"] = "measured_from_this_photograph"
            from_own_photo += 1
        elif fallback_tones:
            # Deterministic so the same building keeps the same tone between builds, and so the
            # district does not reshuffle its colours every time the corpus grows.
            index = int(hashlib.sha256(local_id.encode()).hexdigest(), 16) % len(fallback_tones)
            style["color"] = fallback_tones[index]
            style["colour_source"] = "measured_from_district_corpus"
        changed += 1
    if from_own_photo:
        print(f"    {from_own_photo} of those took their colour from a photograph of that building")
    return changed


# ---------------------------------------------------------------------- main


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--props", action="store_true")
    parser.add_argument("--paving", action="store_true")
    parser.add_argument("--facades", action="store_true")
    args = parser.parse_args()
    run_all = not (args.props or args.paving or args.facades)

    control = DistrictControl()
    print(f"district control : {control.path.name} @ {control.sha256[:12]}")

    if run_all or args.props:
        print("[props: street trees]")
        index = load(DATA / "tiles" / "tile-index.json")
        size = write(OUT / "props.json", build_props(control, index))
        print(f"    wrote props.json ({size / 1024:.0f} KB)")

    if run_all or args.paving:
        print("[paving]")
        size = write(OUT / "paving.json", build_paving(control))
        print(f"    wrote paving.json ({size / 1024:.0f} KB)")

    if run_all or args.facades:
        print("[facades]")
        size = write(OUT / "facades.json", build_facades(control))
        print(f"    wrote facades.json ({size / 1024:.0f} KB)")

    print("\nscene dressing complete")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
