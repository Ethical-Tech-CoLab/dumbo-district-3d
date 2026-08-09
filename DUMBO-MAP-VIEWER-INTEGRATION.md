# DUMBO Map and Viewer Integration

How the browser viewer is put together, what it owns, and what it borrows.

---

## 1. Architecture

The viewer is a **shell** on the shared **kernel**. It renders; the kernel decides.

```
                    @d3d/viewer-kernel                      (shared repo)
  Frame · ModuleRegistry · LodSelector · TileStreamer · TourPlayer · EventBus
                              │
                              ▼
                    dumbo-district-3d/viewer                (this repo)
  App.tsx           wiring, frame loop, modes, tour orchestration
  DistrictScene.ts  three.js: terrain, extrusion, picking, bridge placeholder
  WalkControls.ts   pointer-lock first person
  WalkRouter.ts     A* over the pedestrian network
  GroundGrid.ts     bilinear ground sampling
  FrameLoop.ts      rAF with a hidden-document fallback
  components/       metadata panel · tour panel · HUD · map view · photo strip
```

Nothing in this viewer knows anything about the Manhattan Bridge beyond a URN and a manifest URL.

---

## 2. Boot sequence

1. `ModuleRegistry.load('district/district-manifest.json')`.
2. The registry follows `depends_on` and tries the bridge manifest. It is `required: false`; if it
   fails, a `module:missing` event fires, the user sees an integration notice, and the district
   renders anyway.
3. `new Frame(georeference)` — the scene frame, `nyc-harbor-enu`.
4. `new LodSelector(ladder)`, `new TileStreamer(tileIndex, selector)`.
5. Ground grid, district boundary and walk network load; the router is constructed.
6. The camera is placed at Washington Street and Water Street facing north — the view the district
   exists to deliver.
7. `FrameLoop.start()`.

A missing district manifest is fatal and shows the exact commands needed to build it. A missing
bridge manifest is not.

---

## 3. Frame loop

Streaming runs at 4 Hz, not per frame: tile decisions are not a per-frame cost.

```
dt ──▶ tour playing?  ── yes ─▶ player.update(dt) ─▶ camera state
        │                        also syncs WalkControls so exiting the tour does not teleport
        └─ no ──────────────────▶ WalkControls.update(dt) ─▶ ground height + eye height

camera ─▶ Frame.sceneToRender ─▶ three.js camera
        └▶ every 0.25 s ─▶ TileStreamer.update(…, plannedRoute) ─▶ load / unload
```

`FrameLoop` uses `requestAnimationFrame` when it is ticking, and falls back to a timer when the
document is hidden. Browsers suspend rAF entirely for hidden documents, which is correct for a game
and wrong for a viewer that also has to run for automated screenshot capture, CI smoke tests, and
rendering a tour to disk. The fallback runs slower, because nobody is watching, and hands back the
moment the document becomes visible.

---

## 4. Modes

| Mode | Budget | What it does |
|---|---:|---|
| `walk` | 12 px | First person. Pointer-lock look, WASD, shift to hurry, clamped to `DCTL-054` so the user cannot outrun the streamer. |
| `map` | 48 px | Plan view drawn from the tile index itself. |
| `tour` | 8 px | The player drives; controls follow. |
| `inspect` | 2 px | Declared and honoured by the LOD budget, but this module publishes no inspect UI — it belongs to the bridge team. |

Switching mode changes one number. That is the entire mechanism.

---

## 5. Map view

The map is drawn **from the same tile index the 3D view streams from**, not from a separate basemap.
Two consequences, both deliberate:

- The map and the scene cannot disagree about what exists.
- The fidelity zones become visible. Gold is hero, blue is walkable, dark is context, and the red
  dashed tiles are the twelve that declare the Manhattan Bridge as foreign content. The LOD strategy
  stops being a document and becomes something a reviewer can see.

It also draws the active tour route, lettered stop markers, and the camera as a heading arrow. SVG's
Y axis is flipped so scene north is up.

---

## 6. Rendering the district

**Terrain.** A single mesh from the interpolated ground grid, tinted by height. DUMBO rises about
23 m from the waterfront; without this the camera is metres underground for most of the district.
(Note for anyone building similar: scene `+Y` north maps to render `−Z`, which flips the grid's
handedness. Winding must be `a-b-c`, or every triangle faces down and is silently culled.)

**Buildings.** One merged `BufferGeometry` per tile — one draw call per tile, not per building —
with a vertex-range table in `userData` mapping ranges back to building IDs, so picking still
resolves an individual building. Walls are shaded by orientation and roofs lightened, so massing
reads without textures. Facade tint is a stable hash of the building ID.

**Water.** A plane at mean high water, 0.59 m above this frame's NAVD88 zero (`DCTL-010`). Terrain
occludes it wherever the ground is higher. The shoreline is not modelled, so there is a visible seam
at the water's edge; see `DUMBO-SCOPE.md` §4.

**The bridge.** A red wireframe envelope, sized from the bridge team's own `CTL-001`, `CTL-005` and
`CTL-007`, positioned by the provisional placement, drawn only when a resident tile declares it. It
is obviously not a model, which is the point.

---

## 7. Selection and metadata

Raycast into the tile root, find which vertex range was hit, map to a building ID, look up the
payload record, and convert to the shared `metadata.schema.json` shape. The panel renders the shared
contract, so the same panel would render a bridge part unchanged.

Every record shows its confidence chip, source basis, source refs, the control values it consumed,
and any open questions. Provenance is a first-class part of the UI, not an about box.

---

## 8. Tours

The player is the shared one. This viewer supplies the four things only a shell can know:

| Callback | Supplied by |
|---|---|
| `resolveAsset` | building anchors from resident tiles, then the registry, then a foreign module's placement |
| `router` | `WalkRouter`, A* over the OSM pedestrian network |
| `groundHeight` | `GroundGrid` bilinear sample |
| `tour:capture` | a real framebuffer grab, cover-fitted to the requested aspect |

Photographs are genuine renders taken at the moment the script's `capture_photo` fired, which is what
makes "the family stopped and took a picture here" an artefact rather than a claim.

See [TOUR-DIRECTOR.md](TOUR-DIRECTOR.md).

---

## 9. Running it

```bash
cd viewer
npm install
npm run dev          # http://localhost:5178
npm run build        # typecheck + production bundle
```

The kernel and contracts are aliased from `../../digital-3d-shared-contracts` in `vite.config.ts`, so
editing the kernel is immediately visible here with no build or link step. `server.fs.allow` is
widened because those sources live outside this project root.

---

## 10. Known limits

| | |
|---|---|
| Bundle is ~723 KB (200 KB gzipped) | Dominated by three.js. Code-splitting would help but adds nothing at this stage. |
| No collision | You can walk through walls. Fine for a survey tool, wrong for a game. |
| Confidence overlay rebuilds tiles | Cheap at 381 buildings; would need per-vertex attribute swapping at city scale. |
| No LOD3 in 3D | The map view covers the silhouette case. |
| Picking is LOD0/LOD1 only | LOD2 blocks are explicitly `selectable: false`. |
