# DUMBO District Digital Twin

A walkable, browser-renderable digital twin of the DUMBO neighbourhood in Brooklyn, built from
authoritative NYC open data, with every asset traceable to a registered source.

Interoperates with [`manhattan-bridge-3d`](../manhattan-bridge-3d) through
[`digital-3d-shared-contracts`](../digital-3d-shared-contracts). **It contains no bridge geometry.**

---

## What it is

- **381 buildings**, every one grade A, from NYC Building Footprints joined to PLUTO
- **112 tiles** across four LOD levels, streamed by screen-space error
- **4,346-node pedestrian network** from OpenStreetMap, used for walking and tour routing
- **1,252 street trees** from the NYC Forestry census, instanced in 30 draw calls
- **1,986 paved surfaces** with kerbs, and procedural facades from PLUTO class and year
- **An interpolated ground surface**, because DUMBO climbs 23 m from the waterfront
- **A first-person walk mode, a map mode with terrain/street/satellite basemaps, and tour playback**
- **446 KB** of tile payloads for the entire district

---

## Quick start

```bash
# 1. Fetch the sources  (NYC Open Data + Overpass; a few minutes)
python scripts/ingest_sources.py --all

# 2. Build boundary, hero zone and tile grid
python scripts/build_boundaries.py

# 3. Build buildings, tiles, ground, walk network and all published documents
python scripts/build_district_assets.py

# 4. Derive a provisional Manhattan Bridge placement and a placeholder manifest
python scripts/propose_bridge_placement.py --write

# 5. Build walk-mode scene dressing: trees, paving, facades
python scripts/build_scene_dressing.py

# 6. Generate the demonstration tours
python scripts/build_tour.py

# 7. Run it
cd viewer && npm install && npm run dev     # http://localhost:5178
```

Python 3.12, standard library only. Node 20+ for the viewer.

Press **▶ DUMBO in an hour — a family of four** to watch a four-stop walking tour drive itself.

---

## Documents

| | |
|---|---|
| [AGENT-INSTRUCTIONS.md](AGENT-INSTRUCTIONS.md) | The original brief. |
| [DUMBO-SCOPE.md](DUMBO-SCOPE.md) | What this module owns, and what it deliberately does not. |
| [DUMBO-GEOSPATIAL-CONTROL.md](DUMBO-GEOSPATIAL-CONTROL.md) | **Source of truth for every coordinate, datum and boundary.** Parsed by the build. |
| [DUMBO-SOURCE-REGISTER.md](DUMBO-SOURCE-REGISTER.md) | Every external source, its licence, and what it may justify. |
| [DUMBO-LOD-STRATEGY.md](DUMBO-LOD-STRATEGY.md) | How geometry is spent, and why the ladder is shared. |
| [DUMBO-MAP-VIEWER-INTEGRATION.md](DUMBO-MAP-VIEWER-INTEGRATION.md) | Viewer architecture. |
| [TOUR-DIRECTOR.md](TOUR-DIRECTOR.md) | Driving the viewer from external walking instructions. |
| [MILESTONES.md](MILESTONES.md) | **What comes next, and why in that order.** |
| [BRIDGE-TEAM-COORDINATION.md](BRIDGE-TEAM-COORDINATION.md) | **What the Manhattan Bridge team needs to do.** |
| [BRIDGE-TEAM-PROMPT.md](BRIDGE-TEAM-PROMPT.md) | The same, as a drop-in agent prompt with exact URL structure. |
| [IMPLEMENTATION-REPORT.md](IMPLEMENTATION-REPORT.md) | Phase 1 report. |

---

## The control document is the source of truth

`DUMBO-GEOSPATIAL-CONTROL.md` is parsed by `scripts/district_control.py`. The scripts carry no
dimensional constants of their own. If a number is not in a control table it does not exist in the
model; to change the model, change the document and rebuild.

This mirrors `manhattan-bridge-3d/GEOMETRY-CONTROL.md` on purpose: two repositories, one method.

```bash
python scripts/district_control.py --check-frame
```

```
ENU round-trip error        : 0.0000 mm
flat-plane drop at DCTL-004 : 1.257 m (declared 1.260 m)
OK: frame is within its declared bounds
```

---

## Layout

```
DUMBO-*.md                 governance; the control document is machine-parsed
scripts/
  district_control.py      parses the control document; rigorous geodetic ↔ ENU
  ingest_sources.py        NYC Open Data + Overpass, with audit sidecars
  build_boundaries.py      boundary, hero zone, tile grid
  build_district_assets.py buildings, tiles, ground, walk network, all documents
  build_scene_dressing.py  street trees, paving surfaces, facade appearance
  propose_bridge_placement.py  provisional bridge georeference (OQ-009 / DOQ-001)
  build_tour.py            A* router that emits directions-shaped tour scripts
data/                      raw fetched sources + .source.json audit sidecars
viewer/
  src/                     the shell: three.js, walk controls, router, components
  public/district/         published: manifest, georeference, ladder, registry,
                           tile index, source register, tiles, ground, walk network,
                           basemap layers, props, paving, facades
  public/frames/           byte-identical copy of the canonical shared frame
  public/tours/            demonstration tour scripts
  public/modules/          placeholder bridge manifest (delete when they publish)
```

---

## Interoperation in one paragraph

The district publishes `district-manifest.json`. It declares an optional dependency on
`manhattan-bridge`. Twelve tiles list `urn:d3d:manhattan-bridge:bridge_proxy` in `foreign_assets`, so
whenever a visitor can see the bridge, the bridge module's content is kept resident — capped at the
`max_level` the bridge team declares. If their manifest is absent the viewer logs it, shows an
integration notice, draws a labelled placeholder, and carries on. No bridge geometry is ever stored
here.

---

## Validation

```bash
# Documents against the shared schemas
cd ../digital-3d-shared-contracts && npm install
node tools/validate.mjs ../dumbo-district-3d/viewer/public/district/*.json
node tools/validate.mjs --schema tour-script ../dumbo-district-3d/viewer/public/tours/dumbo-*.json

# Control document and frame
python scripts/district_control.py --check-frame

# Viewer
cd viewer && npm run build
```

---

## Attribution

- Building footprints and lot data: NYC Open Data (OTI, DCP)
- © OpenStreetMap contributors, ODbL
- Tidal datums: NOAA CO-OPS station 8518750 (The Battery, NY)
