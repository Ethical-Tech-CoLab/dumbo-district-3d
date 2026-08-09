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

Usage:
    python scripts/ingest_sources.py --all
    python scripts/ingest_sources.py --footprints --pluto
"""

from __future__ import annotations

import argparse
import hashlib
import json
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

    Uses the standard shoelace moments. `area2` accumulates twice the signed area, so the centroid is
    the moment sum divided by three times that. Interior rings are ignored: a building's holes do not
    move its centroid enough to matter for a point-in-district test.
    """
    area2 = 0.0
    mx = my = 0.0
    for polygon in polygons:
        if not polygon:
            continue
        ring = polygon[0]
        if len(ring) < 4:
            continue
        for i in range(len(ring) - 1):
            x1, y1 = float(ring[i][0]), float(ring[i][1])
            x2, y2 = float(ring[i + 1][0]), float(ring[i + 1][1])
            cross = x1 * y2 - x2 * y1
            area2 += cross
            mx += (x1 + x2) * cross
            my += (y1 + y2) * cross
    if area2 == 0.0:
        return None
    return (mx / (3.0 * area2), my / (3.0 * area2))


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


def fetch_sidewalks(control: DistrictControl) -> None:
    """DSRC-010. NYC planimetric sidewalk polygons, used to pave the walk view."""
    print("[DSRC-010] NYC sidewalk polygons")
    west, south, east, north = control.bbox
    where = f"intersects(the_geom,'POLYGON(({west} {south},{east} {south},{east} {north},{west} {north},{west} {south}))')"
    try:
        records = _socrata_paged("vfx9-tbb6", {"$where": where}, page=2000)
    except Exception:
        # Some Socrata deployments reject `intersects` on this dataset; fall back to a bbox filter
        # on the computed centroid-ish envelope and clip locally.
        print("    intersects() rejected, falling back to unfiltered page scan")
        records = _socrata_paged("vfx9-tbb6", {}, page=2000)
        records = [r for r in records if _touches_bbox(r.get("the_geom"), control.bbox)]
    print(f"    {len(records)} sidewalk polygons")
    _write(
        DATA / "streetscape" / "sidewalks.raw.json",
        records,
        query=where,
        source_id="DSRC-010",
        note="NYC planimetric sidewalk polygons. Paves the walk view instead of a flat grey plane.",
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
    parser.add_argument("--nta", action="store_true")
    parser.add_argument("--trees", action="store_true")
    parser.add_argument("--sidewalks", action="store_true")
    parser.add_argument("--horizon", action="store_true")
    parser.add_argument("--ferry", action="store_true")
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
