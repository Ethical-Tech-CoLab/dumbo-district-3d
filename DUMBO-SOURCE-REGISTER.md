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

### DSRC-008 — NYC digital elevation model and LiDAR · Tier A · grants A · NOT INGESTED

| | |
|---|---|
| Publisher | NYC Office of Technology and Innovation |
| License | NYC Open Data Terms of Use |
| Verified | **No — not fetched** |

Superseded in practice by `DSRC-013`, which supplies terrain at 1 m from a public-domain national
programme and is samplable over HTTP without downloading a raster. NYC's own DEM is 1 ft and would
be a **refinement, not a correction**; it stays registered so that the difference between what we
have and the best available is visible rather than forgotten.

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

### DSRC-010 — NYC Planimetric Database: surfaces underfoot · Tier A · grants A

| | |
|---|---|
| Publisher | NYC Office of Technology and Innovation, via NYC Open Data |
| Accessed | 2026-08-10 |
| License | NYC Open Data Terms of Use |
| Attribution | **Required** — "Planimetric surfaces: NYC Open Data (OTI)" |
| Native CRS | EPSG:4326 |
| Verified | Yes |

Six published layers, because between them they cover everything a walker stands on in DUMBO:

| Layer | Dataset | In the district |
|---|---|---:|
| Sidewalk | `52n9-sdep` | 170 features |
| Roadbed | `i36f-5ih7` | 390 |
| Curbs | `5xvt-8cbk` | 714 lines |
| Public Plazas | `ue2e-9jm2` | 12 |
| Open Space (Parks) | `y6ja-fw4f` | 88 |
| Boardwalk | `p9cw-7gsv` | 2 |

This replaces the widened-centreline approximation and closes `DOQ-006`. The difference is not
cosmetic: a widened centreline puts the kerb wherever a typical half-width says it should be, turns
every junction into a heap of overlapping quads, and cannot tell a pavement from a plaza from a park.
These are traced shapes, so the pavement runs where the pavement runs.

The **curbs** layer is the one that changes the view most. It is surveyed kerb *lines*, which is what
makes a kerb face possible at all — extruded to `DCTL-080`, they give the eye the vertical edge it
needs to judge level and distance while walking. The line is grade A; the height applied to it is a
single conventional value and is not.

A first attempt at this used dataset `vfx9-tbb6`, which the catalogue also lists as "NYC Planimetric
Database: Sidewalk". That one is a Socrata **map**, not a table: it answers queries with rows that
have no columns, which is why an earlier run reported fetching 50,000 records and keeping none.

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

### DSRC-013 — USGS 3DEP 1 m bare-earth DEM · Tier A · grants A

| | |
|---|---|
| Publisher | U.S. Geological Survey, 3D Elevation Program |
| Accessed | 2026-08-09 |
| License | **Public domain** (U.S. Government work) |
| Attribution | Not legally required; given anyway — "Elevation: USGS 3D Elevation Program (3DEP)" |
| Native CRS | EPSG:3857 service, queried in EPSG:4326 |
| Vertical datum | **NAVD88**, metres |
| Verified | Yes |

The terrain surface. Sampled through the ImageServer's `getSamples` operation on the exact 8 m ground
grid — 29,025 points in 30 batched requests — rather than downloaded, because the national raster is
far too large to hold and we need only this district.

**Bare earth** is the correct product here: buildings are already removed from it, so what is sampled
is the pavement a walker stands on, not the roof above them. Elevations arrive in NAVD88 metres,
which is already this project's vertical datum, so nothing is transformed and nothing can be got
wrong in transforming it.

Verified empirically as well as by specification: every build compares these samples against
`ground_elevation` from `DSRC-003`, an independent grade A measurement of the same quantity by a
different agency. Current agreement is a **bias of +0.03 m** and a **p95 of 1.51 m** across 385
buildings. See `DUMBO-GEOSPATIAL-CONTROL.md` §6.1 for why those two numbers, and not one, are the
right guard.

Retires `DOQ-003`.

### DSRC-014 — NYC Planimetric Database: Hydrography · Tier A · grants A

| | |
|---|---|
| Publisher | NYC Office of Technology and Innovation, via NYC Open Data |
| Accessed | 2026-08-09 |
| License | NYC Open Data Terms of Use |
| Attribution | **Required** — "Hydrography: NYC Open Data (OTI)" |
| Native CRS | EPSG:4326 |
| Verified | Yes |

Water body polygons — the East River and the Navy Yard Basin — rasterised onto the ground grid to
decide land from water. 7,532 of 29,025 cells are water.

This replaces the district boundary polygon, which had been doing the job by default. That polygon
exists to scope the project and was drawn by inspection (`DOQ-005`); it was never a shoreline, and
using it as one meant the terrain either drowned the waterfront or paved the river depending on
which way the apron erred.

### DSRC-015 — Openly-licensed photographs of DUMBO · Tier C · grants B

| | |
|---|---|
| Publisher | Wikimedia Commons contributors |
| Accessed | 2026-08-09 |
| License | **Mixed, all reuse-permitting** — CC0-1.0, public domain, CC BY 2.0/3.0/4.0, CC BY-SA 2.0/3.0/4.0 |
| Attribution | **Required per image** — credit lines are carried in `photo-survey.json` |
| Verified | Yes |

The first found-imagery campaign: **336 photographs** fetched, of which **62 survived human review**
— 185 were geolocated, 156 good enough to grant grade B before curation.

Every image was already published under a licence that permits reuse. Nothing was scraped from a
tourism site, a listing agent or a social feed, because an image whose licence is unknown cannot be
used here however good it looks — fetching it would create a liability, not an asset. The ingest
checks each licence against an allowlist and **rejects anything it does not recognise**, so a new or
oddly spelled variant fails closed.

**Curation is a person's job, and the numbers say why.** The automatic screen had attached 84
photographs to buildings; a reviewer rejected 56 of them — a two-thirds false-positive rate,
including a photograph of a parked car that was colouring four warehouses. Every rejection is
recorded in `data/photos/rejected.json` keyed by source URL, and the ingest consults that ledger
before adding anything, so the same material is never offered twice.

Each kept photograph carries a **category** saying what it is evidence for, because "use" proved too
blunt: a picture of Jane's Carousel, an archival view of a demolished wall, and a usable facade are
all "use", and treating them alike put the wrong colour on the wrong building. Photographs
categorised `bridge` are kept and credited but **nothing is derived from them** — that structure
belongs to another module, which is the anti-duplication rule applied to imagery.

What each licence permits is recorded per record rather than assumed in bulk:

| Licence | `usage` | Meaning |
|---|---|---|
| CC0, public domain, CC BY | `redistribute` | The image may be served to users, with its credit line. |
| CC BY-SA | `derive_appearance` | Colours and dimensions may be **measured**; the image is not republished by us. |

Share-alike is deliberately held at `derive_appearance`. Measuring the dominant brick colour of a
wall extracts a *fact*, and facts carry no copyright, so nothing downstream inherits the ShareAlike
obligation. Republishing the photographs would be permitted too, but only under the same licence,
and quietly mixing that obligation into an otherwise MIT repository is very hard to unpick later.
Reference it, measure it, do not vendor it. **No third-party image bytes are committed here** — the
same rule this project already applies to another module's geometry.

Photographs never grant A; that is the contract's rule, not a local one. Grade B requires an image
that is locatable, datable and large enough to read a material from. See `DOQ-007`.

### DSRC-016 — OpenStreetMap street furniture · Tier B · grants A for position

| | |
|---|---|
| Publisher | OpenStreetMap contributors, via Overpass API |
| Accessed | 2026-08-10 |
| License | **ODbL 1.0** |
| Attribution | **Required** — "© OpenStreetMap contributors" wherever this is rendered |
| Native CRS | EPSG:4326 |
| Verified | Yes |

1,359 features inside the district: 465 benches, 401 street lamps, 173 barrier lines totalling
**7.4 km**, plus bollards, bike racks, litter baskets, hydrants, signals and flagpoles.

Railings are the reason this source was added. The Brooklyn Bridge Park waterfront is fenced along
almost its whole length, and a promenade rendered without one does not read as a promenade — it
reads as a lawn that stops at the water, with nothing telling a walker where the edge is.

**A finding worth recording: OSM does not tag these the way the wiki suggests.** Not one feature in
the district carries `barrier=railing`. The waterfront guard rail is `barrier=fence` with
`fence_type=railing`, and querying the documented tag alone returns nothing at all. The ingest
therefore reads `fence_type` in preference to `barrier`, which is also what keeps the promenade's
railing from rendering like the Farragut substation's chain-link.

Position is grade A — these are surveyed or traced from imagery by mappers on the ground. Everything
else is grade C: OSM says a bench is *there*, not what it looks like, so heights, colours and forms
are conventional values chosen per type. That split is the whole point of the grade.

### DSRC-017 — OpenStreetMap ground-floor businesses · Tier B · grants A for position

| | |
|---|---|
| Publisher | OpenStreetMap contributors, via Overpass API |
| Accessed | 2026-08-10 |
| License | **ODbL 1.0** |
| Attribution | **Required** — "© OpenStreetMap contributors" wherever this is rendered |
| Native CRS | EPSG:4326 |
| Verified | Yes |

166 shops, cafes, restaurants and bars inside the district, 164 of them named. **137 awnings** were
placed from them; 2 businesses had no building within 30 m and were skipped rather than guessed at.

A DUMBO warehouse at street level is a row of shopfronts under a brick wall, and without them every
building meets the pavement as a blank face — the most obvious remaining tell that the model is a
model.

**PLUTO cannot answer this question**, which is why a second source is needed for a fact it looks
like it should already hold. PLUTO's building class describes a whole building, so a cafe on the
ground floor of a residential block is invisible to it, and the ground floor is exactly the part a
walker sees. The ingest this project already runs does not carry `retailarea` either. OSM maps the
business itself, which is the thing visible from the pavement.

The work is in the placement rather than the geometry. An OSM node sits somewhere *inside* a
business, so each awning is projected onto the nearest facade edge and turned to face outward, away
from the footprint's interior. Hanging it at the node would leave awnings in the middle of rooms;
guessing the facing would put half of them inside the wall. The outward direction is chosen by
testing against the footprint's own centroid rather than by ring winding, because the published
footprints do not all wind the same way — the same trap that made whole planimetric layers vanish.

Graded **D**. The business is real and the wall is real; that it has an awning at all, and the size
and colour of that awning, are decoration.

### DSRC-018 — NYC landmark building register (LPC) · Tier A · grants A

| | |
|---|---|
| Publisher | NYC Landmarks Preservation Commission, via NYC Open Data (`gpmc-yuvp`) |
| Accessed | 2026-08-10 |
| License | NYC Open Data Terms of Use |
| Attribution | **Required** — "Landmark designations: NYC Landmarks Preservation Commission" |
| Native CRS | EPSG:4326 · keyed by BIN and BBL |
| Verified | Yes |

**1,386 designated buildings** inside the district envelope, each carrying the primary material, the
architectural style, the building type and the construction date — as published in the designation
report for that specific address.

This is the strongest architectural evidence in the project, and it arrived late. Everything else
*infers* what a building looks like: PLUTO gives a tax class, from which a material family is
guessed; a photograph gives a colour measured off a wall. Here the city says, on its own authority,
that 55 Hudson Avenue is Greek Revival, brick, 1830s. 287 of the district's 446 buildings are covered.

| District | Buildings |
|---|---:|
| Brooklyn Heights Historic District | 1,166 |
| DUMBO Historic District | 93 |
| Vinegar Hill Historic District | 45 |
| Fulton Ferry Historic District | 37 |
| Borough Hall Skyscraper Historic District | 21 |
| Individual landmarks | 17 |

**It also corrected the district's own boundary.** Tested against this register, the previous
hand-drawn extent excluded eleven designated buildings — ten of them the Hudson Avenue row that the
Vinegar Hill Historic District exists to protect, and 10 Jay Street on the DUMBO shoreline. A scope
polygon that omits the landmarked core of a neighbourhood it claims to cover is simply drawn wrong,
and no amount of looking at it would have said so. See section 2 of the control document.

The register drives three things: the facade's **material** (brownstone and brick are distinct and it
distinguishes them), the **glazing ratio** via architectural style, and the **bay pitch** via building
type — a row house has a narrow two-bay front, a daylight factory has wide industrial openings, and
that single number is most of what stops a wall reading as a striped box.

A photographed facade still wins on colour. The report describes the fabric; the photograph describes
the surface as it stands today, after however many repaintings.

---

## 3. Definition sources

`DEF-001` … `DEF-007` are project decisions, not external evidence: the frame anchor, the boundary,
the hero zone, the tile tuning, pedestrian ergonomics, unit constants, and the ground interpolation
parameters. They are listed in [DUMBO-GEOSPATIAL-CONTROL.md](DUMBO-GEOSPATIAL-CONTROL.md) §8 and
exist so that `source_basis` is never empty and a reader can always tell a decision from a
measurement.

---

## 4. Attribution the viewer must display

Collected by `ModuleRegistry.attributions()` and rendered unconditionally in the viewer footer.
The list is **generated from this register**, not hand-maintained — a hand-kept list falls behind the
moment a source is added, which is how street trees, elevation and hydrography came to be used
without being credited:

```
Building footprints: NYC Open Data (OTI)
Lot attributes: NYC Department of City Planning (PLUTO)
Tidal datums: NOAA CO-OPS station 8518750
© OpenStreetMap contributors
Street trees: NYC Parks Forestry Management System
Elevation: USGS 3D Elevation Program (3DEP)
Hydrography: NYC Open Data (OTI)
```

Any source carrying an `attribution_text` is credited, not only those where attribution is legally
required. The USGS and NOAA works are public domain and oblige nothing; naming who measured the
ground you are standing on costs a line of text.

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
data/terrain/dem.raw.json                    + .source.json
data/terrain/hydrography.raw.json            + .source.json
```

Re-run `python scripts/ingest_sources.py --all` to refresh. The sidecars are what make this register
auditable rather than aspirational.
