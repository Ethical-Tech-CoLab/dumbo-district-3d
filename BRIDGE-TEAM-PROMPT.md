# Handoff Prompt — Manhattan Bridge Team

Drop the block below into the `manhattan-bridge-3d` agent session verbatim. Everything it needs is
either inline or at a path it can read from the local filesystem.

---

## COPY FROM HERE

You are working in `c:\Dev\manhattan-bridge-3d`.

Two sibling repositories now exist and are complete. Read them before changing anything here:

- `c:\Dev\digital-3d-shared-contracts` — shared schemas, docs and viewer runtime. No module data.
- `c:\Dev\dumbo-district-3d` — the DUMBO neighbourhood twin, which consumes this project.

**Read these four documents first, in order:**

1. `c:\Dev\dumbo-district-3d\BRIDGE-TEAM-COORDINATION.md` — what is being asked of you and why
2. `c:\Dev\digital-3d-shared-contracts\VIEWER-MODES.md` — how CAD inspection and district
   walkthrough share one viewer instead of forking into two
3. `c:\Dev\digital-3d-shared-contracts\COORDINATE-SYSTEM.md` — frames, and the vertical datum problem
4. `c:\Dev\digital-3d-shared-contracts\GOVERNANCE.md` — ownership boundaries and how contracts change

### Context

The DUMBO team built their district twin as an independent module. They deliberately modelled **no
part of the bridge**. Where the Manhattan Bridge crosses their district, their viewer currently draws
a red wireframe envelope, clearly labelled as a placeholder, sized from *your* published control
dimensions (`CTL-001`, `CTL-005`, `CTL-007`).

They also wrote a **placeholder `bridge-manifest.json` on your behalf** so their optional dependency
resolves today. Your job is to replace it with a real one that you own.

### The design, in one paragraph

Inspect mode and walk mode are not different renderers. Every level of detail from every module sits
on one axis, `max_geometric_error_m`, and a viewer mode is simply a different screen-space-error
budget on that shared axis: `{ inspect: 2, walk: 12, map: 48, tour: 8 }` pixels. Your LOD0 control
skeleton at 0.01 m and their context blocks at 8 m are points on the same line. That is why one
kernel serves both, and why you do not have to change your geometry, your coordinate frame, your
build scripts, or your confidence model.

---

## TASK 1 — Publish `bridge-manifest.json` (required)

Create `c:\Dev\manhattan-bridge-3d\viewer\public\bridge-manifest.json`.

Start from the placeholder the DUMBO team wrote, which is already schema-valid:

```
c:\Dev\dumbo-district-3d\viewer\public\modules\manhattan-bridge\bridge-manifest.json
```

Copy it, then make these corrections:

| Field | Change to |
|---|---|
| `title` | `"Manhattan Bridge"` (drop "placeholder manifest") |
| `subtitle` | your own wording |
| `module_version` | `"1.0.0"` — it is `0.0.0` precisely because it is not yours yet |
| `georeference.url` | `"./frames/nyc-harbor-enu.json"` — see URL structure below |
| `lod_ladder.levels` | your real ladder; add the levels you actually ship |
| `asset_registry_url` | add it once you publish a registry |
| `not_implemented_yet` | replace the three placeholder lines with your real gaps |
| `provenance.module_id` | `"manhattan-bridge"` — it currently says `dumbo-district` |
| `extensions` | delete the `dumbo-district` block; that is their placeholder-envelope convention |

**Keep `placement` for now** and keep `provisional: true` until you have decided about Task 4.

Validate before you commit:

```powershell
cd c:\Dev\digital-3d-shared-contracts
node tools\validate.mjs --schema module-manifest c:\Dev\manhattan-bridge-3d\viewer\public\bridge-manifest.json
```

It must print `all documents valid`. The schema uses `additionalProperties: false` deliberately: if
you need a private field, put it under `extensions["manhattan-bridge"]`, not at the top level.

---

## EXACT URL STRUCTURE

This is the part to get precisely right. Paths inside a manifest resolve **relative to the document
that declares them**, not relative to the page.

### Your served tree

Serve `viewer/public/` at your site root, exactly like the DUMBO viewer does:

```
/bridge-manifest.json                     <- the entry point; everything else is reached from here
/frames/nyc-harbor-enu.json               <- copy of the canonical shared frame
/bridge/lod.json                          <- if you externalise the ladder
/bridge/asset-registry.json
/bridge/source-register.json
/assets/bridge.lod0.glb                   <- your control skeleton
/assets/bridge.lod2.glb                   <- the district proxy, Task 2
```

With `bridge-manifest.json` at the root, these relative URLs resolve as:

| In the manifest | Resolves to |
|---|---|
| `"./frames/nyc-harbor-enu.json"` | `/frames/nyc-harbor-enu.json` |
| `"./bridge/lod.json"` | `/bridge/lod.json` |
| `"./bridge/asset-registry.json"` | `/bridge/asset-registry.json` |

And inside `asset-registry.json`, if you set `"base_url": "../assets/"`, then a variant
`"url": "bridge.lod2.glb"` resolves to `/assets/bridge.lod2.glb`.

### The canonical frame

`nyc-harbor-enu` is the shared scene frame. It is **published once**, in the contracts repository:

```
c:\Dev\digital-3d-shared-contracts\frames\nyc-harbor-enu.json
```

Copy that file to `viewer/public/frames/nyc-harbor-enu.json` and reference it with
`"georeference": { "url": "./frames/nyc-harbor-enu.json" }`. **Do not author your own frame and do not
edit the anchor.** It is frozen for the life of contract major version 1; every asset coordinate in
every module depends on it.

Copy it **byte-for-byte**, not by re-serialising, so it can be verified by hash:

```powershell
Copy-Item c:\Dev\digital-3d-shared-contracts\frames\nyc-harbor-enu.json `
          c:\Dev\manhattan-bridge-3d\viewer\public\frames\nyc-harbor-enu.json

# verify
(Get-FileHash c:\Dev\digital-3d-shared-contracts\frames\nyc-harbor-enu.json).Hash -eq `
(Get-FileHash c:\Dev\manhattan-bridge-3d\viewer\public\frames\nyc-harbor-enu.json).Hash
```

The DUMBO build does exactly this and additionally fails hard if the frame generated from its own
control document drifts from this file. Yours should too.

### How DUMBO will point at you

Their `district-manifest.json` currently says:

```json
"depends_on": [
  { "module_id": "manhattan-bridge",
    "manifest_url": "../modules/manhattan-bridge/bridge-manifest.json",
    "required": false }
]
```

Two deployment options — tell them which you want:

**A. Co-served (simplest; good for demos).** They copy your published artifacts into their
`viewer/public/modules/manhattan-bridge/`. Their URL above keeps working unchanged. Note that your
manifest's internal relative URLs then resolve from *that* directory, so a manifest written for
option A needs `../../frames/nyc-harbor-enu.json` rather than `./frames/...`. The placeholder they
wrote already uses the option-A form, because that is where it currently sits.

If you want one manifest that works in both deployments, use **root-absolute** URLs — `/frames/...` —
which the kernel passes through unchanged. That only works when your artifacts sit at the site root.

**B. Separately served (correct for production).** You serve at your own origin; they change one line
to an absolute URL:

```json
"manifest_url": "https://bridge.example.org/bridge-manifest.json"
```

The kernel handles absolute URLs, protocol-relative URLs and root-absolute paths. You must send
**CORS headers** (`Access-Control-Allow-Origin`) on the manifest and every artifact it references, or
their fetches will fail and their viewer will degrade to the placeholder.

Option B is the one that proves the architecture. Option A is fine to start.

---

## TASK 2 — Export a level-2 proxy (highest value)

One decimated GLB of the whole bridge. A few thousand triangles. Correct silhouette, correct
position, no rivets. This is what a pedestrian standing on Washington Street actually sees, and it is
the single most valuable artefact you can hand the district team.

Requirements:

- Authored in **your** frame — origin at main-span midpoint, `+X` toward Brooklyn, `+Z` up, meters.
  Do not pre-transform it into their frame; `placement` in your manifest does that.
- `max_geometric_error_m` around `8.0` for level 2. Be honest rather than flattering.
- Register it in your asset registry as `urn:d3d:manhattan-bridge:bridge_proxy`, which is the URN
  their tile index already references in `foreign_assets` on the 12 tiles you cross.

Your manifest already caps how far anyone may refine it while it is scenery:

```json
"proxy": { "asset_id": "urn:d3d:manhattan-bridge:bridge_proxy", "max_level": 2 }
```

The consuming viewer enforces that cap, so publishing a proxy cannot cost you control over how much
of your model lands in someone else's frame budget.

---

## TASK 3 — Adopt the vertical datum offset (one field, no geometry change)

**This is a real defect, not a preference.**

`GEOMETRY-CONTROL.md` sets `z = 0` at **mean high water**. All NYC building data is **NAVD88**. Those
are different surfaces, **0.59 m apart** in New York Harbor.

Place the bridge without correcting and the whole structure sits 0.59 m low relative to the buildings
it lands between — larger than the ±0.61 m positional accuracy of the footprint data, and visible
where the bridge meets the anchorage plaza.

Two independent sources:

| Source | MHW above NAVD88 |
|---|---|
| NOAA CO-OPS station 8518750 (The Battery), epoch 1983–2001: `MHW = 2.44`, `NAVD88 = 1.85` | **0.59 m** |
| NYSAPLS datum sheet, same station | 0.596 m |

```
z_navd88 = z_mhw + 0.59
```

**Keep authoring against MHW.** Just declare it, and let the transform happen at placement time:

```json
"vertical_datum": "MHW"
```

The offset is already published in the canonical frame under `vertical_datum_offsets_m`, so consumers
read it rather than rediscover it:

```ts
frame.convertElevation(zMhw, 'MHW', 'NAVD88');   // adds 0.59
```

The `z` term in your `placement` is already `0.59` for exactly this reason. That part is grade A and
worth keeping regardless of what you decide about the horizontal terms.

---

## TASK 4 — Ratify, correct or replace the provisional placement (`OQ-009`)

Your `OQ-009` records that the bridge axis azimuth and geodetic anchor are unregistered. The DUMBO
team needed *something* to place the bridge, so they derived one and marked it clearly as a proposal:

| | |
|---|---|
| Method | principal axis of the OSM-mapped centreline, closed-form 2×2 covariance eigenvector |
| Input | 72 centreline points across 6 OSM ways |
| Azimuth toward Brooklyn | **157.37°** from north |
| `yaw_deg` (contract: CCW from East) | 292.63° |
| Mapped extent | 1,891 m, against your `CTL-001` total of 2,089 m — consistent; OSM omits some approach |
| Centroid | −73.990778, 40.707604 |
| Scene translation | `[-150.2, 511.3, 0.59]` |
| Grade | **D**, `provisional: true`, tagged `OQ-009` / `DOQ-001` |

Their script is `c:\Dev\dumbo-district-3d\scripts\propose_bridge_placement.py`; re-run it to
reproduce. Two caveats they state explicitly: OSM is community mapping, not survey; and the centroid
of the mapped centreline approximates but is not proven to equal your main-span midpoint origin.

Your options, in increasing order of rigour:

1. **Ratify as-is** — flip `provisional` to `false`, raise `confidence`, and close `OQ-009`. Only do
   this if you are comfortable with a community-mapped azimuth.
2. **Correct the numbers** and keep it registered, citing your own source in `notes`.
3. **Register a proper geodetic anchor** from an archival drawing or survey, retire `OQ-009` in
   `GEOMETRY-CONTROL.md`, and derive `placement` from that.

Whichever you choose, keep the honesty machinery: `confidence`, `provisional`, `open_questions` and
`notes` explaining the derivation.

---

## TASK 5 — Map `parts.json` onto the shared metadata contract

`viewer/metadata/parts.json` already carries almost everything
`c:\Dev\digital-3d-shared-contracts\schemas\metadata.schema.json` asks for. The mapping is mostly a
rename:

| Yours | Shared |
|---|---|
| `part_id` | `local_id`, and `asset_id` = `urn:d3d:manhattan-bridge:<part_id>` |
| `system` / `subsystem` | `taxonomy.system` / `taxonomy.subsystem` |
| `source_basis` | `source_basis` — your values are already a subset of the shared enum |
| `confidence`, `basis_confidence` | same |
| `control_refs`, `open_questions` | same |
| `review_status` | same |
| `last_modified_by_agent` | `last_modified_by` |
| `bbox_prototype_m` | `bbox` (with `frame`) |
| `prototype_units`, `ho_scale_units`, `bbox_ho_mm`, `scale` | `extensions["manhattan-bridge"]` |

HO scale is a **display** concern, not a data concern, which is why it moves under `extensions`. Your
own panels keep reading it there; the shared metadata panel can now read the common core, so a DUMBO
user clicking your tower gets a sensible record without the district team writing bridge-specific UI.

Also worth doing: your `CONFIDENCE-MODEL.md` was the basis for
`schemas/source-confidence.schema.json`. Adopting it regrades nothing — that was the design intent.

---

## TASK 6 — Optional: adopt `@d3d/viewer-kernel`

Not now. Worth it when Milestone 5 LOD switching lands, because the kernel already implements
screen-space-error selection, tile streaming and the mode-budget mechanism.

```ts
import { Frame, LodSelector, ModuleRegistry, TileStreamer } from '@d3d/viewer-kernel';
```

API reference: `c:\Dev\digital-3d-shared-contracts\VIEWER-API.md`. Working reference implementation:
`c:\Dev\dumbo-district-3d\viewer\src\`. Alias the packages the way their `vite.config.ts` does.

If you do adopt it, you get the shared tour player too, which means a tour can start on your bridge
and walk down into their neighbourhood.

---

## ACCEPTANCE

You are done with the required work when:

- [ ] `node tools\validate.mjs --schema module-manifest <your manifest>` prints `all documents valid`
- [ ] Your manifest is served, with CORS if cross-origin
- [ ] `viewer/public/frames/nyc-harbor-enu.json` hashes identically to the canonical frame
- [ ] A level-2 proxy GLB exists and is registered as `urn:d3d:manhattan-bridge:bridge_proxy`
- [ ] Bridge elevations declare `"vertical_datum": "MHW"`
- [ ] `placement` is either ratified or explicitly still `provisional: true`
- [ ] Nothing in your repository contains DUMBO geometry — buildings, streets, terrain are theirs

**End-to-end check.** Point the district at your manifest and run their viewer:

```powershell
cd c:\Dev\dumbo-district-3d\viewer
npm install
npm run dev          # http://localhost:5178
```

Press **▶ DUMBO in an hour — a family of four** and walk to stop D, Anchorage Place. The tour narrates
the handoff and calls `enter_inspect` against your `brooklyn_anchorage` entry point. Today that raises
an integration notice saying your module publishes no inspect UI. When your proxy and manifest are
live, the red wireframe is replaced by your geometry and the notice changes.

That notice is the specification for what to build after this.

## COPY TO HERE
