# Milestones

Where the DUMBO district twin goes next, and why in this order.

Phase 1 is reported in [IMPLEMENTATION-REPORT.md](IMPLEMENTATION-REPORT.md).
Items marked ✅ have shipped.

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
flashiest next step and is near the bottom, because aligning it to an unregistered terrain surface
would mean redoing it.

---

## Shipped

| | |
|---|---|
| ✅ **Basemap layers** | Terrain, street, satellite, hybrid and plain. Provider-agnostic, all credential-free. [BASEMAP-LAYERS.md](../digital-3d-shared-contracts/BASEMAP-LAYERS.md) |
| ✅ **Scene dressing** | 1,252 census street trees, 1,986 paved surfaces with kerbs, 381 procedural facades. [SCENE-DRESSING.md](../digital-3d-shared-contracts/SCENE-DRESSING.md) |
| ✅ **Map camera** | Pan, zoom, and externally driven fly-to. A tour now opens over the whole district and flies in to its first stop. |
| ✅ **Collapsible UI** | Tour, photos and building details collapse to a clickable status row; state persists. Double-click or double-tap walks you there. |
| ✅ **Manhattan horizon** | 2,103 real skyline silhouettes across the river, with aerial perspective. Not a painted backdrop. |
| ✅ **Water and vessels** | Water at mean high water, ferries on 49 real OSM routes, seasonal sailboats and jetskis. Terrain is clipped to a land mask so the river is water. |
| ✅ **Photo survey contract** | Evidence model for crowdsourced photography, plus a volunteer capture guide. [PHOTO-SURVEY.md](../digital-3d-shared-contracts/PHOTO-SURVEY.md) |
| ✅ **Real bridge proxy consumed** | The Manhattan Bridge team published; the viewer now loads their 4,620-triangle proxy GLB and places it by their `placement`. The red wireframe placeholder is gone, and remains only as the fallback when their module is unavailable. |

---

## Note on the bridge integration

The bridge team ratified the placement this project proposed: `provisional` is now `false` and the
grade moved from `D` to `C`, with the same azimuth and translation. `DOQ-001` can close once we have
re-checked it against their geometry in context.

Their artifacts are co-served from `viewer/public/modules/`, which is **gitignored**. That is
deliberate: committing their GLB would put bridge geometry in this repository, which the standing
obligation below forbids. Co-serving is a deployment convenience; ownership is unchanged.

---

## Next — accuracy

### M3.1 — Terrain from NYC DEM and LiDAR — **highest priority**

Retires `DOQ-003`, still the largest inferred component in the model.

Ground height is interpolated from building base elevations. Those samples are grade A; the surface
between them is grade C, and everything touching the ground inherits it: the walking camera, every
tree, every paving quad, every tour stop, and now the land mask that decides where the river starts.

- Ingest `DSRC-008`, registered since Phase 1 so the remedy has a name.
- Replace `build_ground_grid`, keeping the output contract so nothing downstream changes.
- Derive the land mask from the DEM's own water surface rather than from the district boundary.
- Promote ground from `C` to `A` and close `DOQ-003`.

### M3.2 — Run the photo campaign

The contract and volunteer guide exist; the corpus does not. This is the highest-value item after
terrain, because it retires two open questions at once and is the only one that scales with
community effort rather than engineering time.

- Seed from Mapillary (CC BY-SA, carries bearing) and Wikimedia Commons, licence-checked per file.
- Stand up the auto-screen step: EXIF, dedupe, face and plate blurring, district clip.
- Promote `facades.json` entries from inferred `C` to observed `B`, per building, closing `DOQ-007`
  incrementally rather than in bulk.

### M3.3 — Verify the consumed bridge proxy in context

Their proxy now loads and renders. Remaining:

- Re-check the ratified placement against their geometry from several viewpoints, then close
  `DOQ-001` or file a correction.
- Move from co-serving their files to fetching their published URL, which is the deployment that
  actually proves the contract (option B in the coordination note).
- Drop the `placeholder_envelope` extension from their manifest once the fallback is no longer
  wanted.

### M3.4 — Close the placement open question

`DOQ-001` / their `OQ-009`. They have ratified the placement we proposed; we still owe a visual
check against their geometry before closing it on our side.

### M3.5 — Traced sidewalk polygons

Retires `DOQ-006`. Lower priority than it looks: at walking speed the derived kerbs read correctly.

### M3.6 — Shoreline geometry

The land mask is currently the district boundary polygon, which traces the shoreline by inspection
(`DOQ-005`). NYC's planimetric shoreline would make the water's edge real, and would improve the
waterfront tour stops.

---

## Then — fidelity

| | |
|---|---|
| **M4.1 Roof forms from LiDAR** | Flat roofs dominate LOD0's declared 0.2 m error. Depends on M3.1's ingest. |
| **M4.2 Facade textures from the photo corpus** | Only after M3.2 has enough rectified, licensed images. Deriving attributes comes first; textures are a heavier step with redistribution consequences. |
| **M4.3 Named landmark models** | Jane's Carousel, the Archway, Empire Stores — the objects tours point at. The prop contract already supports it: set `url` on a prototype. Small, high-visibility, independent. |
| **M4.4 District photogrammetry** | Deliberately last. Must be aligned to a control surface; doing it before M3.1 means doing it twice. |

---

## Platform

| | |
|---|---|
| **M5.1 Tour recording to video** | The `FrameLoop` hidden-document fallback already makes headless rendering work. Remaining: frame-accurate stepping and muxing. |
| **M5.2 Directions provider adapter** | Replace `route_leg` with a Google, Bing, Apple or OSRM response. Worth doing to *prove* the format claim. |
| **M5.3 3D Tiles export** | The tile index is deliberately 3D-Tiles-shaped. Do this when a second district exists, not before. |
| **M5.4 A second district** | The real test of whether the viewer is a tool rather than a DUMBO application. Everything is in place: shared frame, dressing as data, provider-agnostic basemaps, no district-specific code in the viewer. Until this is done, "the viewer is generic" is a design claim rather than a demonstrated fact. |
| **M5.5 Mobile pass** | Panels collapse and double-tap works, but pointer-lock walking has no touch equivalent yet. Do after M5.4 so the port does not bake in DUMBO assumptions. |

---

## Deferred, with reasons

| | Why not yet |
|---|---|
| Collision / physics | A survey tool, not a game. Walking through a wall is a feature when inspecting. |
| Interiors | No authoritative source; would be invention at district scale. |
| Pedestrians and vehicles | Animated agents imply behavioural claims the data cannot support. Vessels are the exception, and even they are graded C and D and confined to declared routes and areas. |
| Historic time slices | Needs a temporal dimension in the contracts first. The photo survey's `captured_at` is the beginning of one. |
| Weather rendering | The tour contract declares `weather`; no renderer yet. Cosmetic. |

---

## Standing obligations

Independent of milestones, and non-negotiable:

- **No bridge geometry in this repository, ever.** Consume by URN.
- **The frame anchor is frozen** for contract major version 1.
- **Every new asset carries `source_basis`, `source_refs` and an honest `confidence`.**
- **Every inference gets an open question ID** before it ships, not after someone notices.
- **Photographs never grant grade `A`.** They are evidence of appearance, not of dimension.
- **Attribution stays visible** — ODbL, NYC Open Data and every basemap provider require it.
- **The build fails on frame drift.** Do not weaken that check to make a build pass.
