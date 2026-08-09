# Coordination Note for the Manhattan Bridge Team

From: `dumbo-district-3d`
Re: integrating the two digital twins without either team rebuilding the other's work

---

## What we did

We built the DUMBO district twin as an independent module and set up a shared contracts repository at
`digital-3d-shared-contracts` for the things both projects must agree on. We did **not** model any
part of the bridge, and we do not intend to.

We also found and fixed a problem that would have bitten both of us later. Details in §3.

**A ready-to-run version of this note, written as an agent prompt with exact URL structure and
copy-paste commands, is in [BRIDGE-TEAM-PROMPT.md](BRIDGE-TEAM-PROMPT.md).**

---

## What we need from you

Two items deliver most of the value. Neither requires changing any geometry.

### 1. Publish `bridge-manifest.json` — about 60 lines

One document is the entire dependency surface between our projects. It declares what you own, which
modes you support, your LOD ladder, and where you can be handed off to.

We have written a **placeholder** version at
`dumbo-district-3d/viewer/public/modules/manhattan-bridge/bridge-manifest.json`. Copy it into your
repository, correct it, and serve it. Point us at the URL and we delete ours.

Validate with:

```bash
cd digital-3d-shared-contracts
node tools/validate.mjs --schema module-manifest path/to/bridge-manifest.json
```

### 2. Export a level-2 proxy — one decimated GLB

A single low-detail mesh of the whole bridge, a few thousand triangles, no rivets. This is what a
pedestrian on Washington Street actually sees, and it is the highest-value artefact you can give us.

Right now we draw a red wireframe envelope, sized from your own `CTL-001`, `CTL-005` and `CTL-007`,
clearly labelled as a placeholder. It is deliberately ugly so nobody mistakes it for your model.

Your manifest already caps how far we may refine it:

```json
"proxy": { "asset_id": "urn:d3d:manhattan-bridge:bridge_proxy", "max_level": 2 }
```

We enforce that cap, so publishing a proxy cannot cost you control over how much of your model ends
up in someone else's frame budget.

---

## 3. The datum problem, and the number that fixes it

**`GEOMETRY-CONTROL.md` sets `z = 0` at mean high water. All NYC building data is NAVD88. Those are
different surfaces, 0.59 m apart in New York Harbor.**

Placing the bridge in the district without correcting for it puts the entire structure 0.59 m low
relative to the buildings it lands between — larger than the ±0.61 m positional accuracy of the
footprint data, and visible where the bridge meets the anchorage plaza.

The value, with two independent sources:

| Source | MHW above NAVD88 |
|---|---|
| NOAA CO-OPS station 8518750 (The Battery), epoch 1983–2001: `MHW = 2.44`, `NAVD88 = 1.85` | **0.59 m** |
| NYSAPLS datum sheet, same station | 0.596 m |

```
z_navd88 = z_mhw + 0.59
```

It is registered as `DCTL-010` in our control document and published in the shared georeference under
`vertical_datum_offsets_m`, so it is a field read rather than a discovery:

```ts
frame.convertElevation(zMhw, 'MHW', 'NAVD88');
```

**You do not need to change any geometry.** Keep authoring against MHW. Declare
`"vertical_datum": "MHW"` and let the transform happen at placement time. That is what the field is
for.

---

## 4. OQ-009 — a starting point, not an answer

Your `OQ-009` records that the bridge axis azimuth and geodetic anchor are unregistered. We needed
*something* to place the bridge, so we derived a **provisional** placement and marked it as such.

Method (`scripts/propose_bridge_placement.py`): fetch the Manhattan Bridge centreline from
OpenStreetMap, project to our scene frame, and take the principal axis of the point set by the
closed-form eigenvector of its 2×2 covariance.

| | |
|---|---|
| Centreline points | 72, across 6 OSM ways |
| Fitted azimuth toward Brooklyn | **157.37°** from north |
| `yaw_deg` in contract terms | 292.63° counter-clockwise from East |
| Mapped extent | 1,891 m (your `CTL-001` total is 2,089 m — consistent, OSM omits some approach) |
| Centroid | −73.990778, 40.707604 |
| Scene translation | `[−150.2, 511.3, 0.59]` |

Graded **D**, `provisional: true`, tagged `OQ-009` and `DOQ-001`. Two honest caveats:

- OSM is community mapping, not survey. Good enough to see the bridge from a street; not good enough
  to measure anything.
- The centroid of the mapped centreline **approximates but is not proven to equal** the midpoint of
  the main span that you use as your origin.

The `z` term is different: `0.59` is the datum correction from §3, and that part is grade A and worth
adopting regardless of what happens to the horizontal terms.

Ratify it, correct it, or replace it. It is a one-line edit either way, and nothing in our module
treats it as truth.

---

## 5. What you do not have to change

- **Your coordinate frame.** Origin at main-span midpoint, `+X` toward Brooklyn, `+Z` up. Keep it.
  The contract composes it via `placement`. The shared scene frame `nyc-harbor-enu` is published
  canonically at `digital-3d-shared-contracts/frames/nyc-harbor-enu.json`; you reference it, you do
  not author it.
- **Your geometry, build scripts, or `GEOMETRY-CONTROL.md`.**
- **Your confidence model.** The shared `source-confidence.schema.json` was generalised *from* your
  `CONFIDENCE-MODEL.md` specifically so you can adopt it without regrading anything. Your
  `source_basis` values are a subset of the shared enum.
- **Your viewer.** It works. Adopting `@d3d/viewer-kernel` is worth doing when Milestone 5 LOD
  switching lands, since the kernel already implements it — but that is later, and optional.

Mapping `parts.json` onto `metadata.schema.json` is a field rename; the HO-specific fields move under
`extensions["manhattan-bridge"]`, where your own panels keep reading them and our shared panel can
read the common core.

---

## 6. The design argument, briefly

Two viewer modes look like they need two viewers: CAD inspection wants maximum geometry, a district
walkthrough wants minimum. They do not. They want **the same LOD ladder with different error
budgets**:

```json
"mode_sse_budget_px": { "inspect": 2, "walk": 12, "map": 48, "tour": 8 }
```

Every level from both modules sits on one axis, `max_geometric_error_m`. Your LOD0 control skeleton at
0.01 m and our context blocks at 8 m are points on the same line. Selection is standard screen-space
error. Changing mode changes one number.

The full argument, including why the alternatives are worse, is in
`digital-3d-shared-contracts/VIEWER-MODES.md`.

---

## 7. Try it

```bash
cd dumbo-district-3d/viewer && npm install && npm run dev
```

Open http://localhost:5178, press **▶ DUMBO in an hour — a family of four**, and walk to stop D. The
tour narrates the handoff, calls `enter_inspect` against your `brooklyn_anchorage` entry point, and
raises an integration notice saying your module publishes no inspect UI yet.

That notice is the specification for what to build next.

---

## 8. Summary

| Priority | Action | Effort |
|---|---|---|
| 1 | Publish `bridge-manifest.json` | ~60 lines |
| 2 | Export a level-2 proxy GLB | one decimation pass |
| 3 | Adopt `MHW = NAVD88 + 0.59 m` | one field |
| 4 | Ratify or correct the provisional placement (`OQ-009`) | one line, or a survey |
| 5 | Map `parts.json` onto `metadata.schema.json` | field renames |
| 6 | Consider `@d3d/viewer-kernel` at Milestone 5 | optional |

Items 1 and 2 alone make the integration real.
