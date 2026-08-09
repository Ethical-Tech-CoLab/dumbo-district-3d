# DUMBO Scope

What this module owns, what it deliberately does not, and where the line runs.

---

## 1. Subject

The DUMBO district of Brooklyn, bounded by the East River to the north, Bridge Street and the Vinegar
Hill edge to the east, York and Sands Streets to the south, and Old Fulton Street and the Brooklyn
Bridge approach to the west. The exact polygon is in
[DUMBO-GEOSPATIAL-CONTROL.md](DUMBO-GEOSPATIAL-CONTROL.md) §2.1 and is the only definition that
counts.

Roughly 0.9 km east to west, 0.7 km north to south. 381 buildings inside the boundary.

---

## 2. In scope

**Authoritative.** This module is the single source of truth for all of it:

| | Status |
|---|---|
| Building footprints and massing | Built. 381 buildings, all grade A. |
| Property metadata (address, owner, class, land use, floors, year) | Built. 375 of 381 joined to PLUTO. |
| Street and footway network | Built. 4,346 nodes, 5,131 edges. |
| District boundary and fidelity zones | Built. |
| Tile grid, streaming and LOD management | Built. 112 tiles, four levels. |
| Ground surface | Interpolated from building base elevations. Grade C, `DOQ-003`. |
| Walking experience and district navigation | Built. |
| Map and 3D scene integration | Built. |
| Tour playback | Built, on the shared player. |
| Waterfront and park extents | Partial: parks appear as landmarks, not as modelled surfaces. |

---

## 3. Out of scope

**Owned by `manhattan-bridge-3d`.** This module contains none of it and never will:

- bridge towers, cables, suspenders, deck systems, trusses, anchorages
- bridge control dimensions and component taxonomy
- bridge photogrammetry and engineering detail
- the bridge inspect UI

The bridge is consumed by URN through its module manifest. Today that manifest is a placeholder
written by this project (see [BRIDGE-TEAM-COORDINATION.md](BRIDGE-TEAM-COORDINATION.md)) and the
viewer draws a red wireframe envelope, labelled as a placeholder, sized from the bridge team's own
published control dimensions. It is not a model of the bridge and is not presented as one.

The Brooklyn Bridge is not owned by anyone in this stack yet. It is currently not rendered at all.

**Deferred, and honestly so:**

| | Why | Tracked as |
|---|---|---|
| Terrain from DEM or LiDAR | Not yet ingested; ground is interpolated from building bases | `DOQ-003`, `DSRC-008` |
| District photogrammetry | Explicitly excluded from Phase 1 by the brief | — |
| Roof forms | All roofs are flat at the dataset roof height | folded into LOD0's declared 0.2 m error |
| Interiors | Not a Phase 1 concern | — |
| Vegetation, street furniture, vehicles, people | Not a Phase 1 concern | — |
| Textures and materials | Massing only; facades are flat-tinted | — |
| Historic time slices | Out of scope | — |

---

## 4. Where the line runs

Three cases worth being precise about, because they are where duplication would creep in.

**The bridge lands in the district.** The Manhattan Bridge's Brooklyn anchorage sits inside the
district boundary, and Anchorage Place runs beneath it. The *street* is ours. The *anchorage* is
theirs. The tile index declares `urn:d3d:manhattan-bridge:bridge_proxy` in `foreign_assets` on the 12
tiles the bridge crosses, so the streamer keeps their content resident whenever a visitor can see it.
We model the kerb; they model the masonry.

**Buildings under the bridge approach.** Ordinary buildings, ours, from the NYC footprint dataset.
The dataset does not include the bridge structure, so there is no overlap to resolve.

**The waterfront.** The shoreline itself is not modelled. Water is a plane at mean high water,
0.59 m above this frame's NAVD88 zero. Where terrain rises above that, terrain wins. This is an
approximation with a visible seam at the water's edge and is recorded as such rather than dressed up.

---

## 5. Fidelity by zone

Fidelity is spent where people stand and look. See
[DUMBO-LOD-STRATEGY.md](DUMBO-LOD-STRATEGY.md).

| Zone | Tiles | Levels built |
|---|---:|---|
| Hero — Washington Street, Water Street, Main Street, the waterfront, both bridge approaches, Old Fulton Street | 32 | 0, 1, 2 |
| Walkable — the rest of the district | 31 | 1, 2 |
| Context — beyond the boundary | 49 | 2 |

---

## 6. Confidence

Every asset carries a grade describing how its geometry was obtained, per the shared evidence model.

Current state of the 381 buildings: **381 grade A, zero B, zero C, zero D.**

That is a real result, not a generous one: every building's plan geometry and roof height come
directly from the NYC building footprints dataset (`DSRC-001`), which is an authoritative published
source with a stated ±0.61 m positional accuracy. The reconstruction fallbacks in `DCTL-062` and
`DCTL-063`, which would demote a building to C or D, were not needed by any building in this district.

Two things in the model are *not* grade A and are labelled accordingly:

- The **ground surface** is grade C. Its samples are authoritative; the surface interpolated between
  them is not (`DOQ-003`).
- The **Manhattan Bridge placement** is grade D and provisional (`DOQ-001`).

---

## 7. Phase boundary

Phase 1 stops here, per the brief. Delivered:

1. Boundary definition ✓
2. Tile grid ✓
3. Building footprint import ✓
4. PLUTO integration ✓
5. Geospatial control model ✓
6. Asset registry schema ✓ (in shared contracts)
7. Map viewer prototype ✓
8. Scene viewer prototype ✓
9. Bridge placeholder integration point ✓

Plus, beyond the brief, because the requirement arrived with it: externally driven tour playback,
built on a shared contract and a shared player rather than as a district-local feature.

No photogrammetry, as instructed.
