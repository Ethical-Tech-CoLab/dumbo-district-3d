"""
Turn the raw photo candidates into an evidence corpus, and derive what can be measured from it.

Three jobs, in order:

  1. Emit `photo-survey.json` conforming to the shared photo-survey contract. Every record carries
     its licence, its credit line, what it is evidence *for*, and how well it is located. An image
     with no licence never reaches this stage; one with no subject is context, not evidence.

  2. Attach geolocated observations to the buildings they plausibly show, so a facade can cite a
     photograph instead of an inference.

  3. Where an image decoder is available, measure the dominant colours of the images we are allowed
     to measure, and publish an observed palette. This is the part that makes the walk view look
     like DUMBO rather than like a generic brick district.

Step 3 needs Pillow. The rest is standard library. If Pillow is missing the corpus is still built
and still useful; the palette is simply absent and the appearance stays inferred, which the output
says plainly rather than quietly pretending.
"""

from __future__ import annotations

import argparse
import colorsys
import hashlib
import json
import math
import re
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from district_control import AGENT_ID, DistrictControl

REPO_ROOT = Path(__file__).resolve().parent.parent
DATA = REPO_ROOT / "data"
OUT = REPO_ROOT / "viewer" / "public" / "district"
CACHE = DATA / "photos" / "cache"

CONTRACT_VERSION = "1.0.0"
MODULE_ID = "dumbo-district"
FRAME_ID = "nyc-harbor-enu"
CAMPAIGN_ID = "dumbo-2026-found-imagery"

USER_AGENT = (
    "dumbo-district-3d/1.0 "
    "(https://github.com/Ethical-Tech-CoLab/dumbo-district-3d; appearance survey) "
    "Python-urllib/3.12"
)

# What each campaign subject is good evidence for. Mirrors the shot list in ingest_photos.py.
SUBJECT_ASPECTS: dict[str, list[str]] = {
    "district_streetscape": ["facade_material", "facade_colour", "paving_material", "street_furniture"],
    "carousel": ["condition", "other"],
    "empire_stores": ["facade_material", "facade_colour", "window_pattern", "storefront"],
    "waterfront_park": ["paving_material", "tree_size", "condition"],
    "cobblestone": ["paving_material", "kerb"],
    "brick_warehouse": ["facade_material", "facade_colour", "window_pattern"],
    "street_trees": ["tree_size", "tree_species", "paving_material"],
    "washington_street": ["facade_material", "facade_colour", "paving_material", "roofline"],
}

# Material gates. Each says, in HLS terms, what the material could plausibly look like under real
# outdoor light, and where in the frame to look for it.
#
# This replaces "take the dominant colour of a crop", which does not work on street photography: the
# most common colour in a DUMBO photograph is sky, bridge steel or river, and a palette built that
# way came out uniformly grey and would have made every warehouse in the district look like a
# battleship. Gating by hue and saturation asks a narrower and much more answerable question — *where
# this photograph shows brick, what colour is the brick* — and treats coverage as the evidence that
# the material is actually present.
MATERIAL_GATES: dict[str, dict] = {
    "brick": {
        "region": (0.05, 0.15, 0.95, 0.80),
        "hue": (0.995, 0.11),          # red through orange, wrapping past 0
        "sat": (0.14, 1.00),
        "light": (0.12, 0.62),
        "min_coverage": 0.06,
        "label": "brick and terracotta masonry",
    },
    "paving": {
        "region": (0.05, 0.60, 0.95, 1.00),
        "hue": None,                    # neutral: judged on saturation alone
        "sat": (0.00, 0.16),
        "light": (0.18, 0.68),
        "min_coverage": 0.15,
        "label": "cobblestone, granite sett and asphalt",
    },
    "foliage": {
        "region": (0.05, 0.05, 0.95, 0.70),
        "hue": (0.17, 0.45),           # yellow-green through green
        "sat": (0.12, 1.00),
        "light": (0.10, 0.62),
        "min_coverage": 0.05,
        "label": "street tree canopy",
    },
    "water": {
        "region": (0.05, 0.35, 0.95, 0.95),
        "hue": (0.48, 0.66),           # cyan through blue
        "sat": (0.06, 1.00),
        "light": (0.15, 0.72),
        "min_coverage": 0.08,
        "label": "East River surface",
    },
}

# Which materials each campaign subject is worth searching for. A waterfront photo may evidence
# paving, foliage and water at once; a brick warehouse shot is not evidence about the river.
SUBJECT_MATERIALS: dict[str, list[str]] = {
    "district_streetscape": ["brick", "paving"],
    "empire_stores": ["brick"],
    "brick_warehouse": ["brick"],
    "washington_street": ["brick", "paving"],
    "cobblestone": ["paving"],
    "street_trees": ["foliage", "paving"],
    "waterfront_park": ["paving", "foliage", "water"],
    "carousel": ["brick"],
}


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def load(path: Path) -> object:
    return json.loads(path.read_text(encoding="utf-8"))


def slug(text: str) -> str:
    base = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")[:48]
    return base or "obs"


# ------------------------------------------------------------------ observations


def parse_captured(raw: str) -> tuple[str | None, str]:
    """Commons dates are free text. Return (ISO-8601 or None, precision)."""
    if not raw:
        return None, "unknown"
    text = raw.replace("\u00a0", " ").strip()
    match = re.search(r"(\d{4})-(\d{2})-(\d{2})[ T](\d{2}):(\d{2}):(\d{2})", text)
    if match:
        y, mo, d, h, mi, s = (int(v) for v in match.groups())
        try:
            return datetime(y, mo, d, h, mi, s, tzinfo=timezone.utc).isoformat(), "exact"
        except ValueError:
            pass
    match = re.search(r"(\d{4})-(\d{2})-(\d{2})", text)
    if match:
        y, mo, d = (int(v) for v in match.groups())
        try:
            return datetime(y, mo, d, tzinfo=timezone.utc).isoformat(), "day"
        except ValueError:
            pass
    match = re.search(r"(\d{1,2})\s+(January|February|March|April|May|June|July|August|September|"
                      r"October|November|December)\s+(\d{4})", text)
    if match:
        months = ["january", "february", "march", "april", "may", "june", "july", "august",
                  "september", "october", "november", "december"]
        day, month, year = int(match.group(1)), match.group(2).lower(), int(match.group(3))
        try:
            return datetime(year, months.index(month) + 1, day, tzinfo=timezone.utc).isoformat(), "day"
        except ValueError:
            pass
    match = re.search(r"(19|20)\d{2}", text)
    if match:
        return datetime(int(match.group(0)), 1, 1, tzinfo=timezone.utc).isoformat(), "year"
    return None, "unknown"


def season_of(iso: str | None) -> str | None:
    if not iso:
        return None
    month = int(iso[5:7])
    return {12: "winter", 1: "winter", 2: "winter", 3: "spring", 4: "spring", 5: "spring",
            6: "summer", 7: "summer", 8: "summer", 9: "autumn", 10: "autumn",
            11: "autumn"}[month]


def credit_line(record: dict) -> str:
    """The line that must appear wherever anything derived from this image is shown."""
    author = (record.get("author") or "").strip() or "unknown author"
    licence = record.get("licence_id") or "unknown licence"
    collection = record.get("source_collection") or ""
    title = (record.get("title") or "").removeprefix("File:")
    return f"{title} by {author}, {licence}, via {collection}".strip()


def grade_for(record: dict, captured: str | None, precision: str) -> str:
    """How much confidence an appearance derived from this image may claim.

    Photographs never grant A; that is the contract's rule, not a local one. B requires the image to
    be locatable, datable and large enough to read a material from. Everything else grants C, which
    is the same grade as an inference — an honest statement that it is corroboration, not evidence.
    """
    long_edge = max(record.get("width") or 0, record.get("height") or 0)
    located = record.get("lat") is not None and record.get("lon") is not None
    dated = precision in ("exact", "day", "month")
    if located and dated and long_edge >= 1600:
        return "B"
    return "C"


def build_observations(control: DistrictControl, records: list[dict]) -> list[dict]:
    west, south, east, north = control.bbox
    centre_lon, centre_lat = (west + east) / 2.0, (south + north) / 2.0

    observations: list[dict] = []
    for record in records:
        if not record.get("usage"):
            continue
        captured, precision = parse_captured(record.get("captured_raw", ""))
        aspects = SUBJECT_ASPECTS.get(record.get("subject", ""), ["other"])

        lat, lon = record.get("lat"), record.get("lon")
        if lat is not None and lon is not None:
            position = {"lon": round(float(lon), 7), "lat": round(float(lat), 7)}
            position_source = "exif_gps"
            accuracy = 25.0
        else:
            # Openverse rarely carries coordinates. The photograph is still evidence of what the
            # district's materials look like, but it cannot be pinned to a building, so it is placed
            # at the district centre with an accuracy that says exactly how little that means.
            position = {"lon": round(centre_lon, 7), "lat": round(centre_lat, 7)}
            position_source = "unknown"
            accuracy = 900.0

        identifier = hashlib.sha256((record.get("image_url") or record.get("page_url") or "")
                                    .encode()).hexdigest()[:10]
        observations.append({
            "observation_id": f"obs-{slug(record.get('subject', ''))}-{identifier}",
            "image_url": record.get("page_url") or record.get("image_url"),
            "thumbnail_url": record.get("thumbnail_url") or record.get("image_url"),
            "position": position,
            "position_source": position_source,
            "position_accuracy_m": accuracy,
            "captured_at": captured,
            "captured_precision": precision,
            "season": season_of(captured),
            "observes": [],
            "quality": {
                "pixels_long_edge": max(record.get("width") or 0, record.get("height") or 0) or None,
            },
            "license": record["licence_id"],
            "license_url": record.get("licence_url"),
            "attribution_text": credit_line(record),
            "rights_holder": (record.get("author") or "").strip() or None,
            "usage": record["usage"],
            "privacy_reviewed": False,
            "source_collection": record.get("source_collection"),
            "review": {
                "status": "auto_screened",
                "reviewed_at": now(),
                "grants_confidence": grade_for(record, captured, precision),
                "notes": (
                    "Licence checked automatically against an allowlist; no human has yet looked at "
                    "the image itself. Faces and plates are not screened, so nothing here is "
                    "published as an image without review."
                ),
            },
            "notes": f"campaign subject: {record.get('subject')}",
            "_subject": record.get("subject"),
            "_aspects": aspects,
        })
    return observations


# Titles that mean a photograph cannot be evidence about a building's exterior, however close to it
# the camera stood. Proximity is not aim: Commons coordinates record where the photographer was, so a
# picture taken inside a gym, or of a market stall, sits a few metres from a warehouse and would
# otherwise be attached to it as facade evidence. Without a compass bearing this keyword screen is
# the cheapest honest filter available; a campaign that captures bearing would not need it.
NOT_EXTERIOR = (
    "interior", "inside", "lobby", "boxing ring", "gym interior", "display", "jewelry",
    "jewellery", "for sale", "stall", "menu", "food", "coffee", "cupcake", "chocolate",
    "portrait", "poster", "map ", "map of", "logo", "plaque", "sign detail", "artwork",
    "sculpture detail", "installation", "performance", "concert", "wedding", "protest",
    "manhole", "bicycle", "dog", "cat", "car ", "limousine", "truck", "bus ", "boat",
    "ferry", "helicopter", "fireworks", "sunset over", "skyline from", "panorama of manhattan",
)

# Titles that positively indicate a street-level exterior view worth trusting a little more.
EXTERIOR_HINTS = (
    "street", "building", "warehouse", "facade", "avenue", "corner", "block", "storefront",
    "archway", "houses", "row", "brick", "loft", "factory", "stores",
)


def is_exterior_subject(observation: dict) -> bool:
    title = ((observation.get("image_url") or "") + " " +
             (observation.get("attribution_text") or "")).lower()
    return not any(bad in title for bad in NOT_EXTERIOR)


def attach_to_buildings(control: DistrictControl, observations: list[dict],
                        buildings: list[dict], radius_m: float) -> tuple[int, int]:
    """Link each located observation to the buildings it plausibly shows.

    Deliberately conservative. Without a compass bearing a photograph cannot be said to show one
    particular facade, so an observation is attached to every building within its accuracy radius
    and marked `partial`: it is evidence that these buildings look like this, not proof of which one
    is in the frame. Bearing, when a future campaign supplies it, is what narrows this to one.
    """
    attached = 0
    screened = 0
    for observation in observations:
        if observation["position_source"] == "unknown":
            continue
        if not is_exterior_subject(observation):
            screened += 1
            observation["notes"] = (observation.get("notes") or "") + \
                "; screened out of facade evidence: subject is not a building exterior"
            continue
        lon, lat = observation["position"]["lon"], observation["position"]["lat"]
        x, y, _ = control.geodetic_to_enu(lon, lat)
        near: list[tuple[float, dict]] = []
        for building in buildings:
            bx, by = building["centroid"]
            distance = math.hypot(bx - x, by - y)
            if distance <= radius_m:
                near.append((distance, building))
        near.sort(key=lambda item: item[0])
        aspects = [a for a in observation["_aspects"]
                   if a in ("facade_material", "facade_colour", "window_pattern",
                            "storefront", "awning", "signage", "roofline", "entrance")]
        if not aspects:
            continue
        for distance, building in near[:4]:
            observation["observes"].append({
                "asset_id": f"urn:d3d:{MODULE_ID}:{building['local_id']}",
                "aspect": aspects,
                "visibility": "partial",
                "distance_m": round(distance, 1),
            })
        if observation["observes"]:
            attached += 1
    return attached, screened


# --------------------------------------------------------------------- palette


def try_pillow():
    try:
        from PIL import Image  # noqa: PLC0415
        return Image
    except ImportError:
        return None


def fetch_thumbnail(url: str) -> Path | None:
    """Cache a thumbnail locally so a palette run is not a fresh download every time."""
    if not url:
        return None
    CACHE.mkdir(parents=True, exist_ok=True)
    name = hashlib.sha256(url.encode()).hexdigest()[:16] + ".img"
    path = CACHE / name
    if path.exists():
        return path
    try:
        request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(request, timeout=60) as response:
            payload = response.read()
        if len(payload) < 1024:
            return None
        path.write_bytes(payload)
        return path
    except Exception:  # noqa: BLE001 - a missing thumbnail must not fail the corpus
        return None


def _hue_in(hue: float, span: tuple[float, float] | None) -> bool:
    if span is None:
        return True
    low, high = span
    return (low <= hue <= high) if low <= high else (hue >= low or hue <= high)


def sample_material(image_mod, path: Path, gate: dict) -> dict | None:
    """Measure the colour of one material in one photograph, if it is present at all.

    Returns the mean colour of the pixels that pass the gate, plus the fraction of the sampled
    region they occupied. Coverage is what makes this evidence rather than wishful thinking: if only
    two percent of a streetscape is brick-coloured, that photograph is not telling us about brick,
    and it is discarded instead of contributing a colour.
    """
    try:
        with image_mod.open(path) as raw:
            image = raw.convert("RGB")
            width, height = image.size
            box = (int(gate["region"][0] * width), int(gate["region"][1] * height),
                   int(gate["region"][2] * width), int(gate["region"][3] * height))
            if box[2] - box[0] < 16 or box[3] - box[1] < 16:
                return None
            crop = image.crop(box)
            crop.thumbnail((180, 180))
            pixels = list(crop.convert("RGB").tobytes())
    except Exception:  # noqa: BLE001 - a bad image must not fail the corpus
        return None

    total = len(pixels) // 3
    if total < 400:
        return None

    sat_low, sat_high = gate["sat"]
    light_low, light_high = gate["light"]
    matched: list[tuple[int, int, int]] = []
    for i in range(0, total * 3, 3):
        r, g, b = pixels[i], pixels[i + 1], pixels[i + 2]
        hue, lightness, saturation = colorsys.rgb_to_hls(r / 255, g / 255, b / 255)
        if not (light_low <= lightness <= light_high):
            continue
        if not (sat_low <= saturation <= sat_high):
            continue
        if not _hue_in(hue, gate["hue"]):
            continue
        matched.append((r, g, b))

    coverage = len(matched) / total
    if coverage < gate["min_coverage"] or len(matched) < 200:
        return None

    r = sum(m[0] for m in matched) // len(matched)
    g = sum(m[1] for m in matched) // len(matched)
    b = sum(m[2] for m in matched) // len(matched)
    return {"hex": f"#{r:02x}{g:02x}{b:02x}", "coverage": round(coverage, 3)}


def build_palette(observations: list[dict], limit: int) -> dict:
    image_mod = try_pillow()
    if image_mod is None:
        print("    Pillow not installed; skipping palette derivation")
        return {"available": False, "reason": "no image decoder installed", "surfaces": {}}

    hits: dict[str, list[dict]] = {}
    downloaded = 0
    for observation in observations:
        materials = SUBJECT_MATERIALS.get(observation.get("_subject") or "", [])
        if not materials or downloaded >= limit:
            continue
        path = fetch_thumbnail(observation.get("thumbnail_url") or "")
        if path is None:
            continue
        downloaded += 1
        for material in materials:
            measured = sample_material(image_mod, path, MATERIAL_GATES[material])
            if measured is None:
                continue
            hits.setdefault(material, []).append({
                "observation_id": observation["observation_id"],
                "attribution_text": observation["attribution_text"],
                "license": observation["license"],
                **measured,
            })

    surfaces = {}
    for material, entries in hits.items():
        # Weight each contribution by how much of its frame the material actually filled, so a photo
        # of a whole brick wall counts for more than one with a brick doorway in the corner.
        weight_total = sum(e["coverage"] for e in entries) or 1.0
        r = sum(int(e["hex"][1:3], 16) * e["coverage"] for e in entries) / weight_total
        g = sum(int(e["hex"][3:5], 16) * e["coverage"] for e in entries) / weight_total
        b = sum(int(e["hex"][5:7], 16) * e["coverage"] for e in entries) / weight_total
        spread = sorted(e["hex"] for e in entries)
        surfaces[material] = {
            "label": MATERIAL_GATES[material]["label"],
            "observations": len(entries),
            "mean_hex": f"#{int(r):02x}{int(g):02x}{int(b):02x}",
            "mean_coverage": round(weight_total / len(entries), 3),
            "samples": [{"observation_id": e["observation_id"], "hex": e["hex"],
                         "coverage": e["coverage"]} for e in entries[:80]],
            "range_hex": [spread[0], spread[-1]],
            "credits": sorted({e["attribution_text"] for e in entries})[:16],
        }
        print(f"    {material:9} {len(entries):3d} photos -> {surfaces[material]['mean_hex']} "
              f"(mean coverage {surfaces[material]['mean_coverage']:.2f})")

    print(f"    decoded {downloaded} thumbnails")
    return {"available": True, "surfaces": surfaces}


# ------------------------------------------------------------------------ main


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--palette-limit", type=int, default=60,
                        help="Maximum thumbnails to download for palette derivation.")
    parser.add_argument("--attach-radius", type=float, default=60.0,
                        help="Metres within which a located photo is taken to show a building.")
    args = parser.parse_args()

    control = DistrictControl()
    raw_path = DATA / "photos" / "photos.raw.json"
    if not raw_path.exists():
        print("no photo candidates; run: python scripts/ingest_photos.py", file=sys.stderr)
        return 1
    records = load(raw_path)
    print(f"[corpus] {len(records)} licensed candidates")

    observations = build_observations(control, records)
    print(f"    {len(observations)} observations")

    # Buildings, for attachment. Imported lazily so this script does not drag in the whole asset
    # builder when it is only being used to refresh a palette.
    from build_district_assets import Dem, build_buildings  # noqa: PLC0415
    buildings, _ = build_buildings(control, Dem.load(control))
    attached, screened = attach_to_buildings(control, observations, buildings, args.attach_radius)
    print(f"    {attached} observations attached to buildings")
    print(f"    {screened} screened out as not building exteriors")

    print("[palette]")
    palette = build_palette(observations, args.palette_limit)

    located = sum(1 for o in observations if o["position_source"] != "unknown")
    grades: dict[str, int] = {}
    for observation in observations:
        grade = observation["review"]["grants_confidence"]
        grades[grade] = grades.get(grade, 0) + 1

    survey = {
        "contract_version": CONTRACT_VERSION,
        "module_id": MODULE_ID,
        "frame_id": FRAME_ID,
        "campaign": {
            "campaign_id": CAMPAIGN_ID,
            "title": "DUMBO found-imagery campaign, first pass",
            "opened": datetime.now(timezone.utc).date().isoformat(),
            "contact": "https://github.com/Ethical-Tech-CoLab/dumbo-district-3d/issues",
            "guidance_url": "PHOTO-SURVEY.md",
        },
        "observations": [
            {k: v for k, v in o.items() if not k.startswith("_") and v is not None}
            for o in observations
        ],
        "provenance": {
            "module_id": MODULE_ID,
            "generated_by": f"dumbo-district-3d/scripts@1.0.0 ({AGENT_ID})",
            "generated_at": now(),
        },
    }
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "photo-survey.json").write_text(json.dumps(survey, separators=(",", ":")), encoding="utf-8")

    palette_doc = {
        "contract_version": CONTRACT_VERSION,
        "module_id": MODULE_ID,
        "campaign_id": CAMPAIGN_ID,
        "derived_from": "photo-survey.json",
        "notes": (
            "Colours measured from openly-licensed photographs of DUMBO. Measurements of an image "
            "are facts about it, so nothing here inherits a ShareAlike obligation; the images "
            "themselves are referenced, never vendored. Credits are carried per surface so anything "
            "using a palette can display them."
        ),
        **palette,
        "provenance": {
            "module_id": MODULE_ID,
            "generated_by": f"dumbo-district-3d/scripts@1.0.0 ({AGENT_ID})",
            "generated_at": now(),
        },
    }
    (OUT / "photo-palette.json").write_text(json.dumps(palette_doc, indent=1), encoding="utf-8")

    print()
    print(f"observations : {len(observations)}  ({located} located, {len(observations) - located} unplaced)")
    print(f"grades       : {grades}")
    print(f"attached     : {attached}")
    print(f"wrote        : photo-survey.json, photo-palette.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
