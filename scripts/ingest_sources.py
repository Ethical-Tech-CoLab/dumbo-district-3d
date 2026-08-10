"""
Fetch every external source the DUMBO district model depends on.

Nothing here invents geometry. Each fetcher writes a raw payload plus a sidecar manifest recording the
query, the response size and a hash, so DUMBO-SOURCE-REGISTER.md can be audited against what was actually
downloaded.

Sources and their register IDs are documented in DUMBO-SOURCE-REGISTER.md:
  DSRC-001  NYC Building Footprints        (Socrata 5zhs-2jue)
  DSRC-002  NYC MapPLUTO                   (Socrata 64uk-42ks)
  DSRC-004  NYC Neighborhood Tabulation Areas (Socrata 9nt8-h7nd), context only
  DSRC-007  OpenStreetMap via Overpass     (streets, footways, landmarks)
  DSRC-013  USGS 3DEP 1 m bare-earth DEM   (ImageServer getSamples)
  DSRC-014  NYC Planimetric Hydrography    (Socrata pjs3-c3z5)

Usage:
    python scripts/ingest_sources.py --all
    python scripts/ingest_sources.py --footprints --pluto
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from district_control import AGENT_ID, DistrictControl, point_in_ring

REPO_ROOT = Path(__file__).resolve().parent.parent
DATA = REPO_ROOT / "data"

SOCRATA = "https://data.cityofnewyork.us/resource"
OVERPASS_ENDPOINTS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.osm.ch/api/interpreter",
]
DEP_SERVICE = (
    "https://elevation.nationalmap.gov/arcgis/rest/services/3DEPElevation/ImageServer"
)
USER_AGENT = f"dumbo-district-3d ({AGENT_ID})"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _http(url: str, *, data: bytes | None = None, tries: int = 4, timeout: int = 180) -> bytes:
    ctx = ssl.create_default_context()
    last: Exception | None = None
    for attempt in range(tries):
        request = urllib.request.Request(url, data=data, headers={"User-Agent": USER_AGENT})
        if data is not None:
            request.add_header("Content-Type", "application/x-www-form-urlencoded")
        try:
            with urllib.request.urlopen(request, timeout=timeout, context=ctx) as response:
                return response.read()
        except (urllib.error.URLError, TimeoutError, ssl.SSLError) as exc:
            last = exc
            wait = 3 * (attempt + 1)
            print(f"    retry {attempt + 1}/{tries} in {wait}s ({exc})", file=sys.stderr)
            time.sleep(wait)
    raise RuntimeError(f"failed after {tries} attempts: {url}") from last


def _write(path: Path, payload: object, *, query: str, source_id: str, note: str = "") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, indent=1)
    path.write_text(text, encoding="utf-8")
    sidecar = path.with_suffix(path.suffix + ".source.json")
    sidecar.write_text(
        json.dumps(
            {
                "source_id": source_id,
                "query": query,
                "fetched_at": _now(),
                "fetched_by": AGENT_ID,
                "bytes": len(text),
                "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
                "record_count": len(payload) if isinstance(payload, list) else None,
                "note": note,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    count = len(payload) if isinstance(payload, list) else "n/a"
    print(f"    wrote {path.relative_to(REPO_ROOT)}  records={count}  bytes={len(text)}")


def _socrata_paged(resource: str, params: dict[str, str], *, page: int = 5000) -> list[dict]:
    out: list[dict] = []
    offset = 0
    while True:
        query = dict(params)
        query["$limit"] = str(page)
        query["$offset"] = str(offset)
        url = f"{SOCRATA}/{resource}.json?" + urllib.parse.urlencode(query, safe="(),'$*")
        batch = json.loads(_http(url))
        out.extend(batch)
        if len(batch) < page:
            return out
        offset += page
        print(f"    ... {len(out)} records")


def _overpass(query: str) -> dict:
    body = urllib.parse.urlencode({"data": query}).encode("utf-8")
    last: Exception | None = None
    for endpoint in OVERPASS_ENDPOINTS:
        print(f"    endpoint {endpoint}")
        try:
            return json.loads(_http(endpoint, data=body, tries=3, timeout=300))
        except Exception as exc:  # noqa: BLE001 - try the next mirror whatever went wrong
            last = exc
            print(f"    endpoint failed: {exc}", file=sys.stderr)
    raise RuntimeError("every Overpass endpoint failed") from last


# --------------------------------------------------------------------------- fetchers


def fetch_footprints(control: DistrictControl) -> None:
    """DSRC-001. Building footprints, clipped to the district boundary polygon."""
    print("[DSRC-001] NYC Building Footprints")
    west, south, east, north = control.bbox
    where = f"within_box(the_geom,{north},{west},{south},{east})"
    records = _socrata_paged("5zhs-2jue", {"$where": where})
    print(f"    {len(records)} records in the query envelope")

    ring = control.boundary_ring
    kept = []
    for record in records:
        geom = record.get("the_geom")
        if not geom or geom.get("type") not in ("Polygon", "MultiPolygon"):
            continue
        polygons = geom["coordinates"] if geom["type"] == "MultiPolygon" else [geom["coordinates"]]
        centroid = _centroid(polygons)
        if centroid is None or not point_in_ring(centroid, ring):
            continue
        record["_centroid"] = list(centroid)
        kept.append(record)

    print(f"    {len(kept)} records inside the district boundary")
    _write(
        DATA / "footprints" / "footprints.raw.json",
        kept,
        query=where,
        source_id="DSRC-001",
        note="Clipped to the DUMBO-GEOSPATIAL-CONTROL.md boundary ring by footprint centroid.",
    )


def _centroid(polygons: list) -> tuple[float, float] | None:
    """
    Area-weighted centroid of a set of polygon rings in lon/lat.

    Uses the standard shoelace moments, but about a local origin rather than about (0, 0).

    That detail is the whole function. A building footprint in New York has coordinates near
    (-73.99, 40.70), so each shoelace cross product `x1*y2 - x2*y1` is a difference of two numbers
    close to 3,011 whose true value is around 1e-9. Double precision carries about 16 significant
    digits, so subtracting them discards roughly twelve of them, and the surviving noise is then
    divided by an area that is itself the sum of those same cancelled terms. The result is not
    slightly wrong, it is unrelated: measured against the raw dataset, 63% of DUMBO's footprints
    produced a centroid outside their own bounding box, one of them by 595 metres.

    Subtracting the first vertex first makes every coordinate a small offset, the cancellation
    disappears, and the moments are computed at full precision. Interior rings are ignored: a
    building's holes do not move its centroid enough to matter for a point-in-district test.
    """
    origin: tuple[float, float] | None = None
    area2 = 0.0
    mx = my = 0.0
    for polygon in polygons:
        if not polygon:
            continue
        ring = polygon[0]
        if len(ring) < 4:
            continue
        if origin is None:
            origin = (float(ring[0][0]), float(ring[0][1]))
        for i in range(len(ring) - 1):
            x1 = float(ring[i][0]) - origin[0]
            y1 = float(ring[i][1]) - origin[1]
            x2 = float(ring[i + 1][0]) - origin[0]
            y2 = float(ring[i + 1][1]) - origin[1]
            cross = x1 * y2 - x2 * y1
            area2 += cross
            mx += (x1 + x2) * cross
            my += (y1 + y2) * cross
    if origin is None or area2 == 0.0:
        return None
    return (mx / (3.0 * area2) + origin[0], my / (3.0 * area2) + origin[1])


def fetch_pluto(control: DistrictControl) -> None:
    """DSRC-002. Tax-lot attributes, used for land use, floors, owner and year built."""
    print("[DSRC-002] NYC MapPLUTO")
    west, south, east, north = control.bbox
    where = (
        f"latitude between {south} and {north} "
        f"and longitude between {west} and {east} "
        f"and borough='BK'"
    )
    records = _socrata_paged(
        "64uk-42ks",
        {
            "$where": where,
            "$select": ",".join(
                [
                    "bbl", "borough", "block", "lot", "address", "ownername",
                    "bldgclass", "landuse", "numfloors", "numbldgs", "unitsres",
                    "unitstotal", "yearbuilt", "yearalter1", "lotarea", "bldgarea",
                    "zonedist1", "builtfar", "latitude", "longitude", "cd", "zipcode",
                ]
            ),
        },
    )
    print(f"    {len(records)} lots")
    _write(
        DATA / "pluto" / "pluto.raw.json",
        records,
        query=where,
        source_id="DSRC-002",
        note="Brooklyn lots within the district query envelope. Joined to footprints on BBL.",
    )


def fetch_streets(control: DistrictControl) -> None:
    """DSRC-007. OSM ways used to build the pedestrian walk network."""
    print("[DSRC-007] OpenStreetMap street and footway network")
    west, south, east, north = control.bbox
    bbox = f"{south},{west},{north},{east}"
    query = (
        "[out:json][timeout:180];"
        "("
        f'way["highway"]["highway"!~"^(motorway|motorway_link|trunk_link|construction|proposed)$"]({bbox});'
        f'way["footway"]({bbox});'
        ");"
        "out geom tags;"
    )
    result = _overpass(query)
    ways = [e for e in result.get("elements", []) if e.get("type") == "way" and e.get("geometry")]
    print(f"    {len(ways)} ways")
    _write(
        DATA / "streets" / "osm-ways.raw.json",
        ways,
        query=query,
        source_id="DSRC-007",
        note="OpenStreetMap contributors, ODbL. Attribution is mandatory wherever this is rendered.",
    )


def fetch_street_furniture(control: DistrictControl) -> None:
    """DSRC-016. The things a walker actually brushes past.

    Railings above all. The Brooklyn Bridge Park waterfront is fenced along nearly its whole
    length, and a promenade rendered without its railing does not read as a promenade — it reads
    as a lawn that stops at the water. Lamps, benches, bollards and bike racks matter for the
    same reason: they are what makes a street look occupied rather than modelled.

    Two geometries, deliberately kept apart. Railings and fences are *lines* and have to be
    extruded along their run; everything else is a *point* and can be instanced. Asking Overpass
    for both in one query and sorting the result by type is cheaper than two round trips.
    """
    print("[DSRC-016] OpenStreetMap street furniture")
    west, south, east, north = control.bbox
    bbox = f"{south},{west},{north},{east}"
    line_selectors = [
        'way["barrier"~"^(railing|handrail|fence|guard_rail|wall)$"]',
    ]
    point_selectors = [
        'node["highway"="street_lamp"]',
        'node["amenity"="bench"]',
        'node["barrier"="bollard"]',
        'node["amenity"="bicycle_parking"]',
        'node["amenity"="waste_basket"]',
        'node["amenity"="drinking_fountain"]',
        'node["emergency"="fire_hydrant"]',
        'node["highway"="traffic_signals"]',
        'node["man_made"="flagpole"]',
        'node["amenity"="fountain"]',
    ]
    query = (
        "[out:json][timeout:180];("
        + "".join(f"{selector}({bbox});" for selector in line_selectors)
        + "".join(f"{selector}({bbox});" for selector in point_selectors)
        + ");out geom tags;"
    )
    result = _overpass(query)

    lines: list[dict] = []
    points: list[dict] = []
    for element in result.get("elements", []):
        tags = element.get("tags") or {}
        if element.get("type") == "way" and element.get("geometry"):
            lines.append(
                {
                    "osm_id": element["id"],
                    "barrier": tags.get("barrier"),
                    "geometry": [[p["lon"], p["lat"]] for p in element["geometry"]],
                    "tags": tags,
                }
            )
        elif element.get("type") == "node" and element.get("lat") is not None:
            points.append(
                {
                    "osm_id": element["id"],
                    "lon": float(element["lon"]),
                    "lat": float(element["lat"]),
                    "tags": tags,
                }
            )

    kinds: dict[str, int] = {}
    for line in lines:
        kinds[f"barrier={line['barrier']}"] = kinds.get(f"barrier={line['barrier']}", 0) + 1
    for point in points:
        tags = point["tags"]
        key = next(
            (f"{k}={tags[k]}" for k in ("highway", "amenity", "barrier", "emergency", "man_made") if k in tags),
            "other",
        )
        kinds[key] = kinds.get(key, 0) + 1
    for key in sorted(kinds, key=lambda k: -kinds[k]):
        print(f"    {kinds[key]:5d}  {key}")

    _write(
        DATA / "streets" / "osm-street-furniture.raw.json",
        {"lines": lines, "points": points},
        query=query,
        source_id="DSRC-016",
        note="OpenStreetMap contributors, ODbL. Attribution is mandatory wherever this is rendered.",
    )


def fetch_storefronts(control: DistrictControl) -> None:
    """DSRC-017. Ground-floor businesses, used to place awnings and shopfronts.

    A DUMBO warehouse at street level is a row of shopfronts under a brick wall, and without them
    every building meets the pavement as a blank face. PLUTO knows a lot about these lots but not
    which ones have a shop at the bottom: its building class describes the whole building, so a cafe
    on the ground floor of a residential block is invisible to it. OSM maps the business itself,
    which is exactly the thing that is visible from the pavement.
    """
    print("[DSRC-017] OpenStreetMap ground-floor businesses")
    west, south, east, north = control.bbox
    bbox = f"{south},{west},{north},{east}"
    amenities = "^(cafe|restaurant|bar|pub|fast_food|bank|pharmacy|ice_cream|bakery|cinema|theatre)$"
    selectors = [
        'node["shop"]', 'way["shop"]',
        f'node["amenity"~"{amenities}"]', f'way["amenity"~"{amenities}"]',
    ]
    query = (
        "[out:json][timeout:180];("
        + "".join(f"{selector}({bbox});" for selector in selectors)
        + ");out center tags;"
    )
    result = _overpass(query)

    shops = []
    for element in result.get("elements", []):
        tags = element.get("tags") or {}
        lat = element.get("lat") or (element.get("center") or {}).get("lat")
        lon = element.get("lon") or (element.get("center") or {}).get("lon")
        if lat is None or lon is None:
            continue
        shops.append(
            {
                "osm_type": element["type"],
                "osm_id": element["id"],
                "name": tags.get("name"),
                "shop": tags.get("shop"),
                "amenity": tags.get("amenity"),
                "lon": float(lon),
                "lat": float(lat),
                "tags": tags,
            }
        )
    print(f"    {len(shops)} businesses, {sum(1 for s in shops if s['name'])} named")
    _write(
        DATA / "streets" / "osm-storefronts.raw.json",
        shops,
        query=query,
        source_id="DSRC-017",
        note="OpenStreetMap contributors, ODbL. Attribution is mandatory wherever this is rendered.",
    )


def fetch_landmarks(control: DistrictControl) -> None:
    """DSRC-007. Named places used as tour stops and map labels."""
    print("[DSRC-007] OpenStreetMap named landmarks")
    west, south, east, north = control.bbox
    bbox = f"{south},{west},{north},{east}"
    selectors = [
        'node["tourism"]', 'way["tourism"]',
        'node["historic"]', 'way["historic"]',
        'way["leisure"="park"]', 'node["leisure"="park"]',
        'node["amenity"~"^(theatre|arts_centre|marketplace|cafe|restaurant|ferry_terminal)$"]',
        'way["amenity"~"^(theatre|arts_centre|marketplace|ferry_terminal)$"]',
        'node["man_made"="pier"]', 'way["man_made"="pier"]',
        'node["railway"="subway_entrance"]',
        'way["bridge"]["name"]',
    ]
    query = (
        "[out:json][timeout:180];("
        + "".join(f"{selector}({bbox});" for selector in selectors)
        + ");out center tags;"
    )
    result = _overpass(query)
    named = []
    for element in result.get("elements", []):
        tags = element.get("tags") or {}
        if not tags.get("name"):
            continue
        lat = element.get("lat") or (element.get("center") or {}).get("lat")
        lon = element.get("lon") or (element.get("center") or {}).get("lon")
        if lat is None or lon is None:
            continue
        named.append(
            {
                "osm_type": element["type"],
                "osm_id": element["id"],
                "name": tags["name"],
                "lon": float(lon),
                "lat": float(lat),
                "tags": tags,
            }
        )
    print(f"    {len(named)} named features")
    _write(
        DATA / "streets" / "osm-landmarks.raw.json",
        named,
        query=query,
        source_id="DSRC-007",
        note="OpenStreetMap contributors, ODbL.",
    )


def fetch_nta(control: DistrictControl) -> None:
    """DSRC-004. Context only. Explicitly NOT used as the district boundary."""
    print("[DSRC-004] NYC Neighborhood Tabulation Areas (context)")
    records = _socrata_paged(
        "9nt8-h7nd",
        {"$where": "nta2020='BK0202'"},
    )
    _write(
        DATA / "boundaries" / "nta-bk0202.raw.json",
        records,
        query="nta2020='BK0202'",
        source_id="DSRC-004",
        note=(
            "Downtown Brooklyn-DUMBO-Boerum Hill. Retained as context. The project boundary is the "
            "polygon in DUMBO-GEOSPATIAL-CONTROL.md section 2.1, which is much smaller."
        ),
    )


def fetch_trees(control: DistrictControl) -> None:
    """DSRC-009. NYC Forestry street tree points, used to place street trees in walk mode."""
    print("[DSRC-009] NYC Forestry street trees")
    west, south, east, north = control.bbox
    where = f"within_box(location,{north},{west},{south},{east})"
    records = _socrata_paged(
        "hn5i-inap",
        {
            "$where": where,
            "$select": "objectid,dbh,tpstructure,tpcondition,genusspecies,location",
        },
    )
    print(f"    {len(records)} trees in the query envelope")
    _write(
        DATA / "streetscape" / "trees.raw.json",
        records,
        query=where,
        source_id="DSRC-009",
        note=(
            "Forestry Management System street tree points. dbh is trunk diameter in inches, which "
            "drives per-tree canopy scale rather than every tree being identical."
        ),
    )


def fetch_dem(control: DistrictControl) -> None:
    """DSRC-013. USGS 3DEP 1 m bare-earth elevation, sampled over the district grid.

    This is the source that retires DOQ-003. Until now the ground was interpolated from building
    base elevations: grade A samples with a grade C surface stretched between them, which says
    nothing about the street itself, and nothing at all about open ground away from buildings.

    3DEP is sampled rather than downloaded. The national raster is far too large to hold, but the
    ImageServer will return elevations for a batch of points in one request, so we ask for exactly
    the grid the terrain mesh uses and keep only that. Bare earth is what we want: buildings are
    already removed, so this is the pavement, not the roofline.
    """
    print("[DSRC-013] USGS 3DEP 1 m bare-earth DEM")
    cell = control.value_m("DCTL-070")
    ox, oy, cols, rows = _ground_grid_extent(control, cell)
    print(f"    sampling {cols} x {rows} = {cols * rows} points at {cell:g} m spacing")

    points: list[list[float]] = []
    for row in range(rows):
        y = oy + row * cell
        for col in range(cols):
            lon, lat = control.enu_to_geodetic(ox + col * cell, y, 0.0)[:2]
            points.append([round(lon, 8), round(lat, 8)])

    values = _sample_3dep(points)
    missing = sum(1 for v in values if v is None)
    present = [v for v in values if v is not None]
    if not present:
        raise RuntimeError("3DEP returned no usable samples")
    print(f"    {len(present)} samples, {missing} no-data, "
          f"range {min(present):.2f} .. {max(present):.2f} m")

    _write(
        DATA / "terrain" / "dem.raw.json",
        {
            "service": DEP_SERVICE,
            "cell_m": cell,
            "origin_xy_m": [ox, oy],
            "cols": cols,
            "rows": rows,
            "frame_id": "nyc-harbor-enu",
            "vertical_datum": "NAVD88",
            "units": "m",
            "interpolation": "RSP_BilinearInterpolation",
            "values": values,
        },
        query=f"getSamples {cols}x{rows} @ {cell:g}m, bilinear, EPSG:4326",
        source_id="DSRC-013",
        note=(
            "Bare-earth elevations in NAVD88 metres, sampled on the district ground grid. Values are "
            "null where 3DEP has no data. Row-major from origin_xy_m, +x east, +y north."
        ),
    )


def _ground_grid_extent(control: DistrictControl, cell: float) -> tuple[float, float, int, int]:
    """The ground grid covers the tile scheme exactly, so DEM and terrain stay in lockstep."""
    ox, oy, span_x, span_y = control.tile_extent
    cols = int(math.ceil(span_x / cell)) + 1
    rows = int(math.ceil(span_y / cell)) + 1
    return ox, oy, cols, rows


def _sample_3dep(points: list[list[float]], batch: int = 1000) -> list[float | None]:
    """Sample the 3DEP ImageServer in batches.

    The service caps a single request at 1000 points and rejects long GET URLs outright, so this
    posts the geometry. Order is preserved, and a batch that comes back short is padded with nulls
    rather than silently shifting every later value into the wrong cell.
    """
    out: list[float | None] = []
    total = (len(points) + batch - 1) // batch
    for index in range(0, len(points), batch):
        chunk = points[index:index + batch]
        payload = urllib.parse.urlencode({
            "geometry": json.dumps({"points": chunk, "spatialReference": {"wkid": 4326}}),
            "geometryType": "esriGeometryMultipoint",
            "returnFirstValueOnly": "true",
            "interpolation": "RSP_BilinearInterpolation",
            "f": "json",
        }).encode()
        raw = _http(f"{DEP_SERVICE}/getSamples", data=payload)
        data = json.loads(raw)
        if "error" in data:
            raise RuntimeError(f"3DEP error: {data['error']}")
        samples = data.get("samples", [])
        values: list[float | None] = []
        for sample in samples:
            try:
                values.append(round(float(sample["value"]), 3))
            except (KeyError, TypeError, ValueError):
                values.append(None)
        if len(values) != len(chunk):
            print(f"    batch {index // batch + 1}/{total}: expected {len(chunk)}, "
                  f"got {len(values)}; padding", file=sys.stderr)
            values.extend([None] * (len(chunk) - len(values)))
        out.extend(values[:len(chunk)])
        print(f"    batch {index // batch + 1}/{total} ok")
        time.sleep(0.3)
    return out


def fetch_hydrography(control: DistrictControl) -> None:
    """DSRC-014. NYC planimetric hydrography, used to decide land from water.

    The land mask was previously the district boundary polygon, which was drawn by inspection to
    define project scope (DOQ-005) and was never meant to carry the shoreline. Using it as one meant
    the terrain either drowned the waterfront or paved the river depending on which way the apron
    erred. This is the city's own water polygon, so land and water become a sourced distinction.
    """
    print("[DSRC-014] NYC planimetric hydrography")
    west, south, east, north = _grid_bbox(control)
    poly = (f"POLYGON(({west} {south},{east} {south},{east} {north},"
            f"{west} {north},{west} {south}))")
    where = f"intersects(the_geom,'{poly}')"
    records = _socrata_paged("pjs3-c3z5", {"$where": where}, page=500)
    named = sorted({r.get("name") for r in records if r.get("name") not in (None, "unset")})
    print(f"    {len(records)} water polygons; named: {', '.join(named) or 'none'}")
    _write(
        DATA / "terrain" / "hydrography.raw.json",
        records,
        query=where,
        source_id="DSRC-014",
        note=(
            "Water body polygons covering the ground grid extent, including the East River. Used as "
            "the land/water mask for the terrain mesh instead of the district boundary."
        ),
    )


def _grid_bbox(control: DistrictControl) -> tuple[float, float, float, float]:
    """Geodetic envelope of the ground grid, with a margin so the mask covers every edge cell."""
    ox, oy, span_x, span_y = control.tile_extent
    margin = 120.0
    corners = [
        control.enu_to_geodetic(ox - margin, oy - margin, 0.0)[:2],
        control.enu_to_geodetic(ox + span_x + margin, oy - margin, 0.0)[:2],
        control.enu_to_geodetic(ox + span_x + margin, oy + span_y + margin, 0.0)[:2],
        control.enu_to_geodetic(ox - margin, oy + span_y + margin, 0.0)[:2],
    ]
    lons = [c[0] for c in corners]
    lats = [c[1] for c in corners]
    return (round(min(lons), 6), round(min(lats), 6), round(max(lons), 6), round(max(lats), 6))


def fetch_sidewalks(control: DistrictControl) -> None:
    """DSRC-010. NYC planimetric surfaces: what a walker is actually standing on.

    The paved surfaces shipped until now were derived by widening OSM centrelines by a typical
    half-width per street class (DOQ-006). That gives a plausible diagram of a street but not its
    shape: no real kerb line, junctions as overlapping quads, and no distinction between a pavement,
    a plaza and a park.

    These are the city's own planimetric polygons, surveyed rather than inferred. Six layers are
    fetched because between them they cover everything underfoot in DUMBO:

      sidewalk   the pavement itself
      roadbed    the carriageway, so the kerb line is where the two meet rather than a guess
      curbs      surveyed kerb LINES, which is what makes a kerb face possible at all
      plazas     the pedestrianised spaces DUMBO has a lot of
      parks      Brooklyn Bridge Park and Commodore Barry, with names and land use
      boardwalk  the timber waterfront decks

    Each is a separate published dataset, so each gets its own file and its own audit sidecar.
    """
    print("[DSRC-010] NYC planimetric surfaces")
    west, south, east, north = control.bbox
    poly = (f"POLYGON(({west} {south},{east} {south},{east} {north},"
            f"{west} {north},{west} {south}))")
    where = f"intersects(the_geom,'{poly}')"

    for label, dataset, note in (
        ("sidewalks", "52n9-sdep", "Pavement polygons. Replaces centreline widening for footways."),
        ("roadbed", "i36f-5ih7", "Carriageway polygons. The kerb line is where this meets the pavement."),
        ("curbs", "5xvt-8cbk", "Surveyed kerb lines, as MultiLineString. Extruded to a kerb face."),
        ("plazas", "ue2e-9jm2", "Pedestrianised public plazas."),
        ("parks", "y6ja-fw4f", "Open space with names and land use; grass rather than paving."),
        ("boardwalk", "p9cw-7gsv", "Timber waterfront decks."),
    ):
        try:
            records = _socrata_paged(dataset, {"$where": where}, page=1000)
        except Exception as exc:  # noqa: BLE001 - one layer failing must not lose the others
            print(f"    {label}: FAILED ({exc})", file=sys.stderr)
            continue
        print(f"    {label}: {len(records)} features")
        _write(
            DATA / "streetscape" / f"{label}.raw.json",
            records,
            query=where,
            source_id="DSRC-010",
            note=note,
        )


def _touches_bbox(geom: object, bbox: tuple[float, float, float, float]) -> bool:
    if not isinstance(geom, dict):
        return False
    west, south, east, north = bbox
    polygons = (
        geom.get("coordinates", [])
        if geom.get("type") == "MultiPolygon"
        else [geom.get("coordinates", [])]
    )
    for polygon in polygons:
        if not polygon:
            continue
        for point in polygon[0]:
            try:
                lon, lat = float(point[0]), float(point[1])
            except (TypeError, ValueError, IndexError):
                continue
            if west <= lon <= east and south <= lat <= north:
                return True
    return False


def fetch_horizon(control: DistrictControl) -> None:
    """
    DSRC-011. Building footprints across the East River, for the Manhattan skyline.

    A view from a DUMBO street or the waterfront is dominated by Manhattan. Painting a photographic
    backdrop would be fast and would be a lie: it would not move correctly with the camera and would
    not be traceable to anything. These are the same authoritative footprints used for the district
    itself, just far away and rendered as silhouettes.
    """
    print("[DSRC-011] Lower Manhattan skyline footprints")
    # Lower Manhattan and the Two Bridges / Financial District frontage, which is what is actually
    # visible across the river from DUMBO. Deliberately bounded: the whole island would be tens of
    # thousands of buildings for geometry nobody can resolve at that range.
    north, west, south, east = 40.7260, -74.0180, 40.7005, -73.9700
    where = (
        f"within_box(the_geom,{north},{west},{south},{east}) "
        "AND height_roof > 20"
    )
    records = _socrata_paged(
        "5zhs-2jue",
        {"$where": where, "$select": "bin,the_geom,height_roof,ground_elevation,feature_code"},
    )
    print(f"    {len(records)} buildings over 20 ft across the river")
    _write(
        DATA / "horizon" / "manhattan-skyline.raw.json",
        records,
        query=where,
        source_id="DSRC-011",
        note=(
            "Same authoritative dataset as the district (DSRC-001), bounded to the frontage visible "
            "from DUMBO and filtered to buildings tall enough to read at that distance. Rendered as "
            "silhouettes at LOD3, never selectable, never dimensionally citable at that range."
        ),
    )


def fetch_ferry_routes(control: DistrictControl) -> None:
    """DSRC-012. Ferry landings and routes, so vessels move along real lines rather than invented ones."""
    print("[DSRC-012] Ferry landings and routes (OpenStreetMap)")
    query = (
        "[out:json][timeout:180];"
        "("
        'node["amenity"="ferry_terminal"](40.680,-74.045,40.745,-73.955);'
        'way["amenity"="ferry_terminal"](40.680,-74.045,40.745,-73.955);'
        'way["route"="ferry"](40.680,-74.045,40.745,-73.955);'
        ");"
        # `out geom;` alone. Combining it with `tags` suppresses geometry entirely, which returns
        # correct-looking route names attached to empty paths.
        "out geom;"
    )
    result = _overpass(query)
    elements = []
    for element in result.get("elements", []):
        tags = element.get("tags") or {}
        if not tags:
            continue
        entry = {
            "osm_type": element["type"],
            "osm_id": element["id"],
            "tags": tags,
        }
        if element.get("geometry"):
            entry["geometry"] = [
                [float(p["lon"]), float(p["lat"])] for p in element["geometry"]
            ]
        lat = element.get("lat") or (element.get("center") or {}).get("lat")
        lon = element.get("lon") or (element.get("center") or {}).get("lon")
        if lat is not None and lon is not None:
            entry["lon"] = float(lon)
            entry["lat"] = float(lat)
        elements.append(entry)
    print(f"    {len(elements)} ferry features")
    _write(
        DATA / "streetscape" / "ferry.raw.json",
        elements,
        query=query,
        source_id="DSRC-012",
        note="OpenStreetMap contributors, ODbL. Ferry terminals and route lines in the East River.",
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--all", action="store_true")
    parser.add_argument("--footprints", action="store_true")
    parser.add_argument("--pluto", action="store_true")
    parser.add_argument("--streets", action="store_true")
    parser.add_argument("--landmarks", action="store_true")
    parser.add_argument("--furniture", action="store_true")
    parser.add_argument("--storefronts", action="store_true")
    parser.add_argument("--nta", action="store_true")
    parser.add_argument("--trees", action="store_true")
    parser.add_argument("--sidewalks", action="store_true")
    parser.add_argument("--horizon", action="store_true")
    parser.add_argument("--ferry", action="store_true")
    parser.add_argument("--dem", action="store_true")
    parser.add_argument("--hydrography", action="store_true")
    args = parser.parse_args()

    control = DistrictControl()
    print(f"district control : {control.path.name} @ {control.sha256[:12]}")
    print(f"query envelope   : {control.bbox}")

    jobs = []
    if args.all or args.footprints:
        jobs.append(fetch_footprints)
    if args.all or args.pluto:
        jobs.append(fetch_pluto)
    if args.all or args.streets:
        jobs.append(fetch_streets)
    if args.all or args.landmarks:
        jobs.append(fetch_landmarks)
    if args.all or args.furniture:
        jobs.append(fetch_street_furniture)
    if args.all or args.storefronts:
        jobs.append(fetch_storefronts)
    if args.all or args.nta:
        jobs.append(fetch_nta)
    if args.all or args.trees:
        jobs.append(fetch_trees)
    if args.all or args.horizon:
        jobs.append(fetch_horizon)
    if args.all or args.ferry:
        jobs.append(fetch_ferry_routes)
    if args.all or args.sidewalks:
        jobs.append(fetch_sidewalks)
    if args.all or args.hydrography:
        jobs.append(fetch_hydrography)
    if args.all or args.dem:
        jobs.append(fetch_dem)

    if not jobs:
        parser.error("choose at least one source, or --all")

    failed = []
    for job in jobs:
        try:
            job(control)
        except Exception as exc:  # noqa: BLE001 - one bad source must not lose the others
            print(f"  FAILED: {job.__name__}: {exc}", file=sys.stderr)
            failed.append(job.__name__)

    if failed:
        print(f"\n{len(failed)} source(s) failed: {failed}", file=sys.stderr)
        return 1
    print("\nall sources fetched")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
