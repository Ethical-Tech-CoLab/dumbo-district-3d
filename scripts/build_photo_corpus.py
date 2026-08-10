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

from district_control import AGENT_ID, DistrictControl, point_in_ring

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
    "grass": {
        # Lower in frame than foliage and lighter: a lawn is lit from above, a canopy shades itself.
        # Same hue window, so the region and lightness are what separate them.
        "region": (0.05, 0.50, 0.95, 1.00),
        "hue": (0.18, 0.42),
        "sat": (0.14, 1.00),
        "light": (0.18, 0.70),
        "min_coverage": 0.08,
        "label": "park lawn and planting",
    },
    "riprap": {
        # The armour stone at the water's edge. Neutral like paving but darker and coarser, and low
        # in frame; the saturation ceiling is what keeps sunlit sand out of it.
        "region": (0.05, 0.55, 0.95, 1.00),
        "hue": None,
        "sat": (0.00, 0.20),
        "light": (0.10, 0.50),
        "min_coverage": 0.10,
        "label": "shoreline rock and riprap",
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


def photo_key(url: str) -> str:
    """Stable identifier for a photograph: a hash of its canonical source page.

    Query strings are stripped first, because Commons appends tracking parameters that vary between
    fetches and would otherwise make the same image look like a new one every time.
    """
    return hashlib.sha256((url or "").split("?", 1)[0].encode()).hexdigest()[:12]


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

        # The identifier must depend only on the image, never on the search that happened to find
        # it. It used to include the campaign subject, which meant the same photograph reached by a
        # different query got a different id — and every human decision about it was silently lost
        # on the next ingest. Decisions are the most expensive thing in this pipeline; their key has
        # to be the one stable fact available.
        identifier = photo_key(record.get("page_url") or record.get("image_url") or "")
        # The exact identifier the previous format produced, kept so decisions recorded against it
        # can be migrated without loss. Reproduced rather than approximated: the old key hashed the
        # file URL and prefixed the search subject, so nothing about it can be inferred from the new
        # one.
        legacy = "obs-{}-{}".format(
            slug(record.get("subject", "")),
            hashlib.sha256((record.get("image_url") or record.get("page_url") or "")
                           .encode()).hexdigest()[:10],
        )
        observations.append({
            "observation_id": f"obs-{identifier}",
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
            "_legacy_id": legacy,
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
    # Indoor events and workspaces. Added after an office interior filed as "FrogDesign Ling.jpg"
    # reached a warehouse facade: rooms with large windows defeat the sky test below, because a
    # window is also bright and blue.
    "studio", "workshop", "meetup", "conference", "seminar", "class ", "desk", "office interior",
    "apartment", "showroom", "exhibit", "arts center", "arts centre",
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


def looks_outdoor(image_mod, path: Path) -> float | None:
    """Fraction of the top of the frame that reads as sky. Recorded, but NOT used to reject.

    The intent was to tell interiors from exteriors, since a street-level photograph of a building
    almost always has sky across the top and a room does not. Measured against this corpus, it does
    not separate them: interiors scored 0.16 to 0.79 and exteriors 0.51 to 1.00, because rooms with
    large windows are bright and blue at the top of frame too. Any threshold that caught the office
    interior would also have thrown away good street views.

    It is kept because it is a real measurement worth carrying on the record, and because publishing
    the number is more useful than a threshold quietly tuned until this particular sample passed.
    Deciding interior from exterior needs a human, which is exactly what `review.status` says.
    """
    try:
        with image_mod.open(path) as raw:
            image = raw.convert("RGB")
            width, height = image.size
            strip = image.crop((0, 0, width, max(8, int(height * 0.08))))
            strip.thumbnail((200, 60))
            pixels = list(strip.convert("RGB").tobytes())
    except Exception:  # noqa: BLE001
        return None

    total = len(pixels) // 3
    if total < 60:
        return None
    sky = 0
    for i in range(0, total * 3, 3):
        r, g, b = pixels[i], pixels[i + 1], pixels[i + 2]
        _, lightness, saturation = colorsys.rgb_to_hls(r / 255, g / 255, b / 255)
        if lightness > 0.52 and (b >= r or saturation < 0.14):
            sky += 1
    return round(sky / total, 3)


# What a photograph can be evidence FOR, chosen by a reviewer.
#
# Categories exist because "use" and "skip" turned out to be too blunt. A picture of Jane's Carousel
# is worthless for a warehouse facade and valuable for the carousel; a 1910 archival shot describes a
# DUMBO that no longer exists; and a photograph of the Manhattan Bridge is not ours to derive
# anything from at all, because another module owns that structure. Collapsing all of those into
# "use" meant either throwing away good material or letting it colour the wrong thing.
#
# Each maps to the `aspect` vocabulary already in the photo-survey contract, so a category is a
# reviewer-facing name for a decision the schema could already express.
REVIEW_CATEGORIES: dict[str, dict] = {
    "facade": {
        "label": "Building facade",
        "hint": "A building exterior you could read a material or colour from.",
        "aspects": ["facade_material", "facade_colour", "window_pattern", "storefront",
                    "awning", "signage", "roofline", "entrance"],
        # A picture of a building taken from the street necessarily contains the street, so it is
        # asked about paving too. The hue gate and coverage threshold stop it contributing nonsense.
        "materials": ["brick", "paving"],
        "attaches": True,
    },
    "surface": {
        "label": "Street surface",
        "hint": "Cobblestone, paving, kerbs, the ground underfoot.",
        "aspects": ["paving_material", "kerb"],
        "materials": ["paving"],
        "attaches": False,
    },
    "greenery": {
        "label": "Trees and planting",
        "hint": "Street trees, canopy size, grass.",
        "aspects": ["tree_size", "tree_species"],
        "materials": ["foliage", "paving"],
        "attaches": False,
    },
    "furniture": {
        "label": "Street furniture",
        "hint": "Benches, lamps, railings, bollards, bike racks.",
        "aspects": ["street_furniture"],
        "materials": ["paving"],
        "attaches": False,
    },
    "landmark": {
        "label": "Landmark",
        "hint": "Jane's Carousel, the Archway, a named thing rather than a generic building.",
        "aspects": ["condition", "other"],
        "materials": ["brick"],
        "attaches": True,
    },
    "bridge": {
        "label": "Bridge (another module owns this)",
        "hint": "Brooklyn or Manhattan Bridge. Kept and credited, never used to derive district geometry.",
        "aspects": ["other"],
        "materials": [],
        "attaches": False,
        "foreign": True,
    },
    "historic": {
        "label": "Historic",
        "hint": "Archival. Describes a DUMBO that may no longer exist.",
        "aspects": ["condition", "other"],
        "materials": [],
        "attaches": False,
        "historic": True,
    },
    "context": {
        "label": "Context only",
        "hint": "Area designation, maps, signage, wayfinding. Not geometry.",
        "aspects": ["other"],
        "materials": [],
        "attaches": False,
    },
    # Added for the second campaign. The first eight categories described a street; these describe
    # the waterfront, which is where most visitors actually stand and which had no evidence at all.
    "waterside": {
        "label": "Water's edge",
        "hint": "Rocks, riprap, the beach, where the land stops and the river starts.",
        "aspects": ["paving_material", "condition"],
        "materials": ["riprap"],
        "attaches": False,
    },
    "lawn": {
        "label": "Grass and lawn",
        "hint": "Park lawns, planted beds, grass meeting paving.",
        "aspects": ["tree_size", "condition"],
        "materials": ["grass", "foliage"],
        "attaches": False,
    },
    "railing": {
        "label": "Railings and fences",
        "hint": "The promenade guard rail, park fences, handrails.",
        "aspects": ["street_furniture"],
        "materials": [],
        "attaches": False,
    },
}

DEFAULT_CATEGORY = "facade"


def parse_verdict(value: str) -> tuple[str, list[str]]:
    """Split a decision into (verdict, categories).

    A photograph usually shows several things at once — a street view is a facade, a pavement, a
    street tree and a bench in one frame — so a decision carries a list rather than a single choice.
    Written as `use:facade,surface,greenery`.

    Accepts the earlier single-category form, and the plain `use` and `skip` the first review sheet
    produced, so older decision files still apply cleanly; a bare `use` means the facade category it
    used to imply.
    """
    if not isinstance(value, str):
        return "", [DEFAULT_CATEGORY]
    verdict, _, rest = value.partition(":")
    if verdict not in ("use", "skip"):
        return "", [DEFAULT_CATEGORY]
    categories = [c for c in (part.strip() for part in rest.split(",")) if c in REVIEW_CATEGORIES]
    return verdict, categories or [DEFAULT_CATEGORY]


def merged_spec(categories: list[str]) -> dict:
    """Combine several categories into one set of permissions.

    Union, deliberately. A photograph tagged `bridge,facade` shows both, and the honest reading is
    that the facade part is usable and the bridge part is not — which falls out for free, because
    the bridge category contributes no aspects and no materials to the union. The same trick means a
    reviewer can add a tag without having to think about interactions.

    `historic` is the exception that is not a union: it contributes aspects, so the photograph can
    still say what a building looked like, but it contributes no materials, because a colour
    measured from an archival image describes a wall that may have been repainted twice since.
    """
    aspects: list[str] = []
    materials: list[str] = []
    attaches = False
    historic = any(REVIEW_CATEGORIES[c].get("historic") for c in categories)
    for category in categories:
        spec = REVIEW_CATEGORIES[category]
        for aspect in spec["aspects"]:
            if aspect not in aspects:
                aspects.append(aspect)
        if not historic:
            for material in spec.get("materials", []):
                if material not in materials:
                    materials.append(material)
        attaches = attaches or spec.get("attaches", False)
    return {
        "aspects": aspects,
        "materials": materials,
        "attaches": attaches,
        "historic": historic,
        # Foreign only when there is nothing else in the frame we are allowed to look at.
        "foreign_only": all(REVIEW_CATEGORIES[c].get("foreign") for c in categories),
    }


def load_decisions(observations: list[dict] | None = None) -> dict[str, str]:
    """Human review decisions, if any have been made.

    The pipeline's automatic screen is a filter of last resort and is documented as such. Where a
    person has looked at a photograph and said use or skip, that judgement wins outright: it is the
    only thing here that can tell an office interior from a street view, or notice that a sharp
    photograph of a parked car has been quietly colouring four warehouses.

    Decisions recorded against the old subject-dependent ids are migrated on the way in, matching on
    the URL hash they both end with. Somebody's afternoon of reviewing 336 photographs is not
    something to lose to a change of key format.
    """
    path = DATA / "photos" / "review-decisions.json"
    if not path.exists():
        return {}
    try:
        decisions = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        print(f"    review decisions unreadable ({exc}); ignoring", file=sys.stderr)
        return {}

    valid = {k: v for k, v in decisions.items() if parse_verdict(v)[0]}
    if observations is None:
        return valid

    known = {o["observation_id"] for o in observations}
    by_legacy = {o["_legacy_id"]: o["observation_id"] for o in observations if o.get("_legacy_id")}
    migrated: dict[str, str] = {}
    stale = 0
    remapped = 0
    for key, value in valid.items():
        if key in known:
            migrated[key] = value
        elif key in by_legacy:
            migrated[by_legacy[key]] = value
            remapped += 1
        else:
            stale += 1
    if stale:
        print(f"    {stale} decision(s) refer to photographs not in the corpus "
              f"(expected: rejected ones are purged and tracked in rejected.json)")
    if remapped:
        print(f"    migrated {remapped} decision(s) to stable identifiers")
        # Persist the migration, or everything downstream — the review sheet above all — keeps
        # reading the old keys and shows an afternoon of review as untouched. Entries that could not
        # be matched are carried through untouched rather than dropped.
        healed = dict(migrated)
        for key, value in valid.items():
            if key not in known and key not in by_legacy:
                healed[key] = value
        path.write_text(json.dumps(healed, indent=1, sort_keys=True), encoding="utf-8")
    return migrated


def write_rejection_ledger(observations: list[dict], decisions: dict[str, str]) -> int:
    """Record every rejected photograph by URL, so it cannot be sourced again.

    Decisions are keyed by observation id, which only exists once a corpus has been built. The
    ingest runs before that and works in URLs, so it needs the rejections in its own terms —
    otherwise every new search would re-offer the same parked cars and gallery interiors, and
    somebody would have to reject them a second time.

    The ledger is additive and never forgets: an entry stays even if the photograph drops out of the
    current corpus, because the reason it was rejected does not expire.
    """
    path = DATA / "photos" / "rejected.json"
    existing: dict[str, dict] = {}
    if path.exists():
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            existing = {}

    added = 0
    for observation in observations:
        verdict, _ = parse_verdict(decisions.get(observation["observation_id"], ""))
        if verdict != "skip":
            continue
        url = (observation.get("image_url") or "").split("?", 1)[0]
        if not url or url in existing:
            continue
        existing[url] = {
            "rejected_at": now(),
            "title": (observation.get("attribution_text") or "").split(" by ")[0][:120],
            "reason": "not useful for the district model",
        }
        added += 1

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(existing, indent=1, sort_keys=True), encoding="utf-8")
    return added


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
    image_mod = try_pillow()
    decisions = load_decisions(observations)
    if decisions:
        tally: dict[str, int] = {}
        for value in decisions.values():
            verdict, categories = parse_verdict(value)
            for key in (["skip"] if verdict == "skip" else categories):
                tally[key] = tally.get(key, 0) + 1
        print(f"    {len(decisions)} human decisions on file; these override the automatic screen")
        print(f"      {dict(sorted(tally.items(), key=lambda kv: -kv[1]))}")
    rings = [(b, [(p[0], p[1]) for p in b["ring"]] + [(b["ring"][0][0], b["ring"][0][1])])
             for b in buildings]
    for observation in observations:
        verdict, categories = parse_verdict(decisions.get(observation["observation_id"], ""))
        spec = merged_spec(categories)
        review = observation["review"]

        if verdict == "skip":
            screened += 1
            review["status"] = "rejected"
            review["reviewer"] = "human"
            observation["notes"] = (observation.get("notes") or "") + "; rejected by human review"
            continue

        if verdict == "use":
            review["status"] = "accepted"
            review["reviewer"] = "human"
            observation["category"] = categories[0]
            observation["categories"] = categories
            if spec["foreign_only"]:
                # The anti-duplication rule, applied to imagery. A photograph of the Manhattan
                # Bridge is kept and credited, because it is genuinely a picture of DUMBO, but
                # nothing about that structure is ours to derive: the bridge module owns it.
                observation["notes"] = (observation.get("notes") or "") + \
                    "; subject belongs to another module, retained for reference only"
            if spec["historic"]:
                observation["notes"] = (observation.get("notes") or "") + \
                    "; historic, describes a past state rather than current conditions"
            if not spec["attaches"]:
                continue

        if observation["position_source"] == "unknown":
            continue

        if verdict != "use" and decisions:
            # Same rule as the palette: once a reviewer is in the loop, an unreviewed photograph
            # waits for them. Attaching it anyway is how a bridge's paint ended up on a warehouse,
            # and the first review threw out two thirds of what this pass proposed.
            observation["review"]["status"] = "auto_screened"
            continue

        lon, lat = observation["position"]["lon"], observation["position"]["lat"]
        x, y, _ = control.geodetic_to_enu(lon, lat)

        if verdict != "use" and not is_exterior_subject(observation):
            screened += 1
            observation["notes"] = (observation.get("notes") or "") + \
                "; screened out of facade evidence: subject is not a building exterior"
            continue

        if image_mod is not None:
            path = fetch_thumbnail(observation.get("thumbnail_url") or "")
            if path is not None:
                sky = looks_outdoor(image_mod, path)
                if sky is not None:
                    observation["quality"]["sky_fraction"] = sky

        # A coordinate that falls inside a footprint is treated as naming its SUBJECT, not as proof
        # that the photographer was indoors. Commons records the location a picture is *about* at
        # least as often as where the camera stood, and on this corpus the two are indistinguishable.
        # So a hit means "this photograph is of that building", which is the strongest attribution
        # available without a compass bearing.
        subject = next((b for b, ring in rings if point_in_ring((x, y), ring)), None)

        near: list[tuple[float, dict]] = []
        for building in buildings:
            bx, by = building["centroid"]
            distance = math.hypot(bx - x, by - y)
            if distance <= radius_m:
                near.append((distance, building))
        near.sort(key=lambda item: item[0])
        # A reviewed photograph is evidence for what the reviewer said it shows. Only when nobody
        # has looked does this fall back to the campaign's own guess from the search that found it.
        candidate = spec["aspects"] if verdict == "use" else observation["_aspects"]
        aspects = [a for a in candidate
                   if a in ("facade_material", "facade_colour", "window_pattern",
                            "storefront", "awning", "signage", "roofline", "entrance",
                            "condition", "other")]
        if not aspects:
            continue

        if subject is not None:
            # Named subject: one building, stated clearly, rather than a spray of neighbours.
            observation["observes"].append({
                "asset_id": f"urn:d3d:{MODULE_ID}:{subject['local_id']}",
                "aspect": aspects,
                "visibility": "clear",
                "distance_m": 0.0,
            })
        else:
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

    # A photograph a reviewer rejected must not colour anything, and one they categorised should
    # only colour what they said it shows. The palette used to measure the whole corpus, so a
    # picture of a parked car still contributed to what "DUMBO brick" looks like even after a human
    # had said no to it — and a cobblestone close-up was asked about brick.
    decisions = load_decisions(observations)
    reviewed: dict[str, list[str]] = {}
    if decisions:
        before = len(observations)
        kept = []
        for observation in observations:
            verdict, categories = parse_verdict(decisions.get(observation["observation_id"], ""))
            if verdict == "skip":
                continue
            if verdict == "use":
                reviewed[observation["observation_id"]] = merged_spec(categories)["materials"]
            kept.append(observation)
        observations = kept
        print(f"    measuring from {len(observations)} reviewed photographs "
              f"(of {before}); rejects excluded")

    hits: dict[str, list[dict]] = {}
    downloaded = 0
    undecided = 0
    for observation in observations:
        materials = reviewed.get(observation["observation_id"])
        if materials is None:
            if decisions:
                # A review loop exists, so an unreviewed photograph is a *candidate*, not evidence.
                # It used to fall back to the materials implied by the search query that found it,
                # which meant a fresh campaign silently repainted the district before anyone had
                # looked at a single frame -- and the first campaign's review rejected two thirds of
                # what the automatic pass had proposed. Waiting is the honest behaviour.
                undecided += 1
                continue
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
    if undecided:
        print(f"    {undecided} photograph(s) awaiting review and contributing nothing until then")
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

    added = write_rejection_ledger(observations, load_decisions(observations))
    if added:
        print(f"    {added} newly rejected photograph(s) added to the do-not-source ledger")

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
