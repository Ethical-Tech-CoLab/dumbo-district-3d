# Phase 1 Implementation Report

**Module:** `dumbo-district-3d`
**Date:** 2026-08-09
**Control document:** `DUMBO-GEOSPATIAL-CONTROL.md` @ `16248a6cae4a94eb`

---

## 1. Summary

Phase 1 is complete. All nine required deliverables are built, from real authoritative data, and
validate against contracts that now exist in a shared repository both projects can consume.

The brief's central risk was duplication between two teams building two viewers. That is addressed
not by coordination alone but by a concrete mechanism: **one viewer kernel, one LOD axis, one
coordinate frame, and a module manifest that is the entire dependency surface between projects.** The
recommendation is argued in `digital-3d-shared-contracts/VIEWER-MODES.md`; the implementation is the
running viewer in this repository.

Along the way we found a defect that would have affected both projects: the bridge is authored against
mean high water, NYC data is NAVD88, and the two differ by 0.59 m. That is now a registered control
value carried in the shared georeference.

---

## 2. Deliverables

| # | Required | Status | Where |
|---|---|---|---|
| 1 | Boundary definition | ✅ | `DUMBO-GEOSPATIAL-CONTROL.md` §2.1, `data/boundaries/dumbo-district.geojson` |
| 2 | Tile grid | ✅ | 112 tiles @ 128 m, `viewer/public/district/tile-index.json` |
| 3 | Building footprint import | ✅ | 381 buildings, `DSRC-001` |
| 4 | PLUTO integration | ✅ | 375 of 381 joined on BBL, `DSRC-002` |
| 5 | Geospatial control model | ✅ | 30 controls, machine-parsed, frame verified |
| 6 | Asset registry schema | ✅ | `digital-3d-shared-contracts/schemas/asset-registry.schema.json` |
| 7 | Map viewer prototype | ✅ | plan view drawn from the tile index itself |
| 8 | Scene viewer prototype | ✅ | first-person walk, streaming, LOD, picking |
| 9 | Bridge placeholder integration point | ✅ | optional dependency + provisional placement + labelled placeholder |

Also delivered, because the requirement arrived with the brief:

| | |
|---|---|
| Tour script contract | `tour-script.schema.json` — Maps-style directions plus an experience layer |
| Tour player | `@d3d/viewer-kernel`, shared, not district-local |
| Two demonstration tours | routed on the real pedestrian network |
| Shared contracts repository | 9 schemas, 5 documents, 2 runtime packages, a validator |

No photogrammetry, as instructed.

### Deviations from the brief, and why

**Paths.** `AGENT-INSTRUCTIONS.md` §"PROJECT STRUCTURE" sketches `modules/dumbo-district/…` and
`/shared-contracts/`. Those describe a single monorepo. The actual layout is three sibling
repositories, which is how the work was set up and how the shared contracts repository was
subsequently specified. The deliverables therefore sit at each repository's root — matching
`manhattan-bridge-3d`, which also keeps its governance documents at root — and `/shared-contracts/`
became the separate `digital-3d-shared-contracts` repository. Nothing about the content changed; only
the prefix.

The one place the original path shape survives is where it is still meaningful: the viewer serves the
bridge module at `viewer/public/modules/manhattan-bridge/`, which is the `/modules/manhattan-bridge/`
consumption point the brief asked the district to assume.

**Payload format.** Levels 0–2 ship as JSON footprint rings rather than GLB. Reasoning in
`DUMBO-LOD-STRATEGY.md` §4; the shared contract permits it explicitly via `payload_format` and
`representation`, so this is a declared choice rather than a silent one.

**Boundary source.** The brief lists NYC datasets as authoritative. For the *boundary* specifically,
no NYC dataset matches this subject — NTA `BK0202` also contains Downtown Brooklyn and Boerum Hill —
so the boundary is a documented project definition and the NTA is retained as context (`DSRC-004`).

---

## 3. What was built, in numbers

| | |
|---|---|
| Buildings | **381**, all inside the boundary polygon |
| Confidence | **381 grade A**, 0 B, 0 C, 0 D |
| Height source | 381 from the dataset's `height_roof`; 0 reconstructed, 0 fallback |
| PLUTO join | 375 of 381 (98.4%) |
| Tiles | 112 total — 32 hero, 31 walkable, 49 context; 49 carry content |
| Tile payloads | 123 files, **446 KB** for the entire district |
| Walk network | 4,346 nodes, 5,131 edges |
| Ground grid | 57 × 33 @ 32 m, 0.0–22.6 m NAVD88, grade C |
| Landmarks | 29 addressable URNs |
| Tours | 2, validated; the flagship is 4 stops, 871 m, 18:10 |
| Viewer bundle | 726 KB, 201 KB gzipped |

Every building being grade A is a real result rather than a generous one: plan geometry and roof
heights both come directly from an authoritative published dataset with a stated ±0.61 m accuracy.
The reconstruction fallbacks that would demote a building to C or D exist and were not needed here.

---

## 4. Verification

All of the following pass and were run against the final state.

```
python scripts/district_control.py --check-frame
  ENU round-trip error        : 0.0000 mm
  flat-plane drop at DCTL-004 : 1.257 m (declared 1.260 m)
  OK: frame is within its declared bounds

node tools/validate.mjs                       9 schemas compiled, 7 fixtures valid
node tools/validate.mjs <district docs>       6/6 valid
node tools/validate.mjs <bridge manifest>     valid
node tools/validate.mjs <2 tours>             2/2 valid

tsc --noEmit  (shared kernel)                 clean
tsc --noEmit  (district viewer)               clean
vite build                                    clean
```

Behaviour verified in a running browser:

| Claim | Evidence |
|---|---|
| Buildings render from real data | Washington Street view, warehouses either side |
| Streaming and LOD work together | 32–42 tiles resident, **LOD 0, 1 and 2 simultaneously** |
| Mode changes the budget, nothing else | walk 12 px → tour 8 px → map 48 px, same ladder |
| Terrain follows real relief | camera stands on interpolated ground, not at z = 0 |
| Tour drives the camera | all four stops resolve to their correct scene positions |
| Photos are real | framebuffer grabs appear in the strip at scripted moments |
| Narration and directions fire | stop D narration, turn-by-turn instructions |
| Map reflects the same index | hero/walkable/context zones and the bridge corridor visible |
| **Missing bridge degrades gracefully** | manifest removed → district still renders, integration notice shown, bridge attribution correctly dropped |

That last row is the anti-duplication rule proving itself: the district has no bridge geometry to fall
back on, and it does not need any.

---

## 5. Findings

### 5.1 The vertical datum defect

`manhattan-bridge-3d/GEOMETRY-CONTROL.md` sets `z = 0` at mean high water. All NYC building data is
NAVD88. These are different surfaces.

From NOAA CO-OPS station 8518750 (The Battery), epoch 1983–2001, relative to station datum:
`MHW = 2.44 m`, `NAVD88 = 1.85 m`. Therefore **MHW = NAVD88 + 0.59 m**, independently corroborated at
0.596 m by a NYSAPLS datum sheet for the same station.

Placing the bridge without this correction sinks it 0.59 m into the ground it lands on — larger than
the ±0.61 m accuracy of the footprint data, and visible at the anchorage plaza. Registered as
`DCTL-010` and published in `vertical_datum_offsets_m`, so it is a field read rather than a discovery.

### 5.2 DUMBO is not flat

Building base elevations run 0 to 22.9 m NAVD88, median 11.9 m. An initial flat-ground scene put the
walking camera roughly twelve metres underground.

No DEM is ingested yet, so the ground is interpolated by inverse-distance weighting from building base
elevations — which `DSRC-003` defines as authoritative NAVD88 ground samples. Grade C: the samples are
real, the surface between them is inferred. Tracked as `DOQ-003`; `DSRC-008` is registered as the
remedy.

### 5.3 OQ-009 needed an answer to make progress

The bridge's real-world azimuth is unregistered, so the district could not place it. Rather than guess
quietly, `scripts/propose_bridge_placement.py` derives a placement from the bridge's mapped centreline
— 72 OSM points across 6 ways, principal axis by closed-form 2×2 eigenvector — and publishes it as
**provisional, grade D**, tagged `OQ-009` / `DOQ-001`.

Result: azimuth 157.37° toward Brooklyn, centroid −73.990778 / 40.707604, mapped extent 1,891 m against
the bridge team's own `CTL-001` total of 2,089 m. Consistent, and explicitly not survey truth.

### 5.4 The NTA is the wrong boundary

NYC NTA `BK0202` is *Downtown Brooklyn-DUMBO-Boerum Hill* and covers far more than this subject. It is
registered as `DSRC-004` for context and deliberately not used. The boundary is a documented project
definition following real streets and the shoreline.

---

## 6. The viewer-mode recommendation

**Build one viewer kernel and two mode shells.** Ship the kernel from the shared repository; each
project ships only its own UI and its own data.

Three mechanisms carry it:

1. **One frame.** Both modules express coordinates in `nyc-harbor-enu`. A module with a private
   engineering frame keeps it and publishes a `placement`. Vertical datums are declared, never assumed.
2. **One LOD axis, two budgets.** Every level from both modules sits on `max_geometric_error_m`;
   selection is screen-space error. A mode is a budget: `{ inspect: 2, walk: 12, map: 48, tour: 8 }`.
   That single number is the entire difference between CAD inspection and an efficient walkthrough.
   You do not need two viewers; you need two numbers.
3. **Proxy and handoff.** A module publishes a cheap stand-in with a consumer-enforced `max_level`
   cap, and named entry points for promoting it to full inspection with the camera preserved.

Full argument, including why the alternatives are worse:
`digital-3d-shared-contracts/VIEWER-MODES.md`.

---

## 7. Asks of the Manhattan Bridge team

Detailed in [BRIDGE-TEAM-COORDINATION.md](BRIDGE-TEAM-COORDINATION.md). In priority order:

| | Action | Effort |
|---|---|---|
| 1 | Publish `bridge-manifest.json` | ~60 lines; a placeholder is written and ready to copy |
| 2 | Export a level-2 proxy GLB | one decimation pass; highest-value artefact for the district |
| 3 | Adopt `MHW = NAVD88 + 0.59 m` | one field; no geometry changes |
| 4 | Ratify or correct the provisional placement | one line, or a survey |
| 5 | Map `parts.json` onto `metadata.schema.json` | field renames; HO fields move under `extensions` |
| 6 | Consider `@d3d/viewer-kernel` at Milestone 5 | optional |

Items 1 and 2 alone make the integration real. Nothing on this list requires the bridge team to change
their coordinate frame, their geometry, their build scripts, or their confidence model — the shared
evidence model was generalised *from* theirs precisely so adoption regrades nothing.

---

## 8. Known limitations

Stated plainly; all are recorded in the manifest's `not_implemented_yet`.

| | Tracked |
|---|---|
| No terrain from DEM or LiDAR; ground is interpolated, grade C | `DOQ-003`, `DSRC-008` |
| Bridge placement is provisional and unratified | `DOQ-001`, `OQ-009` |
| All roofs are flat at the dataset roof height | folded into each level's declared error |
| Shoreline not modelled; water is a plane at MHW with a visible seam | `DUMBO-SCOPE.md` §4 |
| MHW offset transferred from The Battery, ~3 km away, not VDatum-derived locally | `DOQ-004` |
| Boundary drawn by inspection, not traced from a cadastral source | `DOQ-005` |
| No collision; you can walk through walls | — |
| No photogrammetry, no interiors, no vegetation, no textures | out of scope for Phase 1 |
| Brooklyn Bridge is not owned by any module and is not rendered | — |

---

## 9. Phase 2 candidates

1. **Ingest NYC DEM and LiDAR** (`DSRC-008`). Retires `DOQ-003` and promotes ground from C to A.
2. **Consume a real bridge proxy** and delete the placeholder.
3. **Shoreline and waterfront surfaces**, replacing the infinite water plane.
4. **Roof forms from LiDAR**, which would justify a mesh-based LOD0 and tighten its declared error.
5. **A tour-recording harness** — the `FrameLoop` hidden-document fallback already makes headless
   capture work, so rendering a tour to video is close.
6. **Adapter for a real directions provider**, replacing `route_leg` in `build_tour.py`.
7. **District photogrammetry**, once the geospatial control layer it must align to is stable.
