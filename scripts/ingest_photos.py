"""
Fetch openly-licensed photographs of DUMBO for the appearance corpus.

This is the ingest half of the first photo campaign. It finds images that are *already* published
under a licence that permits reuse, records who made them and under what terms, and writes a raw
candidate list. It never scrapes a tourism site, a listing agent or a social feed: an image whose
licence is unknown cannot be used here however good it looks, so fetching it would only create a
liability.

Two sources, both licence-first:

  Wikimedia Commons  — everything on Commons carries an explicit licence, and much of it carries
                       coordinates and a capture date too, which is what turns a photograph into an
                       observation rather than a picture.
  Openverse          — aggregates Flickr and others, and can be queried with a licence filter so
                       non-commercial and no-derivatives material never enters the result set.

The licence decides what may be done with each image, and that decision is recorded per record
rather than applied as a blanket assumption. See LICENCE_POLICY below.

Usage:
    python scripts/ingest_photos.py
    python scripts/ingest_photos.py --limit 40
"""

from __future__ import annotations

import argparse
import html
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from district_control import AGENT_ID, DistrictControl

REPO_ROOT = Path(__file__).resolve().parent.parent
DATA = REPO_ROOT / "data"
USER_AGENT = (
    "dumbo-district-3d/1.0 "
    "(https://github.com/Ethical-Tech-CoLab/dumbo-district-3d; openly-licensed imagery survey) "
    "Python-urllib/3.12"
)

COMMONS_API = "https://commons.wikimedia.org/w/api.php"
OPENVERSE_API = "https://api.openverse.org/v1/images/"


# --------------------------------------------------------------------- licence

# What each licence family permits, expressed in the photo-survey contract's own `usage` terms.
#
#   redistribute      the image itself may be served to end users, with its credit line
#   derive_appearance colours, materials and dimensions may be measured from it; the image is not
#                     republished by us
#
# Share-alike is deliberately held at derive_appearance. Measuring the dominant brick colour of a
# wall extracts a fact, and facts carry no copyright, so nothing downstream inherits the ShareAlike
# obligation. Republishing the photograph would be permitted too, but only under the same licence,
# and quietly mixing that obligation into a repository that is otherwise MIT is the kind of thing
# that is very hard to unpick later. Reference it, measure it, do not vendor it.
LICENCE_POLICY: dict[str, tuple[str, str]] = {
    "CC0-1.0": ("redistribute", "CC0 1.0 Universal"),
    "public-domain": ("redistribute", "Public domain"),
    "CC-BY-2.0": ("redistribute", "Creative Commons Attribution 2.0"),
    "CC-BY-3.0": ("redistribute", "Creative Commons Attribution 3.0"),
    "CC-BY-4.0": ("redistribute", "Creative Commons Attribution 4.0"),
    "CC-BY-SA-2.0": ("derive_appearance", "Creative Commons Attribution-ShareAlike 2.0"),
    "CC-BY-SA-3.0": ("derive_appearance", "Creative Commons Attribution-ShareAlike 3.0"),
    "CC-BY-SA-4.0": ("derive_appearance", "Creative Commons Attribution-ShareAlike 4.0"),
}

# Substrings that disqualify a licence outright, checked before the allowlist so a new or oddly
# spelled variant fails closed rather than slipping through.
LICENCE_DENY = ("nc", "nd", "noncommercial", "no-derivative", "fair", "unknown", "gfdl-only")


def normalise_licence(raw: str, version: str = "") -> str | None:
    """Map a source's licence string onto an SPDX-style identifier we have a policy for."""
    if not raw:
        return None
    text = raw.strip().lower().replace("_", "-").replace(" ", "-")
    if text in ("cc0", "cc0-1.0", "cc-zero"):
        return "CC0-1.0"
    if text in ("pdm", "pd", "public-domain", "public-domain-mark"):
        return "public-domain"
    if text.startswith("cc-by") or text.startswith("by"):
        share_alike = "sa" in re.split(r"[-.]", text)
        version = version or ""
        match = re.search(r"(\d\.\d)", text + "-" + version)
        if not match:
            return None
        family = "CC-BY-SA" if share_alike else "CC-BY"
        return f"{family}-{match.group(1)}"
    return None


def licence_allows(identifier: str | None, raw: str) -> tuple[str, str] | None:
    """Return (usage, licence name) when this licence is usable, else None."""
    probe = (raw or "").lower()
    if any(bad in re.split(r"[-\s.]", probe) or bad in probe.replace(" ", "-")
           for bad in LICENCE_DENY):
        return None
    if identifier is None:
        return None
    return LICENCE_POLICY.get(identifier)


# ------------------------------------------------------------------- fetching


def _http(url: str, *, tries: int = 6, timeout: int = 90) -> bytes:
    """Fetch, backing off hard when a shared public API asks us to.

    These are free services run for everyone, and a 429 is a request to slow down rather than an
    error to retry through. Retry-After is honoured when given, and the fallback backoff is
    exponential rather than linear so a burst does not turn into a sustained hammering.
    """
    last: Exception | None = None
    for attempt in range(tries):
        try:
            request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return response.read()
        except urllib.error.HTTPError as exc:
            last = exc
            if exc.code in (429, 503):
                hinted = exc.headers.get("Retry-After") if exc.headers else None
                wait = int(hinted) if (hinted or "").isdigit() else min(60, 5 * 2 ** attempt)
                print(f"    {exc.code}; backing off {wait}s "
                      f"({attempt + 1}/{tries})", file=sys.stderr)
                time.sleep(wait)
                continue
            if exc.code in (404, 400):
                raise
            time.sleep(3 * (attempt + 1))
        except (urllib.error.URLError, TimeoutError) as exc:
            last = exc
            wait = 3 * (attempt + 1)
            print(f"    retry {attempt + 1}/{tries} in {wait}s ({exc})", file=sys.stderr)
            time.sleep(wait)
    raise RuntimeError(f"failed after {tries} attempts: {url}") from last


def _api(base: str, params: dict) -> dict:
    return json.loads(_http(base + "?" + urllib.parse.urlencode(params)))


def _strip_html(value: str) -> str:
    """Commons returns Artist and Credit as HTML fragments; the credit line must be plain text."""
    text = re.sub(r"<[^>]+>", "", value or "")
    return html.unescape(text).strip()


# The campaign's shot list, as queries. Each entry says what the images are wanted as evidence FOR,
# which is what lets a wide streetscape inform paving and tree size without pretending to describe
# one building's windows.
SUBJECTS: list[dict] = [
    {"subject": "district_streetscape", "commons_category": "Dumbo, Brooklyn",
     "openverse": "DUMBO Brooklyn street",
     "aspects": ["facade_material", "facade_colour", "paving_material", "street_furniture"]},
    {"subject": "carousel", "commons_category": "Jane's Carousel",
     "openverse": "Jane's Carousel Brooklyn",
     "aspects": ["facade_material", "condition", "other"]},
    {"subject": "empire_stores", "commons_category": "Empire Stores",
     "openverse": "Empire Stores Brooklyn warehouse",
     "aspects": ["facade_material", "facade_colour", "window_pattern", "storefront"]},
    {"subject": "waterfront_park", "commons_category": "Brooklyn Bridge Park",
     "openverse": "Brooklyn Bridge Park waterfront rocks",
     "aspects": ["paving_material", "tree_size", "condition", "other"]},
    {"subject": "cobblestone", "commons_category": None,
     "openverse": "cobblestone street DUMBO Brooklyn",
     "aspects": ["paving_material", "kerb"]},
    {"subject": "brick_warehouse", "commons_category": None,
     "openverse": "brick warehouse loft Brooklyn DUMBO",
     "aspects": ["facade_material", "facade_colour", "window_pattern"]},
    {"subject": "street_trees", "commons_category": None,
     "openverse": "street trees Brooklyn sidewalk DUMBO",
     "aspects": ["tree_size", "tree_species", "paving_material"]},
    {"subject": "washington_street", "commons_category": None,
     "openverse": "Washington Street DUMBO Manhattan Bridge view",
     "aspects": ["facade_material", "facade_colour", "paving_material", "roofline"]},
]


def fetch_commons_geosearch(control: DistrictControl, limit: int) -> list[dict]:
    """Files Commons itself places inside the district. Position comes from the source, not from us."""
    west, south, east, north = control.bbox
    lat = (south + north) / 2.0
    lon = (west + east) / 2.0
    # Radius that comfortably covers the district envelope, in metres.
    radius = 1200
    print(f"[commons] geosearch {lat:.4f},{lon:.4f} r={radius}m")
    data = _api(COMMONS_API, {
        "action": "query", "format": "json", "list": "geosearch",
        "gscoord": f"{lat}|{lon}", "gsradius": str(radius),
        "gslimit": str(min(limit, 500)), "gsnamespace": "6",
    })
    hits = data.get("query", {}).get("geosearch", [])
    print(f"    {len(hits)} geolocated files")
    return [{"title": h["title"], "_geo": (h["lat"], h["lon"]), "_dist_m": h.get("dist"),
             "_subject": "district_streetscape", "_via": "geosearch"} for h in hits]


def fetch_commons_category(category: str, subject: str, limit: int) -> list[dict]:
    print(f"[commons] category {category!r}")
    out: list[dict] = []
    cont: dict = {}
    while len(out) < limit:
        data = _api(COMMONS_API, {
            "action": "query", "format": "json", "list": "categorymembers",
            "cmtitle": f"Category:{category}", "cmtype": "file",
            "cmlimit": "100", **cont,
        })
        members = data.get("query", {}).get("categorymembers", [])
        out.extend({"title": m["title"], "_subject": subject, "_via": "category"} for m in members)
        if "continue" not in data:
            break
        cont = {"cmcontinue": data["continue"]["cmcontinue"], "continue": data["continue"]["continue"]}
    print(f"    {len(out)} files")
    return out[:limit]


def hydrate_commons(candidates: list[dict]) -> list[dict]:
    """Attach licence, author, capture date, size and coordinates to each Commons candidate."""
    by_title = {c["title"]: c for c in candidates}
    titles = list(by_title)
    hydrated: list[dict] = []
    for start in range(0, len(titles), 20):
        batch = titles[start:start + 20]
        data = _api(COMMONS_API, {
            "action": "query", "format": "json", "titles": "|".join(batch),
            "prop": "imageinfo|coordinates", "iiprop": "url|extmetadata|size|mime",
            "iiurlwidth": "1280",
        })
        for page in data.get("query", {}).get("pages", {}).values():
            info = (page.get("imageinfo") or [{}])[0]
            if not info:
                continue
            meta = info.get("extmetadata", {})

            def field(key: str) -> str:
                return (meta.get(key, {}) or {}).get("value") or ""

            identifier = normalise_licence(field("License"), field("LicenseShortName"))
            verdict = licence_allows(identifier, field("License") + " " + field("LicenseShortName"))
            source = by_title.get(page.get("title"), {})
            coords = (page.get("coordinates") or [{}])[0]
            hydrated.append({
                "source_collection": "Wikimedia Commons",
                "title": page.get("title"),
                "page_url": "https://commons.wikimedia.org/wiki/" +
                            urllib.parse.quote((page.get("title") or "").replace(" ", "_")),
                "image_url": info.get("url"),
                "thumbnail_url": info.get("thumburl"),
                "mime": info.get("mime"),
                "width": info.get("width"),
                "height": info.get("height"),
                "licence_id": identifier,
                "licence_raw": field("LicenseShortName"),
                "licence_url": field("LicenseUrl") or None,
                "usage": verdict[0] if verdict else None,
                "licence_name": verdict[1] if verdict else None,
                "author": _strip_html(field("Artist")),
                "credit": _strip_html(field("Credit")),
                "captured_raw": _strip_html(field("DateTimeOriginal")),
                "lat": coords.get("lat") if coords else source.get("_geo", (None, None))[0],
                "lon": coords.get("lon") if coords else source.get("_geo", (None, None))[1],
                "subject": source.get("_subject", "district_streetscape"),
                "via": source.get("_via"),
            })
        print(f"    hydrated {min(start + 20, len(titles))}/{len(titles)}")
        time.sleep(1.0)
    return hydrated


def fetch_openverse(query: str, subject: str, limit: int) -> list[dict]:
    """Openverse, filtered at the API to licences that permit commercial use and modification."""
    print(f"[openverse] {query!r}")
    try:
        data = _api(OPENVERSE_API, {
            "q": query, "license_type": "commercial,modification",
            "page_size": str(min(limit, 50)),
        })
    except Exception as exc:  # noqa: BLE001 - one query failing must not lose the campaign
        print(f"    failed: {exc}", file=sys.stderr)
        return []
    results = data.get("results", [])
    print(f"    {len(results)} of {data.get('result_count')} results")
    out: list[dict] = []
    for item in results:
        identifier = normalise_licence(item.get("license", ""), item.get("license_version", ""))
        verdict = licence_allows(identifier, f"{item.get('license')} {item.get('license_version')}")
        out.append({
            "source_collection": f"Openverse / {item.get('source') or 'unknown'}",
            "title": item.get("title"),
            "page_url": item.get("foreign_landing_url") or item.get("url"),
            "image_url": item.get("url"),
            "thumbnail_url": item.get("thumbnail"),
            "mime": None,
            "width": item.get("width"),
            "height": item.get("height"),
            "licence_id": identifier,
            "licence_raw": f"{item.get('license')} {item.get('license_version')}".strip(),
            "licence_url": item.get("license_url"),
            "usage": verdict[0] if verdict else None,
            "licence_name": verdict[1] if verdict else None,
            "author": item.get("creator") or "",
            "credit": item.get("source") or "",
            "captured_raw": item.get("date_taken") or "",
            "lat": None,
            "lon": None,
            "subject": subject,
            "via": "openverse",
        })
    return out


# ------------------------------------------------------------------------ main


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--limit", type=int, default=120,
                        help="Maximum candidates per source query.")
    parser.add_argument("--source", choices=("all", "commons", "openverse"), default="all",
                        help="Run one source only. Results merge into the existing candidate file, "
                             "so a source that rate-limited can be retried without re-fetching the "
                             "others.")
    args = parser.parse_args()

    control = DistrictControl()
    print(f"district control : {control.path.name} @ {control.sha256[:12]}")

    raw: list[dict] = []
    if args.source in ("all", "commons"):
        commons_candidates = fetch_commons_geosearch(control, args.limit)
        for entry in SUBJECTS:
            if entry["commons_category"]:
                commons_candidates.extend(
                    fetch_commons_category(entry["commons_category"], entry["subject"], args.limit)
                )
        # De-duplicate by title before hydrating: the same file is often in several categories, and
        # every hydrate call is a request someone else pays for.
        unique: dict[str, dict] = {}
        for candidate in commons_candidates:
            unique.setdefault(candidate["title"], candidate)
        print(f"[commons] {len(unique)} unique candidates to hydrate")
        raw.extend(hydrate_commons(list(unique.values())))

    if args.source in ("all", "openverse"):
        for entry in SUBJECTS:
            raw.extend(fetch_openverse(entry["openverse"], entry["subject"], min(args.limit, 50)))
            time.sleep(2.0)

    out = DATA / "photos" / "photos.raw.json"
    merged: dict[str, dict] = {}
    if out.exists():
        for record in json.loads(out.read_text(encoding="utf-8")):
            key = record.get("image_url") or record.get("page_url") or record.get("title")
            if key:
                merged[key] = record
        print(f"\n[merge] {len(merged)} records already on disk")

    fresh = 0
    rejected = 0
    for record in raw:
        key = record.get("image_url") or record.get("page_url") or record.get("title")
        if not key:
            continue
        if not record.get("usage"):
            rejected += 1
            continue
        if key not in merged:
            fresh += 1
        merged[key] = record

    accepted = list(merged.values())

    print()
    print(f"fetched this run : {len(raw)}")
    print(f"new records      : {fresh}")
    print(f"rejected licence : {rejected}")
    print(f"corpus total     : {len(accepted)}")
    families: dict[str, int] = {}
    sources: dict[str, int] = {}
    for record in accepted:
        families[record["licence_id"]] = families.get(record["licence_id"], 0) + 1
        collection = (record.get("source_collection") or "?").split(" / ")[0]
        sources[collection] = sources.get(collection, 0) + 1
    for name, count in sorted(families.items(), key=lambda kv: -kv[1]):
        print(f"    {count:4d}  {name}")
    print(f"    sources: {sources}")

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(accepted, indent=1), encoding="utf-8")
    sidecar = out.with_suffix(out.suffix + ".source.json")
    sidecar.write_text(json.dumps({
        "source_id": "DSRC-015",
        "query": "Wikimedia Commons geosearch + categories; Openverse commercial,modification",
        "fetched_by": AGENT_ID,
        "corpus_total": len(accepted),
        "rejected_for_licence_this_run": rejected,
        "licence_families": families,
        "collections": sources,
        "note": (
            "Only images already published under a reuse-permitting licence. Anything without an "
            "explicit licence is rejected rather than assumed. Share-alike images are marked "
            "derive_appearance: measurements may be taken from them, the images are not vendored."
        ),
    }, indent=1), encoding="utf-8")
    print(f"\nwrote {out.relative_to(REPO_ROOT)}  ({len(accepted)} records)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
