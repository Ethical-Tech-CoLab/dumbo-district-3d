# DUMBO LOD Strategy

How this module spends geometry, and why the answer is the same shape as the bridge team's answer.

The machine-readable ladder is generated to `viewer/public/district/lod.json` and conforms to
`lod.schema.json`.

---

## 1. The principle

The shared contract puts every level from every module on **one axis: `max_geometric_error_m`**, the
worst-case deviation of a representation from the module's control geometry.

A viewer mode is not a different ladder. It is a different **screen-space error budget** on the same
ladder:

```
sse_px = (geometric_error_m / distance_m) * (viewport_height_px / (2 * tan(fov_y / 2)))
refine while sse_px > budget_px
```

```json
"mode_sse_budget_px": { "inspect": 2, "walk": 12, "map": 48, "tour": 8 }
```

That is the whole reason this module and `manhattan-bridge-3d` can share a scene and a renderer. See
`digital-3d-shared-contracts/VIEWER-MODES.md` §3.2.

`tour` is tighter than `walk` (8 vs 12) on purpose: a scripted tour knows its route in advance, so it
can afford to prefetch and render better than a free-roaming user who might turn any corner.

---

## 2. The ladder

| Level | Name | Intent | Error | Representation | Selectable | Metadata | Streamed |
|---|---|---|---:|---|---|---|---|
| 0 | hero | traverse | 0.20 m | extruded footprint, full ring | yes | yes | yes |
| 1 | walkable | traverse | 2.0 m | extruded footprint, 1 m simplified | yes | yes | yes |
| 2 | context | context | 8.0 m | minimum-area oriented box | no | no | yes |
| 3 | silhouette | silhouette | 25 m | map polygon | no | no | no |

### Where each error figure comes from

**LOD0, 0.20 m.** Plan geometry is the source footprint, unsimplified, so plan error is the source's
own ±0.61 m and is not attributable to the representation. The 0.20 m is dominated by the **flat-roof
assumption**: the model extrudes to `height_roof` and stops. Cornices, parapets and mansards deviate
from that by roughly this much on DUMBO's warehouse stock. It is an honest statement that we model
massing, not roof form.

**LOD1, 2.0 m.** Ramer–Douglas–Peucker at 1 m tolerance, so plan error is bounded at 1 m by
construction. Doubled to 2 m to absorb the roof assumption on top.

**LOD2, 8.0 m.** A minimum-area oriented rectangle, found by rotating callipers over the footprint's
edge directions. Preserves area and orientation and costs four vertices. 8 m is roughly the worst
deviation of an L-shaped Brooklyn warehouse from its bounding rectangle.

**LOD3, 25 m.** Map view only, never rendered in 3D.

---

## 3. Fidelity zones

The ladder says what a level *is*. Zones decide which levels get **built** for a tile. Selection at
runtime is still pure screen-space error.

| Zone | Definition | Tiles | Levels built |
|---|---|---:|---|
| hero | within 60 m of a hero centreline (`DCTL-030`) and inside the boundary | 32 | 0, 1, 2 |
| walkable | inside the boundary | 31 | 1, 2 |
| context | outside the boundary | 49 | 2 |

Hero centrelines, from `DUMBO-GEOSPATIAL-CONTROL.md` §2.2: the Washington Street view corridor, Water
Street, Main Street to the waterfront, the Brooklyn Bridge Park waterfront, the Manhattan Bridge
Brooklyn approach, and Old Fulton Street to Fulton Ferry.

This is the whole cost-control argument: **LOD0 exists only where somebody actually stands and
looks.** 32 of 112 tiles. Everywhere else starts at LOD1 and nobody notices, because by the time you
can see the difference you have walked into a hero tile.

---

## 4. Payload format, and why it is not GLB

Levels 0–2 ship as **JSON footprint rings plus heights**, not GLB, with
`representation: "extruded_footprint"`.

For extruded footprints this is the better trade and the contract explicitly allows it:

- A GLB bakes the extrusion. Every wall quad becomes six vertices with positions, normals and
  colours. Roughly triple the bytes for geometry the client can generate in a fraction of a
  millisecond.
- Baking discards the ability to re-extrude. Confidence overlay, a different roof assumption, a
  future storey-level split — all become rebuilds instead of re-renders.
- The rings are the *authoritative* representation. Shipping them keeps the payload closer to the
  source, which matters for a model whose whole point is traceability.

Actual cost for the whole district: **123 payloads, 446 KB total.** A hero tile is typically 3–6 KB.

The client merges each tile into one `BufferGeometry` with a vertex-range table, so a tile is one draw
call while individual buildings stay pickable.

This is a considered exception, not a shortcut. When photogrammetry or real roof forms arrive they
will be meshes, and those levels will ship as GLB.

---

## 5. Streaming

From `DUMBO-GEOSPATIAL-CONTROL.md` §3:

| Control | Value | Purpose |
|---|---:|---|
| `DCTL-040` tile size | 128 m | ~10–30 buildings per tile |
| `DCTL-041` load radius | 420 m | keep resident within this |
| `DCTL-042` unload radius | 640 m | hysteresis band against thrashing |
| `DCTL-043` prefetch ahead | 260 m | extra distance along camera heading |

Load and unload radii differ deliberately. A camera loitering on a tile boundary with a single radius
will load and unload the same tile every frame.

**Tour prefetch.** During a tour the streamer is additionally handed
`player.plannedRoute(500, 50)` — 500 m of the actual future route, sampled every 50 m. Heading-based
prefetch guesses; a tour knows. This is why a scripted walk arrives at a corner with the geometry
already there.

Observed in the running viewer: 32–42 tiles resident, LOD 0, 1 and 2 all present simultaneously,
which is the ladder working as designed rather than a single level snapping between distances.

---

## 6. Foreign content

Twelve tiles declare `urn:d3d:manhattan-bridge:bridge_proxy` in `foreign_assets` — the tiles the
Manhattan Bridge crosses. When one becomes resident, the shell ensures the bridge module's content is
loaded too, capped at the `max_level: 2` the bridge manifest declares.

The cap is enforced by the consumer, which is the important part: a neighbouring module cannot blow
this district's frame budget even if it ships something enormous.

---

## 7. Known limits

| | Effect | Tracked |
|---|---|---|
| No terrain LOD | The ground grid is a single 57 × 33 mesh, always resident. Cheap at district scale; would need tiling at borough scale. | — |
| Flat roofs at every level | Folded into the declared error of each level, not hidden. | — |
| No impostors or billboards | LOD2 blocks go all the way to the horizon. Fine for 1.5 km; a city would want them. | — |
| No texture streaming | There are no textures. Facades are flat-tinted by a stable hash of the building ID. | — |
| LOD3 is map-only | The map view draws from the tile index directly rather than from a built LOD3 payload. | — |
