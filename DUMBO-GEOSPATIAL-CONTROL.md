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
The boundary below follows recognisable physical edges: the East River shoreline on the north, Bridge Street
and the Vinegar Hill edge on the east, York and Sands Streets on the south, and Old Fulton Street and the
Brooklyn Bridge approach on the west.

| Control ID | Key | Value | Unit | Source IDs | Confidence | Notes |
|---|---|---:|---|---|---|---|
| DCTL-020 | boundary_west | -73.9985 | deg | DEF-002 | A | Bounding longitude, definition. |
| DCTL-021 | boundary_east | -73.9800 | deg | DEF-002 | A | Bounding longitude, definition. |
| DCTL-022 | boundary_south | 40.6975 | deg | DEF-002 | A | Bounding latitude, definition. |
| DCTL-023 | boundary_north | 40.7060 | deg | DEF-002 | A | Bounding latitude, definition. |

The boundary polygon itself is `data/boundaries/dumbo-district.geojson`, generated by
`scripts/build_boundaries.py` from the vertex table in section 2.1. The bounding box above is the query
envelope used against the source APIs; the polygon is what actually clips the data.

### 2.1 Boundary vertices

Ordered counter-clockwise, closing implicitly. Column contract:
`Vertex ID | Longitude | Latitude | Along`.

| Vertex ID | Longitude | Latitude | Along |
|---|---:|---:|---|
| DBV-01 | -73.99750 | 40.70330 | Fulton Ferry Landing, west end of the shoreline edge |
| DBV-02 | -73.99500 | 40.70450 | Shoreline at Brooklyn Bridge Park Main Street lot |
| DBV-03 | -73.99000 | 40.70480 | Shoreline east of the Manhattan Bridge |
| DBV-04 | -73.98400 | 40.70420 | Shoreline at the John Street / Navy Yard edge |
| DBV-05 | -73.98050 | 40.70280 | Vinegar Hill shoreline turn |
| DBV-06 | -73.98050 | 40.70050 | Bridge Street, east edge |
| DBV-07 | -73.98450 | 40.69930 | York Street at Bridge Street |
| DBV-08 | -73.98800 | 40.69870 | York Street at Jay Street |
| DBV-09 | -73.99120 | 40.69880 | Sands Street at the Manhattan Bridge approach |
| DBV-10 | -73.99450 | 40.69990 | Prospect Street at the Brooklyn Bridge approach |
| DBV-11 | -73.99720 | 40.70140 | Old Fulton Street, west edge |

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
| DCTL-050 | eye_height_adult | 1.65 | m | DEF-005 | B | Nominal adult standing eye height. |
| DCTL-051 | eye_height_child | 1.15 | m | DEF-005 | B | Nominal eye height for a child of about eight. |
| DCTL-052 | walk_pace_default | 1.30 | m/s | DEF-005 | B | Unhurried adult pace. |
| DCTL-053 | walk_pace_family | 1.05 | m/s | DEF-005 | B | Group containing children, sightseeing rather than commuting. |
| DCTL-054 | walk_pace_max | 2.20 | m/s | DEF-005 | B | Clamp for free-fly walking, so the streaming manager is never outrun. |

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

No DEM has been ingested yet (`DSRC-008`, `DOQ-003`). In the meantime the ground is interpolated from
the one authoritative elevation sample this project already holds: `ground_elevation` on each building
footprint, which `DSRC-003` defines as the lowest elevation at that building's ground level in NAVD88.
Those are grade `A` point samples; the surface interpolated between them is not, and is graded `C`.

| Control ID | Key | Value | Unit | Source IDs | Confidence | Notes |
|---|---|---:|---|---|---|---|
| DCTL-070 | ground_grid_cell | 32 | m | DEF-007 | A | Cell size of the interpolated ground height grid. |
| DCTL-071 | ground_idw_radius | 260 | m | DEF-007 | A | Search radius for inverse-distance weighting of building base elevations. |
| DCTL-072 | ground_idw_neighbours | 8 | count | DEF-007 | A | Number of nearest samples blended per cell. |
| DCTL-073 | ground_idw_power | 2 | ratio | DEF-007 | A | Inverse-distance exponent. |

Any asset or camera height derived from this grid is graded `C` and carries `DOQ-003`.

---

## 7. Open questions

| ID | Question | Affects | Sources to consult | Status |
|---|---|---|---|---|
| DOQ-001 | Real-world georeference of the Manhattan Bridge model. `manhattan-bridge-3d` OQ-009 leaves the bridge axis azimuth and geodetic anchor unregistered. This repository publishes a **provisional** placement so the bridge can be seen from a DUMBO street; it is explicitly not survey truth. | Bridge placement in the district scene | manhattan-bridge-3d SRC-001, SRC-003; DSRC-007 | Open. Awaiting ratification by the bridge team. |
| DOQ-002 | Buildings whose `height_roof` is zero or null are reconstructed from PLUTO floor counts using DCTL-062. | Building heights | DSRC-001, DSRC-002 | Open, bounded. Affected buildings are graded `C` and listed in the build report. |
| DOQ-003 | No surveyed terrain surface is registered. Ground height is interpolated from building base elevations (section 6) rather than from a DEM, so open ground away from buildings, and the real grade of individual streets, are approximations. | Terrain, walk mode ground height, tour camera height | DSRC-008 (NYC DEM), NYC LiDAR | Open, partially mitigated. Interpolated ground is graded `C`. |
| DOQ-004 | The MHW to NAVD88 offset DCTL-010 is transferred from The Battery, about 3 km away, rather than computed locally with VDatum. | Vertical datum reconciliation | NOAA VDatum | Open, immaterial at current confidence. |
| DOQ-005 | The district boundary in section 2.1 was drawn to follow named streets and the shoreline by inspection, not traced from a cadastral source. | District extent | DSRC-004, NYC planimetrics shoreline | Open, immaterial. It defines project scope only and clips no geometry that another module owns. |
| DOQ-006 | Paved surfaces are derived by widening OSM centrelines with typical half-widths by street class, rather than traced from planimetric sidewalk polygons. Kerb lines are therefore approximate, and junctions are overlapping quads rather than a resolved surface. | Roadway and sidewalk geometry | NYC planimetric sidewalk dataset | Open. Surfaces graded `C`. |
| DOQ-007 | Facade appearance is inferred from PLUTO building class and construction year. It describes the *kind* of building, not the actual facade of that building. | Building appearance in walk mode | Street-level imagery, district photogrammetry | Open. Appearance graded `C`; it never affects geometry or dimensions. |

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
