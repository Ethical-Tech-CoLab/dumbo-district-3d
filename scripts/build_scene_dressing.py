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
    Turn street centrelines into paved surface quads.

    A district viewer that draws streets as one-pixel lines on an untextured plane reads as a
    diagram. Widening each centreline segment into a quad, and adding a sidewalk strip either side
    of vehicular streets, is a cheap change that turns the diagram into a place — kerbs give the
    eye the edges it needs to judge distance while walking.

    The geometry is derived, not surveyed: widths are typical values by street class. Graded C and
    tracked as DOQ-006.
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
            **({"floors": int(floors)} if floors else {}),
        }

    print(f"    {len(facades)} facades; families {dict(sorted(histogram.items(), key=lambda kv: -kv[1])[:5])}")

    return {
        "contract_version": CONTRACT_VERSION,
        "module_id": MODULE_ID,
        "frame_id": FRAME_ID,
        "source_refs": ["DSRC-001", "DSRC-002"],
        "confidence": "C",
        "open_questions": ["DOQ-007"],
        "notes": (
            "Facade appearance derived from PLUTO building class and construction year. The inputs "
            "are grade A; the mapping from class to material and glazing ratio is a convention, so "
            "the appearance is graded C. It describes the KIND of building, not its actual facade. "
            "Replace with street-level imagery or photogrammetry to promote."
        ),
        "styles": facades,
        "provenance": provenance(control),
    }


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
