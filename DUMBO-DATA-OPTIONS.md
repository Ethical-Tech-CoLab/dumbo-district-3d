# Roof form, storefront imagery, and what is actually obtainable

Two questions that both come down to the same thing: what may we lawfully and practically get, and
what should we stop waiting for.

---

## 1. Roof form — no longer blocked, and it needs two small dependencies

`DOQ-011` recorded roof form as blocked behind three dead ends. Two of them have permissively-licensed
solutions that were never tried, because the project had held a stdlib-only line for Python.

| Blocker | Tool | Licence | Verdict |
|---|---|---|---|
| NYC DCP 3-D Building Model is Rhino `.3dm` | **`rhino3dm` 8.32.0** | MIT | McNeel's own reader. Direct route to LOD2 with roof forms for Brooklyn CD2, which covers this district. |
| TUM CityGML archive is **Deflate64**; zlib cannot decompress | **`zipfile-deflate64` 0.2.0** | Apache-2.0 | Drop-in `zipfile` replacement. |
| georocket's CityGML is unlicensed | — | — | Still excluded. Unlicensed is unlicensed. |

Both licences are compatible with this repository's MIT licence, and neither is a native build with
awkward wheels. **Recommendation: take `rhino3dm` and go to NYC DCP's model**, which is the city's own,
covers exactly this area, and is the same publisher as half the sources already registered. The
CityGML route becomes a fallback rather than the plan.

The cost of the stdlib-only line was four evidenced dead ends and a milestone parked for weeks. The
line was worth holding while the alternative was unknown; it is not worth holding against a
15 MB MIT-licensed reader published by the format's own vendor.

### Satellite imagery: useful, but not for this

Worth being precise, because "use satellite" sounds like it should work and mostly does not.

Satellite and aerial **imagery** is a picture. It gives you roof *colour*, *material* and *texture*,
and the outline of plant, tanks and bulkheads. It does not give you *height*, and roof form is a
height question. Deriving shape from imagery needs either stereo pairs with known camera geometry —
commercially licensed, expensive, and a photogrammetry pipeline this project does not have — or a
single-image depth model, which invents plausible geometry and would be graded D at best. Putting
invented roof shapes on real buildings is exactly the failure this project's confidence model exists
to prevent.

**What actually answers it is LiDAR, and New York already publishes some.** `DSRC-008` is registered
but only partly used: the district takes bare-earth elevation from USGS 3DEP, which by definition has
buildings removed. NYC's own 2017 topobathymetric LiDAR includes a **first-return surface**, and
`DSM − DEM` over a footprint is a height field from which roof planes can be fitted directly. That is
a measurement, gradeable `B`, not a guess.

So the order is: **NYC DCP's model first** (already a finished LOD2 product), **LiDAR DSM second**
(a measurement we can fit ourselves), imagery only for roof colour.

---

## 2. Storefront data from Google or Bing — no, and the substitute is better

The short answer is that it is contractually prohibited, and it is worth stating plainly rather than
discovering later.

Google Maps Platform's terms define **Google Maps Content** to include imagery and places data
including business listings, and then:

- content generally may not be **cached or stored** beyond narrow, service-specific windows — 30 days
  where it is allowed at all;
- content must not be used **in conjunction with a non-Google map**;
- there is a standing **prohibition on using Google Maps Content to create content**, relaxed only for
  one grounding API and only with source links attached.

A DUMBO twin is a non-Google map, the whole point is to store what we derive, and the derivation *is*
creating content. All three prohibitions are hit at once. Microsoft's Bing Maps terms are
substantially the same in the ways that matter. Street View facade capture is not a grey area.

**The licensed equivalent is Mapillary**, and it is better suited anyway:

| | Google/Bing Street View | Mapillary |
|---|---|---|
| Licence | Proprietary, no derivation | **CC BY-SA 4.0** |
| Storage | Prohibited / 30 days | Permitted |
| Derivation | Prohibited | Permitted with attribution |
| Coverage in DUMBO | Dense | Dense, and volunteers can add more |
| Camera pose | Not exposed | **Published per image** |

That last row is the one that matters most. Mapillary publishes position and compass angle per image,
which is precisely the `position` + `bearing_deg` that `photo-survey.schema.json` already requires and
which most Commons photographs lack — 23 of the current 62 are unplaced for exactly this reason.
Mapillary imagery arrives already carrying the fields that make a photograph usable as evidence
rather than as reference.

Share-alike is handled the same way this project already handles CC-BY-SA on Commons: `usage:
derive_appearance`. Measure the colour, record the credit, never redistribute the pixels. No
third-party image bytes are committed.

**Recommendation: add Mapillary as `DSRC-019` and use it for storefront and facade capture.** It slots
into the existing ingest, rejection ledger and review sheet without a new pipeline, and it closes the
"we cannot see the ground floor" gap that awnings currently paper over with a conventional shape.

Two things Google *can* lawfully contribute, for completeness: nothing that is stored. Business
*names* are also places data and are covered. The OSM shop nodes already ingested as `DSRC-017` are
the lawful source for that, and 424 of 432 are named.
