# Milestones

Where the DUMBO district twin goes next, and why in this order.

Phase 1 is complete and reported in [IMPLEMENTATION-REPORT.md](IMPLEMENTATION-REPORT.md).
Phase 2 items marked ✅ were delivered alongside this document.

---

## How these are prioritised

Three tests, applied in order:

1. **Does it retire a known inaccuracy?** A documented open question is a debt with interest. The
   longer inferred data sits in the model, the more work is built on top of it.
2. **Does it unblock another team?** Anything the Manhattan Bridge team is waiting on outranks
   local polish.
3. **Does it change what a visitor experiences?** Between two equal items, prefer the one someone
   can see.

Deliberately *not* a criterion: how impressive it looks in isolation. Photogrammetry would be the
flashiest next step and is close to the bottom, because aligning it to an unregistered terrain
surface would mean redoing it.

---

## Phase 2 — accuracy and presence

### M2.1 — Basemap layers in map view ✅

Terrain, street, satellite and hybrid, in the idiom users expect from Google and Bing, without
adopting a vendor SDK or a second coordinate system.

Delivered: `basemap.schema.json`, `BasemapController` in the kernel, five credential-free layers,
and [BASEMAP-LAYERS.md](../digital-3d-shared-contracts/BASEMAP-LAYERS.md).

A side effect worth more than the feature: the red corridor marking tiles that declare the Manhattan
Bridge lands exactly on the real bridge in both OSM street and USGS satellite imagery. That is a
free, continuous check on this module's georeferencing.

### M2.2 — Scene dressing in walk view ✅

1,252 street trees from the Forestry census, 1,986 paved surfaces with kerbs, and 381 facades with
procedural window banding derived from PLUTO class and year.

Delivered: `scene-props.schema.json`, `build_scene_dressing.py`, and
[SCENE-DRESSING.md](../digital-3d-shared-contracts/SCENE-DRESSING.md). Measured at 60 fps with
1,252 instances in 30 draw calls.

### M2.3 — Terrain from NYC DEM and LiDAR — **highest priority remaining**

Retires `DOQ-003`, the largest inferred component in the model.

Ground height is currently interpolated from building base elevations. Those samples are grade A;
the surface between them is grade C, and everything that touches the ground inherits that: the
walking camera, every tree, every paving quad, every tour stop.

- Ingest `DSRC-008` (already registered, deliberately, so the remedy has a name).
- Replace `build_ground_grid` with a resampled DEM; keep the same output contract so nothing
  downstream changes.
- Promote the ground surface from `C` to `A` and close `DOQ-003`.

Everything below inherits accuracy from this, which is why it goes first.

### M2.4 — Consume the real bridge proxy

The bridge team has begun publishing (their manifest and attribution now appear in our viewer). When
their level-2 proxy GLB lands:

- Delete the red wireframe placeholder from `DistrictScene`.
- Drop the `dumbo-district.placeholder_envelope` extension from their manifest.
- Delete `viewer/public/modules/manhattan-bridge/bridge-manifest.json` and point `depends_on` at
  their published URL.
- Re-check the provisional placement against their geometry once it is visible in context.

### M2.5 — Ratify or correct the bridge placement

`DOQ-001` / their `OQ-009`. Ours is grade D, derived from OSM centreline. Once M2.4 lands the
placement can be judged visually against real geometry, which is a far better test than the numbers
alone.

### M2.6 — Shoreline and waterfront surfaces

Water is currently an infinite plane at mean high water, with a visible seam where it meets terrain.
NYC publishes a planimetric shoreline. Fixing this also improves the Brooklyn Bridge Park stops on
the family tour, which are waterfront-facing.

### M2.7 — Traced sidewalk polygons

Retires `DOQ-006`. Paving is currently derived from centrelines with typical widths. NYC's
planimetric sidewalk dataset would give real kerb lines and resolve junctions properly.
Lower priority than it looks: at walking speed the derived version reads correctly.

---

## Phase 3 — fidelity

### M3.1 — Roof forms from LiDAR

Every roof is currently flat at the dataset roof height, which is the dominant term in LOD0's
declared 0.2 m error. LiDAR-derived roof forms would justify a mesh-based LOD0 and let that error
figure drop honestly.

Depends on M2.3, since it uses the same LiDAR ingest.

### M3.2 — Facade imagery

Retires `DOQ-007`. Facades currently describe the *kind* of building. Street-level imagery would
make them describe *that* building.

Requires a licensing decision before any technical work: Mapillary is ODbL and usable; most
commercial street imagery is not redistributable. **Resolve the licence first** — this is exactly
the trap documented in BASEMAP-LAYERS.md §3, where an endpoint answering `200` was mistaken for
permission.

### M3.3 — District photogrammetry

Deliberately last among fidelity items, despite being the most impressive. Photogrammetry must be
aligned to a control surface; aligning it to today's interpolated ground would mean redoing it after
M2.3. The original brief also excluded it from Phase 1, and that judgement still holds.

### M3.4 — Named landmark models

Jane's Carousel, the Archway, Empire Stores. These are the objects tour stops actually point at, and
the ones a visitor recognises. The prop contract already supports it: set `url` on a prototype and
the same instances render a real GLB.

Small, high-visibility, and independent of everything else — a good candidate to slot in whenever
modelling capacity appears.

---

## Phase 4 — platform

### M4.1 — Tour recording to video

The `FrameLoop` hidden-document fallback already makes headless rendering work, which was the hard
part. Remaining work is frame-accurate stepping and muxing.

### M4.2 — Directions provider adapter

Replace `route_leg` in `build_tour.py` with a Google, Bing, Apple or OSRM response. The tour format
was shaped as a directions format specifically so this is a rename rather than a rewrite. Worth
doing to *prove* that claim, not because the internal router is inadequate.

### M4.3 — 3D Tiles export

The tile index is deliberately 3D-Tiles-shaped. At district scale the current format is better
(smaller, re-extrudable). At borough scale it would not be. Do this when a second district is added,
not before.

### M4.4 — A second district

The real test of whether the viewer is a *tool* rather than a DUMBO application. Everything is in
place: the frame is shared and canonical, dressing is data, basemaps are provider-agnostic, and
nothing district-specific is compiled into the viewer.

Until this is done, "the viewer is generic" is a design claim rather than a demonstrated fact.

---

## Deferred, with reasons

| | Why not yet |
|---|---|
| Collision / physics | A survey tool, not a game. Walking through a wall is a feature when inspecting. |
| Interiors | No authoritative source; would be invention at district scale. |
| Vehicles and pedestrians | Animated agents imply behavioural claims the data cannot support. |
| Historic time slices | Interesting, but needs a temporal dimension in the contracts first. |
| Weather and seasons | The tour contract already declares `weather`; no renderer yet. Cosmetic. |
| Mobile / VR | Do after M4.4 proves genericity, or the port hard-codes DUMBO assumptions. |

---

## Standing obligations

Independent of milestones, and non-negotiable:

- **No bridge geometry in this repository, ever.** Consume by URN.
- **The frame anchor is frozen** for contract major version 1.
- **Every new asset carries `source_basis`, `source_refs` and an honest `confidence`.**
- **Every inference gets an open question ID** before it ships, not after someone notices.
- **Attribution stays visible** — ODbL and NYC Open Data both require it.
- **The build fails on frame drift.** Do not weaken that check to make a build pass.
