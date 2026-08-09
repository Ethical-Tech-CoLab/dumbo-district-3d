# DUMBO Source Register

Every external source this module depends on, what it is allowed to justify, and what it costs in
licensing terms.

The machine-readable form is generated to
`viewer/public/district/source-register.json` by `scripts/build_district_assets.py` and conforms to
`source-confidence.schema.json`. This file is the human-readable companion; if the two disagree, the
generated file is what the build actually used.

---

## 1. Grades

| Grade | Definition |
|---|---|
| **A** | Derived from an authoritative published dataset, an official dimension, or an archival drawing. |
| **B** | Derived from consistent imagery or community mapping combined with known control geometry. |
| **C** | Derived from a reconstructed attribute, an aligned mesh, or photogrammetry. |
| **D** | Inferred, decorative, or placeholder. Never citable as a dimension. |

**Rule of the weakest link.** An asset's confidence is the minimum of its own basis grade and the
grade of every control value it consumes. `scripts/build_district_assets.py` enforces this.

**Tier rule.** No Tier C source may override Tier A geometry.

---

## 2. Register

### DSRC-001 — NYC Building Footprints · Tier A · grants A

| | |
|---|---|
| Publisher | NYC Office of Technology and Innovation, via NYC Open Data |
| Endpoint | `https://data.cityofnewyork.us/resource/5zhs-2jue.json` |
| Accessed | 2026-08-09 |
| License | NYC Open Data Terms of Use |
| Attribution | **Required** — "Building footprints: NYC Open Data (OTI)" |
| Native CRS | EPSG:2263 (NY State Plane, Long Island East, NAD83, US foot); published as EPSG:4326 |
| Vertical datum | NAVD88 |
| Units | feet |
| Positional accuracy | ±0.61 m (publisher's stated ±2 ft, ASPRS Class 1, for `geom_source = Photogrammetric`) |
| Verified | Yes |

Supplies plan geometry, `height_roof` and `ground_elevation` for all 381 buildings.

**The attribute trap, stated once so nobody falls into it:** `height_roof` is the height of the roof
*above that building's `ground_elevation`*, not an elevation. `ground_elevation` is the lowest
elevation at the building's ground level, in NAVD88 feet. Reading `height_roof` as an elevation
produces buildings that float or sink by up to 23 m across this district. See
[DUMBO-GEOSPATIAL-CONTROL.md](DUMBO-GEOSPATIAL-CONTROL.md) §5.

### DSRC-002 — NYC MapPLUTO / PLUTO · Tier A · grants A

| | |
|---|---|
| Publisher | NYC Department of City Planning, via NYC Open Data |
| Endpoint | `https://data.cityofnewyork.us/resource/64uk-42ks.json` |
| Accessed | 2026-08-09 |
| License | NYC Open Data Terms of Use |
| Attribution | **Required** — "Lot attributes: NYC Department of City Planning (PLUTO)" |
| Verified | Yes |

Attributes only: address, owner, building class, land use, floors, units, year built, lot area,
zoning. Joined to footprints on `mappluto_bbl`; 375 of 381 buildings matched.

**Never a geometry source.** Floor counts may reconstruct a missing height via `DCTL-062`, which
demotes that building to grade C. No building in this district needed it.

### DSRC-003 — NYC building footprint published metadata · Tier A · grants A

| | |
|---|---|
| Publisher | City of New York, `nyc-geo-metadata` |
| URL | `https://github.com/CityOfNewYork/nyc-geo-metadata/blob/main/Metadata/Metadata_BuildingFootprints.md` |
| Accessed | 2026-08-09 |
| Verified | Yes |

The authority for what the attributes in DSRC-001 actually mean, and the origin of the `height_roof`
and `ground_elevation` definitions above and of the ±2 ft accuracy figure.

### DSRC-004 — NYC Neighborhood Tabulation Areas 2020 · Tier A · grants A · context only

| | |
|---|---|
| Publisher | NYC Department of City Planning, via NYC Open Data |
| Endpoint | `https://data.cityofnewyork.us/resource/9nt8-h7nd.json` |
| Accessed | 2026-08-09 |
| Verified | Yes |

NTA `BK0202` is named *Downtown Brooklyn-DUMBO-Boerum Hill* and covers far more than this project's
subject. Retained as context; **deliberately not used as the district boundary**, which is the
project definition in `DUMBO-GEOSPATIAL-CONTROL.md` §2.1 instead.

### DSRC-005 — NOAA CO-OPS tidal datums, station 8518750 (The Battery, NY) · Tier A · grants A

| | |
|---|---|
| Publisher | NOAA National Ocean Service |
| Endpoint | `https://api.tidesandcurrents.noaa.gov/mdapi/prod/webapi/stations/8518750/datums.json` |
| Accessed | 2026-08-09 |
| License | Public domain (US Government work) |
| Verified | Yes |

Epoch 1983–2001, relative to station datum, in meters: `MHW = 2.44`, `NAVD88 = 1.85`, `MSL = 1.78`,
`MLLW = 1.00`.

Yields `DCTL-010`: **MHW = NAVD88 + 0.59 m**. This is the value that reconciles this module's NAVD88
data with `manhattan-bridge-3d`, whose geometry is authored against mean high water. Without it the
bridge sits 0.59 m into the ground it lands on.

### DSRC-006 — NYSAPLS tide datum reference for station 8518750 · Tier B · grants B

| | |
|---|---|
| Publisher | New York State Association of Professional Land Surveyors |
| Accessed | 2026-08-09 |
| License | Conference handout; cited, not redistributed |
| Verified | Yes |

Independent corroboration: MHW 0.596 m above NAVD88 at the same station. Agrees with DSRC-005 to
6 mm. Recorded because a datum offset that reconciles two repositories deserves two sources.

### DSRC-007 — OpenStreetMap · Tier B · grants B

| | |
|---|---|
| Publisher | OpenStreetMap contributors, via Overpass API |
| Accessed | 2026-08-09 |
| License | **ODbL-1.0** |
| Attribution | **Required, unconditional** — "© OpenStreetMap contributors" |
| Native CRS | EPSG:4326 |
| Verified | Yes |

Supplies the pedestrian walk network (4,346 nodes, 5,131 edges), 29 named landmarks used as tour
targets and map labels, and the Manhattan Bridge centreline used to derive the provisional bridge
placement.

ODbL attribution is mandatory. The viewer displays it unconditionally, whether or not OSM-derived
geometry is currently on screen. Community mapping is graded B: it is consistent and well maintained,
but it is not a survey, and it is never used as control geometry.

### DSRC-008 — NYC digital elevation model and LiDAR · Tier A · grants A · NOT YET INGESTED

| | |
|---|---|
| Publisher | NYC Office of Technology and Innovation |
| License | NYC Open Data Terms of Use |
| Verified | **No — not yet fetched** |

Registered deliberately while unused, so that `DOQ-003` names a real remedy rather than a wish. When
ingested it replaces the interpolated ground surface and promotes it from C to A.

### DSRC-009 — NYC Forestry Management System street trees · Tier A · grants A

| | |
|---|---|
| Publisher | NYC Parks, via NYC Open Data |
| Accessed | 2026-08-09 |
| License | NYC Open Data Terms of Use |
| Attribution | **Required** — "Street trees: NYC Parks Forestry Management System" |
| Native CRS | EPSG:4326 · dbh in inches |
| Verified | Yes |

1,252 street trees inside the district, each with position, species and trunk diameter. Position and
species are grade A. The **rendered canopy is graded C**: it is a procedural form chosen per genus and
scaled by dbh, not a measured crown. A real tree in the right place with an approximated shape — which
is exactly the distinction the grade is there to record.

### DSRC-010 — NYC planimetric sidewalk polygons · Tier A · grants A · NOT YET INGESTED

| | |
|---|---|
| Publisher | NYC Office of Technology and Innovation, via NYC Open Data |
| License | NYC Open Data Terms of Use |
| Verified | **No — not yet fetched** |

`scripts/ingest_sources.py::fetch_sidewalks` is written and targets dataset `vfx9-tbb6`, but the
paving currently shipped is derived from the walk network rather than from these polygons. Registered
so the gap is visible: paved surfaces are grade C until this is ingested.

### DSRC-011 — NYC Building Footprints, Lower Manhattan frontage · Tier A · grants A

| | |
|---|---|
| Publisher | NYC Office of Technology and Innovation, via NYC Open Data |
| Accessed | 2026-08-09 |
| License | NYC Open Data Terms of Use |
| Native CRS | EPSG:2263 · NAVD88 · feet |
| Verified | Yes |

The same dataset as DSRC-001, queried across the river for the skyline visible from DUMBO. Delivered
as extruded silhouette blocks, so the **rendered geometry is graded B** even though the source is A.
See `DOQ-008`.

The query box overlaps the district, so the builder subtracts every block whose centroid falls inside
the district boundary and every block within 700 m of it. Without that subtraction 226 buildings were
drawn twice — once as real surveyed geometry and again as pale far-field blocks floating on top of
them. See the anti-duplication rule in the shared `GOVERNANCE.md`.

### DSRC-012 — OpenStreetMap ferry routes and terminals · Tier B · grants B

| | |
|---|---|
| Publisher | OpenStreetMap contributors, via Overpass API |
| Accessed | 2026-08-09 |
| License | **ODbL-1.0** |
| Attribution | **Required, unconditional** — "© OpenStreetMap contributors" |
| Verified | Yes |

East River ferry route lines and landings, including the real Pier 11 ↔ DUMBO/Fulton Ferry service.
Vessels follow these true route lines; their **speed and spacing are nominal, not a timetable**, and
seasonal small craft are decorative grade D. See `DOQ-009`.

---

## 3. Definition sources

`DEF-001` … `DEF-007` are project decisions, not external evidence: the frame anchor, the boundary,
the hero zone, the tile tuning, pedestrian ergonomics, unit constants, and the ground interpolation
parameters. They are listed in [DUMBO-GEOSPATIAL-CONTROL.md](DUMBO-GEOSPATIAL-CONTROL.md) §8 and
exist so that `source_basis` is never empty and a reader can always tell a decision from a
measurement.

---

## 4. Attribution the viewer must display

Collected by `ModuleRegistry.attributions()` and rendered unconditionally in the viewer footer:

```
Building footprints and lot data: NYC Open Data (OTI, DCP)
Street trees: NYC Parks Forestry Management System
© OpenStreetMap contributors, ODbL
Tidal datums: NOAA CO-OPS station 8518750
Manhattan Bridge digital twin: manhattan-bridge-3d, Ethical Tech CoLab, CC BY 4.0
```

Basemap layers add their own line while a layer is selected, because those terms are the provider's
and apply only when their tiles are on screen. See `BASEMAP-LAYERS.md` in
`digital-3d-shared-contracts`.

---

## 5. Audit trail

Every fetch writes a sidecar next to its payload recording the exact query, timestamp, byte count and
SHA-256:

```
data/boundaries/nta-bk0202.raw.json          + .source.json
data/footprints/footprints.raw.json          + .source.json
data/horizon/manhattan-skyline.raw.json      + .source.json
data/pluto/pluto.raw.json                    + .source.json
data/streets/osm-ways.raw.json               + .source.json
data/streets/osm-landmarks.raw.json          + .source.json
data/streets/osm-manhattan-bridge.raw.json   + .source.json
data/streetscape/trees.raw.json              + .source.json
data/streetscape/ferry.raw.json              + .source.json
```

Re-run `python scripts/ingest_sources.py --all` to refresh. The sidecars are what make this register
auditable rather than aspirational.
