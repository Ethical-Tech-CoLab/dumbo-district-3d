# DUMBO Geospatial Control

**This file is the source of truth for every coordinate, datum and boundary in this repository.**

`scripts/district_control.py` *parses this file*. It carries no copy of any value. If a number is not in a
control table below, it does not exist in the model. To change the model, change this file.

This mirrors the discipline of `manhattan-bridge-3d/GEOMETRY-CONTROL.md` deliberately: two repositories, one
method, so a reviewer can move between them without relearning anything.

---

## 1. Scene frame

The district is authored **directly in the shared scene frame**. It has no private engineering frame and
therefore publishes no `placement` in its module manifest. The Manhattan Bridge, which *is* authored in a
private frame, publishes one. See `digital-3d-shared-contracts/COORDINATE-SYSTEM.md`.

| Item | Definition |
|---|---|
| Frame ID | `nyc-harbor-enu` |
| Kind | Local East-North-Up tangent plane |
| World units | meters |
| +X | East |
| +Y | North |
| +Z | Up |
| Handedness | right-handed, Z-up |
| Render conversion | `(x, y, z) -> (x, z, -y)` to reach glTF / three.js Y-up |
| Horizontal CRS of the anchor | EPSG:4326 (WGS84) |
| Vertical datum of the frame | NAVD88 |

### 1.1 Frame anchor

| Control ID | Key | Value | Unit | Source IDs | Confidence | Notes |
|---|---|---:|---|---|---|---|
| DCTL-001 | frame_anchor_lon | -73.9890 | deg | DEF-001 | A | Frozen by convention. |
| DCTL-002 | frame_anchor_lat | 40.7030 | deg | DEF-001 | A | Frozen by convention. |
| DCTL-003 | frame_anchor_height | 0 | m | DEF-001 | A | NAVD88 zero. |
| DCTL-004 | frame_valid_radius | 4000 | m | DEF-001 | A | Radius within which this frame may be used. Covers the district, both bridges and the far shore. |
| DCTL-005 | frame_flat_plane_drop | 1.26 | m | DEF-001 | A | How far the WGS84 surface falls below the frame's flat z=0 plane at DCTL-004. Verified by `scripts/district_control.py --check-frame`. |

The anchor is a **convention, not a survey monument**. It was chosen as a round-numbered point near the
district centroid so that DUMBO coordinates are small positive and negative numbers, which keeps float32
precision comfortable in the browser. Confidence `A` records that it is *exactly defined*, not that it was
measured. Once published it is frozen: every asset coordinate in every consuming module depends on it.

The geodetic to ENU conversion in `scripts/district_control.py` is **rigorous**: WGS84 geodetic to ECEF,
then a rotation into the local East-North-Up basis. It is not a small-angle approximation, and it
round-trips to better than a micrometre across the whole validity radius. The single thing a flat scene
still loses is that the curved Earth falls away from the frame's `z = 0` plane with distance, which is
exactly what DCTL-005 quantifies: 1.26 m of drop at 4 km, 0.08 m at 1 km, and 0.02 m across a 500 m walk.
Within the district that is smaller than the +/- 0.61 m positional accuracy of the footprint source
(`DSRC-003`), so a flat ground plane is defensible here and is recorded as such rather than assumed.

### 1.2 Vertical datum reconciliation

This is the one genuinely hard interoperation problem between the two repositories, and it is solved here.

* NYC authoritative data (`DSRC-001`, `DSRC-002`) is **NAVD88**, in US survey feet.
* `manhattan-bridge-3d/GEOMETRY-CONTROL.md` sets `z = 0` at **mean high water (MHW)**.

Those are different surfaces. Placing the bridge in the district without correcting for this puts the whole
structure roughly 0.6 m out vertically, which is larger than the positional accuracy of the footprint data
and would be visible where the bridge meets the anchorage plaza.

| Control ID | Key | Value | Unit | Source IDs | Confidence | Notes |
|---|---|---:|---|---|---|---|
| DCTL-010 | mhw_above_navd88 | 0.59 | m | DSRC-005, DSRC-006 | A | MHW minus NAVD88 at NOAA 8518750 (The Battery, NY), tidal epoch 1983-2001. |
| DCTL-011 | msl_above_navd88 | -0.07 | m | DSRC-005 | A | Mean sea level minus NAVD88, same station and epoch. |
| DCTL-012 | mllw_above_navd88 | -0.85 | m | DSRC-005 | A | Mean lower low water minus NAVD88, same station and epoch. |

Derivation of DCTL-010, from the NOAA CO-OPS published datums for station 8518750 relative to that station's
own datum, epoch 1983-2001, in meters: `MHW = 2.44`, `NAVD88 = 1.85`. Therefore `MHW - NAVD88 = 0.59 m`.
Independently corroborated by `DSRC-006`, which publishes `MHW = 0.596 m` above NAVD88 for the same station.

**Conversion rule.** To express a bridge elevation `z_mhw` in the district frame:

```
z_navd88 = z_mhw + 0.59
```

The shared georeference document carries these numbers in `vertical_datum_offsets_m` so that no consumer has
to rediscover them, and so the bridge team can adopt the correction by reading a field rather than by
editing geometry.

The Battery is roughly 3 km from the district anchor. Tidal datum separation varies slowly along the East
River, so applying a single Battery-derived offset across DUMBO introduces an error well under the 0.6 m it
corrects. This is recorded as `DOQ-004` and should be retired with a VDatum-derived local offset before any
`A`-grade vertical claim is made about the waterfront.

---

## 2. District boundary

The project boundary is a **scope definition**, not a legal or administrative boundary.

NYC's Neighborhood Tabulation Area `BK0202` is named *Downtown Brooklyn-DUMBO-Boerum Hill* and is far larger
than this project's subject; it is recorded as context in `DSRC-004` but is **not** used as the boundary.
The boundary below follows recognisable physical edges: the East River shoreline on the north, the Brooklyn
Navy Yard wall on the east, York and Sands Streets on the south, and Furman Street and the Brooklyn Bridge
Park piers on the west.

**Extended 2026-08-10, and this time the extent is checked rather than judged.** The previous boundary ran
along Bridge Street and stopped at Fulton Ferry, which was drawn by eye and looked reasonable. Tested against
the city's landmark building register (`DSRC-018`) it excluded **eleven designated buildings**: ten of them on
Hudson Avenue — 49 through 59, the Greek Revival row that *is* the Vinegar Hill Historic District — and
10 Jay Street on the DUMBO shoreline. A district that leaves out the landmarked core of a neighbourhood it
claims to cover is drawn wrong, and the register is what said so.

The west and south edges now take in Brooklyn Bridge Park down to Pier 6, which is where the waterfront a
visitor actually walks ends rather than where the previous polygon happened to stop.

| Control ID | Key | Value | Unit | Source IDs | Confidence | Notes |
|---|---|---:|---|---|---|---|
| DCTL-020 | boundary_west | -74.0045 | deg | DEF-002 | A | Bounding longitude, definition. Extended west to enclose Brooklyn Bridge Park Piers 1-6. |
| DCTL-021 | boundary_east | -73.9780 | deg | DEF-002 | A | Bounding longitude, definition. Extended east to the Navy Yard wall so Vinegar Hill is whole. |
| DCTL-022 | boundary_south | 40.6915 | deg | DEF-002 | A | Bounding latitude, definition. Extended south to Pier 6 at Atlantic Avenue. |
| DCTL-023 | boundary_north | 40.7060 | deg | DEF-002 | A | Bounding latitude, definition. |

The boundary polygon itself is `data/boundaries/dumbo-district.geojson`, generated by
`scripts/build_boundaries.py` from the vertex table in section 2.1. The bounding box above is the query
envelope used against the source APIs; the polygon is what actually clips the data.

### 2.1 Boundary vertices

Ordered clockwise as seen on a north-up map, closing implicitly. Column contract:
`Vertex ID | Longitude | Latitude | Along`.

Nothing depends on the winding — the point-in-polygon test is even-odd, so it is orientation-agnostic —
but the direction is stated because it was previously documented as counter-clockwise and was not, and a
reader checking one against the other deserves the table to be right.

| Vertex ID | Longitude | Latitude | Along |
|---|---:|---:|---|
| DBV-01 | -73.99750 | 40.70400 | Fulton Ferry Landing, west end of the shoreline edge |
| DBV-02 | -73.99500 | 40.70490 | Shoreline at Brooklyn Bridge Park Main Street lot |
| DBV-03 | -73.99000 | 40.70520 | Shoreline east of the Manhattan Bridge |
| DBV-04 | -73.98400 | 40.70500 | Shoreline at the John Street / Navy Yard edge |
| DBV-05 | -73.97950 | 40.70420 | Vinegar Hill shoreline at the Navy Yard wall |
| DBV-06 | -73.97850 | 40.70050 | Navy Street, east edge of Vinegar Hill |
| DBV-07 | -73.98450 | 40.69930 | York Street at Bridge Street |
| DBV-08 | -73.98800 | 40.69870 | York Street at Jay Street |
| DBV-09 | -73.99120 | 40.69880 | Sands Street at the Manhattan Bridge approach |
| DBV-10 | -73.99450 | 40.69990 | Prospect Street at the Brooklyn Bridge approach |
| DBV-11 | -73.99720 | 40.70140 | Old Fulton Street, west edge |
| DBV-12 | -73.99880 | 40.69950 | Brooklyn Heights bluff above Furman Street |
| DBV-13 | -74.00060 | 40.69600 | Furman Street at Joralemon Street |
| DBV-14 | -74.00280 | 40.69220 | Atlantic Avenue at the Pier 6 landing |
| DBV-15 | -74.00420 | 40.69300 | Pier 6, outer end |
| DBV-16 | -74.00300 | 40.69700 | Pier 5 and Pier 4 outer ends |
| DBV-17 | -74.00120 | 40.70000 | Pier 3 and Pier 2 outer ends |
| DBV-18 | -73.99900 | 40.70280 | Pier 1 outer end, turning back to Fulton Ferry |

### 2.2 Hero fidelity zone

The hero zone is where LOD0 is built. It is the set of places a visitor actually stands and looks, and it is
deliberately small: it is the only zone whose cost is allowed to be high.

| Control ID | Key | Value | Unit | Source IDs | Confidence | Notes |
|---|---|---:|---|---|---|---|
| DCTL-030 | hero_corridor_halfwidth | 60 | m | DEF-003 | A | Half-width of the hero corridor around each hero centerline. |

Hero centerlines. Column contract: `Hero ID | Name | Lon A | Lat A | Lon B | Lat B`.

| Hero ID | Name | Lon A | Lat A | Lon B | Lat B |
|---|---|---:|---:|---:|---:|
| DHZ-01 | Washington Street view corridor | -73.99070 | 40.70250 | -73.99000 | 40.70430 |
| DHZ-02 | Water Street | -73.99400 | 40.70320 | -73.98650 | 40.70260 |
| DHZ-03 | Main Street to the waterfront | -73.99330 | 40.70180 | -73.99420 | 40.70430 |
| DHZ-04 | Brooklyn Bridge Park waterfront | -73.99600 | 40.70380 | -73.99000 | 40.70460 |
| DHZ-05 | Manhattan Bridge Brooklyn approach | -73.99000 | 40.70050 | -73.98850 | 40.70420 |
| DHZ-06 | Old Fulton Street to Fulton Ferry | -73.99560 | 40.70200 | -73.99720 | 40.70320 |

---

## 3. Tile grid

| Control ID | Key | Value | Unit | Source IDs | Confidence | Notes |
|---|---|---:|---|---|---|---|
| DCTL-040 | tile_size | 128 | m | DEF-004 | A | Square tiles in scene ENU meters. |
| DCTL-041 | tile_load_radius | 420 | m | DEF-004 | A | Streaming manager keeps tiles within this radius resident. |
| DCTL-042 | tile_unload_radius | 640 | m | DEF-004 | A | Hysteresis band against thrashing at the load boundary. |
| DCTL-043 | tile_prefetch_ahead | 260 | m | DEF-004 | A | Extra prefetch along the camera heading. A tour player raises this because it knows the route. |

Tile size is a tuning decision, not a measurement. 128 m at DUMBO's density yields on the order of ten to
thirty buildings per tile, which keeps a single tile payload small enough to fetch inside one walking second
at DCTL-052 pace while still amortising draw calls.

---

## 4. Pedestrian model

Used by walk mode and by the tour player when a tour does not specify its own pace.

| Control ID | Key | Value | Unit | Source IDs | Confidence | Notes |
|---|---|---:|---|---|---|---|
| DCTL-050 | eye_height_adult | 1.70 | m | DEF-005 | B | Nominal adult standing eye height. Raised from 1.65 on 2026-08-14. Both values are defensible -- US anthropometric means run about 1.63 m for women and 1.73 m for men -- but 1.65 sat at the low end of that range, and the viewer is one notional adult rather than a population. The visible symptom was waist-high guard rails reading as barriers; that had a separate and larger cause (surveyed barrier heights were being ignored, DSRC-016), fixed at the same time. |
| DCTL-051 | eye_height_child | 1.15 | m | DEF-005 | B | Nominal eye height for a child of about eight. |
| DCTL-052 | walk_pace_default | 1.30 | m/s | DEF-005 | B | Unhurried adult pace. |
| DCTL-053 | walk_pace_family | 1.05 | m/s | DEF-005 | B | Group containing children, sightseeing rather than commuting. |
| DCTL-054 | walk_pace_max | 2.20 | m/s | DEF-005 | B | Clamp for free-fly walking, so the streaming manager is never outrun. |
| DCTL-055 | walk_pace_sprint | 6.60 | m/s | DEF-005 | B | Clamp while shift is held. Three times the old ceiling, and deliberately faster than a person runs: covering the district quickly is a navigation need, not a claim about pedestrians. Safe to raise now only because a tile that fails to arrive is retried with backoff rather than leaving a permanent hole, and because the streamer prefetches along the heading. |

---

## 5. Building height model

Footprint records carry `height_roof` and `ground_elevation`. Their meanings are precise and easy to get
wrong, so they are stated here once, from the publisher's own metadata (`DSRC-003`):

* `ground_elevation` — lowest elevation at the building ground level, **NAVD88**, in feet.
* `height_roof` — height of the roof **above that ground elevation**, in feet. It is *not* an elevation.

Therefore, in the district frame:

```
base_z_m  = ground_elevation_ft * 0.3048
roof_z_m  = base_z_m + height_roof_ft * 0.3048
```

| Control ID | Key | Value | Unit | Source IDs | Confidence | Notes |
|---|---|---:|---|---|---|---|
| DCTL-060 | us_survey_foot | 0.3048006096 | m | DEF-006 | A | Exact US survey foot, for EPSG:2263 native coordinates. |
| DCTL-061 | international_foot | 0.3048 | m | DEF-006 | A | Exact international foot. Applied to the published attribute values, which are plain feet. |
| DCTL-062 | fallback_storey_height | 3.5 | m | DEF-006 | D | Used only when `height_roof` is zero or null AND PLUTO supplies a floor count. Any building using this is graded `C`; see DOQ-002. |
| DCTL-063 | fallback_building_height | 9.0 | m | DEF-006 | D | Last-resort height when neither a roof height nor a floor count exists. Any building using this is graded `D`. |

---

## 6. Ground surface

DUMBO is not flat. Building base elevations across the district run from 0 m at the waterfront to
22.9 m at the southern edge, a median of 11.9 m. Treating the ground as the frame's zero plane would
put a walking camera roughly twelve metres underground through most of the district, which is a far
larger error than anything else in this model.

The ground surface is sampled from `DSRC-013`, the USGS 3DEP 1 m **bare-earth** DEM, on the grid
below. Bare earth is the right product: buildings are already removed from it, so what is sampled is
the pavement a walker stands on rather than the roof above them. Elevations arrive in NAVD88 metres,
which is this project's vertical datum, so no transformation is applied and none can be got wrong.

This retires the interpolated surface that stood in until now, and with it `DOQ-003`. The old method
blended `ground_elevation` from each building footprint with inverse-distance weighting: grade `A`
point samples with a grade `C` surface stretched between them. It knew nothing about the street
itself, and nothing at all about open ground away from buildings — which in a waterfront district
with a park along its whole northern edge is a large fraction of where a visitor actually walks.

The interpolation is **retained as a fallback**. If `data/terrain/dem.raw.json` is absent, the build
still produces a ground surface by the old method and grades it `C` again. A clone that has not run
the ingest step still builds and still runs; it just tells the truth about what it is standing on.

| Control ID | Key | Value | Unit | Source IDs | Confidence | Notes |
|---|---|---:|---|---|---|---|
| DCTL-070 | ground_grid_cell | 8 | m | DEF-007 | A | Cell size of the ground height grid. Four times finer than the 32 m used while the surface was interpolated, because a 1 m DEM can carry it and street grade is visible at this spacing. |
| DCTL-071 | ground_idw_radius | 260 | m | DEF-007 | A | Fallback only. Search radius for inverse-distance weighting of building base elevations. |
| DCTL-072 | ground_idw_neighbours | 8 | count | DEF-007 | A | Fallback only. Number of nearest samples blended per cell. |
| DCTL-073 | ground_idw_power | 2 | ratio | DEF-007 | A | Fallback only. Inverse-distance exponent. |
| DCTL-074 | dem_resolution | 1 | m | DSRC-013 | A | Native ground sample distance of the 3DEP source raster. |
| DCTL-075 | dem_bias_max | 0.35 | m | DEF-007 | A | Largest tolerated **signed median** difference between DEM samples and building `ground_elevation`. The systematic-error limit. See section 6.1. |
| DCTL-076 | dem_spread_max | 3.00 | m | DEF-007 | A | Largest tolerated **95th percentile absolute** difference. The registration limit. Necessarily looser than the bias limit, because the two quantities are not identical. See section 6.1. |

Ground height derived from the DEM is graded `A`. If the fallback is used, it is graded `C` and
carries `DOQ-003`.

### 6.1 Why the DEM is trusted

The DEM is not taken on faith. Every build cross-checks it against `ground_elevation` from the
building footprint dataset (`DSRC-003`), which is an independent grade `A` measurement of the same
physical quantity, derived from a different survey by a different agency.

Two statistics are checked, because a wrong datum and a wrong grid registration fail differently and
a single threshold would conflate them:

- **Bias** — the signed median difference, currently **+0.03 m** against a limit of ±0.35 m. A datum
  slip moves every sample the same way, so mistaking MHW for NAVD88 would appear here as a 0.59 m
  offset (`DCTL-010`) and would fail the build. A feet-for-metres error would be off by a factor of
  3.28 and fail enormously. This one number guards both.
- **Spread** — the 95th percentile absolute difference, currently **1.62 m** against a limit of
  3.00 m. Misregistration scatters rather than shifts: a grid offset by one block would drag samples
  onto the wrong side of DUMBO's slope, fanning out the disagreement while the median barely moved.

The spread limit is deliberately looser than the bias limit, because the two quantities are not
quite the same thing. `ground_elevation` is defined as the *lowest* elevation at a building's ground
level, while the DEM is sampled at the building's centroid; a large building on a slope will
legitimately sit below its own centroid sample. Of 374 comparable buildings, 2.4% differ by more
than 2 m and 1.3% by more than 3 m, which is consistent with that definitional difference rather
than with error.

Buildings whose base elevation was itself taken from the DEM are excluded from the comparison. A
value cannot corroborate itself.

### 6.2 Buildings with no registered ground elevation

The footprint dataset encodes an unknown `ground_elevation` as zero rather than null. Seven of the
381 buildings carry that sentinel, and because DUMBO rises to 23 m, taking it at face value planted
them up to **18.7 m below the street they stand on**.

This was invisible before. The old ground surface was interpolated from these same values, so the
error was baked into the very surface that would have been used to check it — a self-referential
check cannot see a defect in its own reference. Adding an independent source is what exposed it.

Such buildings now take their base elevation from the DEM, and record
`ground_elevation_basis: "dem"` so the substitution is visible in the viewer's own metadata panel
rather than hidden in a build log. If no DEM is available they fall back to zero as before, are
graded down, and carry `DOQ-003`.

### 6.3 Land and water

Cells are classified as land or water from `DSRC-014`, the city's own hydrography polygons.

Before, the land mask was the district boundary polygon. That polygon exists to define project scope
and was drawn by inspection (`DOQ-005`); it was never a shoreline. Using it as one meant the terrain
either drowned the waterfront or paved over the river depending on which way the apron erred, and
the piers of Brooklyn Bridge Park — genuinely land standing in water — could not be represented at
all. Land and water is now a sourced distinction rather than a consequence of a scoping decision.

---

## 6.4 Ground surfaces underfoot

What a walker stands on comes from the city's planimetric survey (`DSRC-010`) rather than from
widening a centreline: pavement, carriageway, plaza, park and boardwalk each as their own traced
polygon, and kerbs from surveyed kerb *lines*.

| Control ID | Key | Value | Unit | Source IDs | Confidence | Notes |
|---|---|---:|---|---|---|---|
| DCTL-080 | kerb_height | 0.15 | m | DEF-008 | C | Height the surveyed kerb lines are extruded to. The survey traces the kerb in plan and says nothing about its face, so this is a single conventional value: a standard New York City kerb reveal. The *line* is grade A, the *height* is not, and anything measured against a kerb face inherits the weaker of the two. |

A kerb face is worth its own control because it is the strongest cue for reading a street at eye
level: without one, a pavement is a change of colour and the eye cannot judge distance across it.

### 6.5 Roofs

| Control ID | Key | Value | Unit | Source IDs | Confidence | Notes |
|---|---|---:|---|---|---|---|
| DCTL-081 | parapet_height | 0.90 | m | DEF-009, DSRC-007 | C | Height of the parapet rim above the roof deck on a flat roof. A single conventional value, like the kerb: New York requires a parapet or guard at the edge of an occupiable flat roof, and DUMBO's are visible from the bridge and from across the river. Taken *out of* the building's declared `height_roof` rather than added to it, so the total extent still matches the authoritative measurement and only the roof deck moves down. |

**Roof form was measured before it was modelled, and the measurement retired the question.** Of the
buildings in this district that carry an OpenStreetMap `roof:shape` tag, **73 of 81 are `flat`** — the
remainder being four skillion, two gabled, one hipped and one pyramidal. DUMBO is a flat-roofed
district of masonry warehouses and row houses, and a pitched-roof classifier would have spent its
effort on under ten buildings out of 446.

That is why `DOQ-011` was closed by evidence rather than by acquiring the NYC DCP LOD2 model. The
model would have told us, at considerable cost, what one Overpass query told us for nothing: these
roofs are flat. What the model *would* still add is the rooftop bulkheads, stair houses and tanks
that sit on them, which remains open and is recorded as such.

The parapet is the part that actually reads. A flat roof rendered as a bare plane meets the sky as a
knife edge; a real one has a rim standing about knee-to-waist height above the deck, and that rim is
most of a DUMBO roofline's silhouette from the Manhattan Bridge.

### What the ground is made of

The survey traces *where* the ground is, to the centimetre, and is silent on what it is made of.
OpenStreetMap (`DSRC-007`) tags the material and is vague about where. Joining them keeps the good
half of each: surveyed geometry, tagged material.

Each surveyed polygon takes the `surface` of the nearest OSM way within 18 m — but only from a way
of a compatible class. That restriction matters more than it looks. DUMBO's carriageways are Belgian
block while the pavements beside them are concrete, and a nearest-way join that ignored the
distinction would hand each the other's material and put the cobblestone on the footway. Carriageway
polygons may therefore only take their material from vehicle ways, and pavement and plaza polygons
only from foot ways.

Parks and boardwalks are excluded from the join altogether. A park polygon is the lawn, not the path
crossing it, and a boardwalk is wood by definition; both were being paved over by the nearest footway
before the exclusion was added.

| Kind | Matched | Result |
|---|---:|---|
| roadway | 295 | 205 asphalt · **62 cobblestone** · 14 paving stones · 1 concrete |
| sidewalk | 111 | 53 concrete · 6 paving stones · 5 asphalt |
| plaza | 8 | 3 paving stones · 3 concrete · 1 asphalt |

The geometry stays grade `A`; the material is grade `C`, because a crowd-sourced tag is a claim about
a street, not a measurement of one. The 62 cobbled carriageways fall on fifteen named streets —
Washington, Water, Front, Main, Plymouth, Pearl, Dock, Jay, John, Adams, Gold, Hudson, York,
Elizabeth Place and the Dumbo Archway — which is the Belgian block the district is known for.

---

## 7. Open questions

| ID | Question | Affects | Sources to consult | Status |
|---|---|---|---|---|
| DOQ-001 | Real-world georeference of the Manhattan Bridge model. `manhattan-bridge-3d` OQ-009 leaves the bridge axis azimuth and geodetic anchor unregistered. This repository publishes a **provisional** placement so the bridge can be seen from a DUMBO street; it is explicitly not survey truth. | Bridge placement in the district scene | manhattan-bridge-3d SRC-001, SRC-003; DSRC-007 | Open. Awaiting ratification by the bridge team. |
| DOQ-010 | A consuming module cannot tell that a foreign module's payload has been superseded. The bridge team remodelled the tower arches and finials and republished under the **same `module_version` 1.0.0**, so every field this module checks was unchanged while all seven payload files differed. The district rendered a superseded export for hours and the only symptom was that the bridge looked subtly wrong. | Any foreign module payload | `module-manifest.schema.json`; shared-contracts governance | Open, mitigated locally. `scripts/sync_bridge_module.py --check` compares file digests and runs in CI, so drift is now loud. The real fix belongs in the contract: a manifest needs a content digest, or `module_version` has to be binding on the payload and not just the interface. Raised with the bridge team. |
| DOQ-002 | Buildings whose `height_roof` is zero or null are reconstructed from PLUTO floor counts using DCTL-062. | Building heights | DSRC-001, DSRC-002 | Open, bounded. Affected buildings are graded `C` and listed in the build report. |
| DOQ-003 | No surveyed terrain surface was registered; ground height was interpolated from building base elevations rather than measured. | Terrain, walk mode ground height, tour camera height | DSRC-013 (USGS 3DEP 1 m bare earth) | **Closed** 2026-08-09. Ground is now sampled from a 1 m bare-earth DEM in NAVD88 and graded `A`, cross-checked every build against building `ground_elevation` (section 6.1). Residual: 3DEP at 1 m rather than NYC's own 1 ft DEM (`DSRC-008`), which would be a refinement, not a correction. |
| DOQ-004 | The MHW to NAVD88 offset DCTL-010 is transferred from The Battery, about 3 km away, rather than computed locally with VDatum. | Vertical datum reconciliation | NOAA VDatum | Open, immaterial at current confidence. |
| DOQ-005 | The district boundary in section 2.1 was drawn to follow named streets and the shoreline by inspection, not traced from a cadastral source. | District extent | DSRC-004, NYC planimetrics shoreline | Open, immaterial. It defines project scope only, clips no geometry that another module owns, and no longer doubles as the shoreline now that `DSRC-014` supplies the land/water mask. |
| DOQ-006 | Paved surfaces were derived by widening OSM centrelines with typical half-widths by street class rather than traced from planimetric polygons. | Roadway and sidewalk geometry | DSRC-010 | **Closed** 2026-08-10. Pavement, carriageway, plaza, park and boardwalk are now surveyed planimetric polygons, and kerbs come from surveyed kerb lines. Residual: the kerb *height* applied to those lines is a single conventional value (`DCTL-080`), because the survey traces kerbs in plan only. The centreline method is retained as a fallback when the layers are absent. |
| DOQ-007 | Facade appearance is inferred from PLUTO building class and construction year. It describes the *kind* of building, not the actual facade of that building. | Building appearance in walk mode | Street-level imagery, district photogrammetry | Open. Appearance graded `C`; it never affects geometry or dimensions. |
| DOQ-008 | The Manhattan skyline is reduced to oriented silhouette blocks. Positions and heights are authoritative, but footprint shape is discarded, and buildings below a prominence threshold are omitted entirely. | Far-field appearance across the river | DSRC-011 | Open by design. Graded `B`; never selectable and never dimensionally citable at that range. |
| DOQ-009 | Vessel movement is plausible traffic, not a timetable. Ferries follow real routes at a nominal speed; recreational craft are invented outright. | Water animation | NYC Ferry schedules, AIS vessel tracking | Open. Ferries graded `C`, recreational craft `D`. |
| DOQ-011 | Roof form is not modelled per building: every roof is a flat deck with a parapet. | Rooflines, silhouette from the bridge and across the river | DSRC-007 `roof:shape`; NYC DCP 3-D Building Model; NYC LiDAR DSM | **Closed by measurement** 2026-08-10. Of the buildings here carrying an OSM `roof:shape` tag, **73 of 81 are flat** — the rest being four skillion, two gabled, one hipped and one pyramidal. A pitched-roof classifier would have served under ten of 446 buildings, so the question was retired rather than answered expensively. Residual, recorded honestly: rooftop bulkheads, stair houses and water tanks are still absent, and those are what the DCP LOD2 model would genuinely add. `DOQ-012`. |
| DOQ-012 | Rooftop structures - bulkheads, stair houses, lift overruns and timber water tanks - were not modelled. A DUMBO roofline seen from the Manhattan Bridge has them; ours did not. | Roofline silhouette at middle distance | DSRC-020 NYC DCP 3-D Building Model | **Closed by measurement** 2026-08-13. The DCP model gives 15,322 roof polygons for 8,530 buildings, each planar and at its own height, so a polygon standing inside a lower one is a structure on that roof. **113** of them over the district, 1.3-18.1 m tall, median plan area 32 m2, each carrying a surveyed position, extent, orientation and height. Graded `B`: the survey is authoritative but from 2014, each structure is reduced to its minimum-area rectangle, and *what* each one is was never surveyed - the labels are inferred from proportion and are not a survey claim. |

---

## 8. Definition sources

`DEF-###` entries are project definitions rather than external evidence. They are recorded so that
`source_basis` is never empty and so a reader can tell a decision from a measurement.

| ID | Definition |
|---|---|
| DEF-001 | Scene frame anchor and validity radius, chosen by this project. |
| DEF-002 | District boundary, chosen by this project. |
| DEF-003 | Hero fidelity zone extent, chosen by this project. |
| DEF-004 | Tile grid and streaming tuning, chosen by this project. |
| DEF-005 | Pedestrian ergonomics, nominal values chosen by this project. |
| DEF-006 | Unit constants and reconstruction fallbacks, chosen by this project. |
| DEF-007 | Ground interpolation parameters, chosen by this project. |
