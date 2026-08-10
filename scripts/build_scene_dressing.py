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

    return {
        "contract_version": CONTRACT_VERSION,
        "module_id": MODULE_ID,
        "frame_id": FRAME_ID,
        "prototypes": prototypes,
        "instances": instances,
        "provenance": provenance(control),
    }


def _as_float(value: object) -> float | None:
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


def build_paving(control: DistrictControl) -> dict:
    """
    The ground surfaces a walker stands on, from the city's own planimetric survey.

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
    print(f"    surfaces {counts}; {len(kerbs)} kerb segments")

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

    print(f"    {len(facades)} facades; families {dict(sorted(histogram.items(), key=lambda kv: -kv[1])[:5])}")
    if observed:
        print(f"    {observed} facades observed from photographs and locked against the procedural pass")

    return {
        "contract_version": CONTRACT_VERSION,
        "module_id": MODULE_ID,
        "frame_id": FRAME_ID,
        "source_refs": ["DSRC-001", "DSRC-002"] + (["DSRC-015"] if observed else []),
        "confidence": "C",
        "observed_count": observed,
        "open_questions": ["DOQ-007"],
        "notes": (
            "Facade appearance derived from PLUTO building class and construction year. The inputs "
            "are grade A; the mapping from class to material and glazing ratio is a convention, so "
            "the appearance is graded C. It describes the KIND of building, not its actual facade. "
            "Buildings carrying photographic evidence are marked basis=observed, take their colour "
            "from that photograph, and are never reassigned by the procedural pass."
        ),
        "styles": facades,
        "provenance": provenance(control),
    }


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
