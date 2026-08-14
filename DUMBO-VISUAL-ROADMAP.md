# What would most improve how DUMBO looks, in order

Written after three photo sweeps and a boundary correction, against the state of the model rather
than against a wishlist. Each item says what is wrong now, what fixes it, and what evidence exists,
because an improvement with no source behind it is decoration and this project grades decoration
honestly.

The three that follow are worth more than everything below them put together.

---

## 1. The ground floor is the same as the fifth floor

**What is wrong.** A walker sees the bottom four metres of a building and almost nothing else. Right
now that band is drawn exactly like every band above it: same colour, same window rhythm, same flat
plane. `facadeBandFactor` already darkens the ground storey slightly and that is the whole treatment.
Real DUMBO at street level is shopfront glass, roll-down shutters, loading bays, painted signage,
stoops and areaways — the most visually dense part of the district and currently its blandest.

**What fixes it.** A distinct ground-floor treatment driven by data already ingested: 432 businesses
from `DSRC-017` say which frontages are retail; `DSRC-018` gives `build_type`, which separates a row
house's stoop and areaway from a factory's loading bay. Full-height glazing where there is a shop, a
recessed dark band with a wide opening where there is a loading bay, a stoop and railing where the
register says Row House.

**Why it is first.** It is the band the eye actually spends its time on, and both sources are already
on disk. No new ingest, no licensing question, no new dependency.

---

## 2. Everything is matte, so nothing reads as glass

**What is wrong.** Every surface in the scene is `MeshLambertMaterial` — pure diffuse. Windows are
darker paint. A real street reads partly through *specularity*: glass catches the sky, wet cobbles
catch the sun, painted metal railings glint. Without any of it the district looks like a scale model
in cardboard, which is exactly the note "it looks like boxes" was pointing at even after the window
bays went in.

**What fixes it.** Move window bands to a material with a specular response and give them an
environment reflection — even a two-colour gradient standing in for sky and ground is enough at this
scale. The window band factor already identifies which triangles are glass, so the classification
work is done; this is a material swap plus a cheap environment map.

**Cost.** One extra material and one small cube texture. No new geometry, no new data. This is the
highest ratio of visible improvement to work in the whole list.

---

## 3. The sun is in one place and nothing casts a shadow — **done 2026-08-12**

**What was wrong.** `setTimeOfDay` moved a directional light along a fixed arc, and
`shadowMap.enabled = false`. Both halves are now fixed. See the lighting section at the end of this
document for the sun placement, and the shadow section below it for the rest.

**What it cost.** 1.17 ms per frame, measured: 5.72 ms with shadows off, 6.89 ms on. Rebuilding the
shadow map every frame instead costs 8.85 ms, so caching it saves about 63% of the shadow overhead.

**What was learned.** Almost nothing about this was hard except finding out what was true. The
detailed account is at the end of this document, but the short version: three separate "shadows are
broken" conclusions during the work were all measurement errors, and the one real defect the work
uncovered — walls going near-black — was a genuine gap in the lighting rig rather than in shadows.

---

## 4. Facade colour is uniform across each building — **done 2026-08-10**

Every wall of a building was one flat colour with a shading factor by orientation, so a street of
brick warehouses was one tone repeated forty times. The corpus measures a *range*, not just a mean —
brick runs `#42332a` to `#a87158`, nearly black to pale buff — and publishing only the mean threw
that away.

Each inferred and designated facade is now placed at a deterministic point in the measured range,
keyed by its own BIN so the same building is the same colour on every machine and every rebuild.
**420 distinct colours across 446 buildings**, up from about a dozen. Blended at 45% rather than
taken literally, because the extremes of the range are single photographs; measured afterwards,
lightness spans 0.22–0.56, saturation tops out at 0.56, and no facade left the warm masonry hues.

Observed facades are exempt: a colour measured from a photograph *of that building* is the strongest
evidence there is, and spreading it towards a district average would be a downgrade.

## 5. Roofs are empty — **done 2026-08-13**

`DOQ-012`, closed. Roof *shape* was already settled — 73 of 81 flat, measured — but DUMBO rooflines
carry bulkheads, stair houses, lift overruns and timber water tanks, and from the Manhattan Bridge
that clutter is the skyline. **103 of them** now stand on the roofs, each with a surveyed position,
plan extent, orientation and height. Hiding them changes 1.1–2.7% of a frame, which is the right
scale for a detail that is silhouette rather than bulk.

The source is the NYC DCP 3-D Building Model (`DSRC-020`), whose download page had moved; it is
found, and `scripts/extract_rooftop_structures.py` reduces the 672 MB Rhino file to a 21 KB committed
artifact so the heavy file is needed once rather than at every build.

**The lesson worth keeping is about datums.** The model's absolute heights and ours disagree by
5–13 m — it is a 2014 survey, ours is the current footprint release — so anchoring to its Z put every
bulkhead *inside* its building. What a survey is reliable about is the *difference* it measures
against itself, so the rise comes from the model and the base from our own geometry. Then the base
has to be the roof **deck**, which the viewer draws a parapet below the declared top (`DCTL-081`);
using the declared top left all 103 floating by exactly 0.90 m. They now sit within ±1 cm.

## 6. Nothing moves except the ferries

No pedestrians, no traffic, no foliage sway. A district with no people in it reads as evacuated. The
walk network (`5,026 nodes`) is already a graph pedestrians could be walked along, and the prop
contract already has a `person` kind. Even a dozen figures at plausible density changes the feel of a
plaza completely. Grade D, decorative, never citable — and worth saying so loudly in the source
register.

## 7. The water is a flat plane

`water.json` places a surface at mean high water with vessels on it. There is no wave, no wake, no
reflection. The East River is a third of the view from the promenade. A scrolling normal map and a
horizon-coloured reflection would do most of the work.

## 8. The unplaced half of the corpus

101 of 193 photographs have no position, so they can inform a palette but can never be attached to a
building. Mapillary (`DSRC-019`, recommended in `DUMBO-DATA-OPTIONS.md`) publishes camera position
*and* compass bearing per image, which is what turns "this is what DUMBO brick looks like" into "this
is what *that wall* looks like". It is the only route that unlocks per-building facade texture.

---

## What is deliberately not on this list

**Downloaded tree models.** Kenney's Nature Kit (CC0, 330 models, GLB) and Quaternius's stylised
nature pack are both genuinely free and were checked. They were not adopted, for three reasons that
are worth stating because "download a nice tree" is the obvious first instinct:

1. **They are stylised, not species.** A CC0 pack gives you "tree A" and "tree B". The census gives
   1,306 trees with a *species* each, across 49 genera. Mapping a London plane, a ginkgo and an
   eastern redbud onto three interchangeable stylised meshes throws away the only thing that makes
   the planting real, and it would look *more* uniform, not less, because a pack has fewer distinct
   forms than DUMBO has genera.
2. **Seasons multiply the problem.** Four seasons across 29 genera is 116 variants. No free pack
   carries that, so the colours would have to be authored anyway — at which point the mesh is the
   only thing being downloaded, and a procedural crown scaled by real trunk diameter is a better
   mesh for this purpose than a fixed-size stylised one.
3. **1,306 instanced draws.** The scene budget is the reason the canopies are low-poly in the first
   place. A pack model is typically 1–3k triangles; the current crown is 40, and the whole prop set
   is 134 draw calls.

A downloaded model becomes the right answer at the point where a *specific* tree matters — a named
specimen in the park, say — rather than 1,306 street trees whose value is in being individually
sized and correctly speciated. The prototype already carries a `url`, so swapping one in is a
one-field change.

**Photorealistic textures.** The project's grading discipline says a texture is a claim about a
specific building, and a stock brick texture on 446 buildings is a claim that is false 446 times.
Measured colour with honest banding is weaker-looking and more truthful. That trade should only be
revisited with rectified per-building photography, which is item 8's endgame.

**More photo sweeps of Wikimedia Commons.** Three sweeps have taken it; 414 files are in the
do-not-source ledger and the last sweep returned 70 new candidates from four fresh categories, most
of the rest being duplicates or out-of-district. The next photograph gain comes from Mapillary or
from volunteers, not from another category query.

---

## A note for whoever promotes the photo pipeline

The bridge team is generalising `scripts/ingest_photos.py` and `scripts/build_photo_corpus.py` into
`digital-3d-shared-contracts/tools/photo-survey/`, which is the right move — the approach has now
been proved on 537 reviewed photographs and nothing in it is district-specific except the shot list.

**Two fixes made on 2026-08-10 must travel with it**, because both are the kind of bug that is
invisible until it has quietly cost you something:

1. **The palette download cap is not a sampling strategy.** Capping decoded thumbnails at a fixed
   number and iterating in dictionary order lets the abundant material crowd out the scarce one. It
   silently held foliage at 2 photographs while 8 were available, and the measured colour appeared to
   revert between builds that differed in nothing else. Decode scarce materials first.

2. **An automatic screen must never overturn a human answer.** Dropping candidates whose published
   coordinates fall outside the module's boundary is a good filter — it took 63 photographs off the
   reviewer's desk in one sweep — but applied naively it also deleted fourteen the reviewer had
   already accepted, because Commons frequently geotags a photograph at its *subject* rather than at
   its camera. Anything a reviewer accepted is exempt from every automatic screen.

---

## Lighting — done 2026-08-12, and what could follow

The district looked dark and the sky dull, and the cause was measurable rather than a matter of
taste. The old rig swept a light along a fixed arc and painted a flat blue-grey behind it. At its own
default of 16:30 it put the sun 20 degrees above the horizon and rendered a **sunlit brick wall at
lightness 0.11**, where brick actually photographs around 0.40. A shaded wall came out at 0.03, which
is black.

Three faults, each a trap this kind of scene falls into repeatedly:

1. **The sun was in the wrong place, always.** A fixed arc has no latitude, no date and no azimuth.
   DUMBO's real sun runs from 72.7 degrees at midsummer noon to 25.8 at midwinter noon, and for a
   street grid the *azimuth* matters more than the altitude -- Washington Street runs north-west, so
   it fills with light only for part of the day. Replaced with real solar geometry in `Sky.ts`,
   accurate to about a tenth of a degree.

2. **The sky was authored at "photograph of a blue sky" values, which read as dusk.** A rendered sky
   has to be brighter than intuition suggests. It is now a gradient dome, pale at the horizon and
   deep overhead, which matters here because half the view is across open water and a flat fill reads
   as a painted backdrop.

3. **Exposure did not move.** Every hour rendered at the same apparent brightness, so evening was
   noon with orange lights. Exposure now falls with the sun, and also eases off when the sun is high,
   because a horizontal surface takes the full beam at noon while a facade takes a glancing fraction.

### The one that cost the most time

A hemisphere light **cannot serve both a wall and a pavement**. It gives a horizontal surface the
full sky term and a vertical one about half, so any value bright enough to keep a shaded facade off
black blows out every pavement: at one point concrete authored at lightness 0.26 rendered at **0.84**,
which is white. This project has now hit that trap twice.

The fix is a **fill light** -- a weak second sun, aimed opposite the real one and 12 degrees up. It
lands almost entirely on the vertical faces the sun is not reaching and contributes little to the
ground, which is nearly edge-on to it. Two further details, both counter-intuitive:

- Its intensity must **rise** with the sun rather than stay fixed. Exposure is being pulled down at
  noon to protect the pavement, and that pull applies to shaded walls too; without compensation the
  shaded side of every building gets darker as the day gets brighter.
- Its tint must be **near-white, not sky blue**. three.js multiplies light colour by surface colour in
  linear space, where a mid-blue sky is about 0.18 and brick is about 0.18 -- so a sky-tinted fill
  delivers roughly 3 per cent of the energy its numbers suggest. That is why raising intensity alone
  never worked.

`npm run test:lighting` asserts the result across nine sun positions, so the next person to touch the
rig finds out immediately rather than by squinting at a screenshot.

### Real-world lighting

`live sun` is the default: the district opens under the sun actually over DUMBO right now. One solar
calculation, no network.

**Real weather is the obvious next step and is deliberately not done yet.** A free tier of Open-Meteo
or similar gives cloud cover, and cloud is what decides whether the sun is a hard source or a soft
one -- an overcast day is not a dimmer sunny day, it is a different light, with no shadows and a
bright uniform dome. That is a genuine addition rather than a tweak: it means crossfading between a
directional-dominant and a hemisphere-dominant rig. It also introduces a network dependency and an
API key into a viewer that has neither, which is a real cost for a project otherwise strict about
provenance.

If it is wanted, the honest shape is: fetch cloud cover and visibility, map cloud to a sun/sky balance
and shadow softness, cache for an hour, and **grade the result D** -- a real measurement of the sky
over DUMBO, but nothing in the geometry depends on it.

### What follows, in order

1. **Specular windows.** More visible now: a brighter sky gives glass something to reflect, and
   shadows give the reflections somewhere dark to sit against.
2. **Cloud cover from a weather API**, as above.

---

## Shadows, 2026-08-12

Switched on for the sun only. `PCFSoftShadowMap`, one 2048 map over a 220 m box that follows the
viewer, buildings and the larger props casting, all ground and facades receiving.

### What it costs

Measured in the browser, median of seven alternating rounds, GPU forced to sync with `readPixels`:

| | ms/frame |
|---|---|
| shadows off | 5.72 |
| shadows on, map cached | 6.89 |
| shadows on, map rebuilt every frame | 8.85 |

So shadows cost 1.17 ms, and **caching the map saves 63% of that cost**. This is why
`shadowMap.autoUpdate = false`: the sun is static between preset changes, so the map only needs
redrawing when the viewer moves far enough, a tile arrives, the props rebuild, or the light changes.
Everything that can invalidate it calls `invalidateShadows()`. Confirmed at the draw-call level --
a cached frame issues 192 calls, a frame that rebuilds the map issues 342.

### Decisions worth keeping

- **Aim the box ahead of the camera, not at it.** Centring on the camera spends half the box behind
  the viewer's head, and from any raised viewpoint the ground actually in frame falls outside the box
  and silently loses its shadows. The lead scales with eye height, because that is what decides how
  much ground the view covers.
- **Snap the box in whole 16 m strides.** A shadow map that shifts a fraction of a texel per frame
  shimmers along every straight edge, and DUMBO is nothing but straight edges.
- **`normalBias` over `bias`.** The district is full of thin geometry -- awning canopies at 0.08 m,
  fence rails at 0.04 m.
- **Let the data decide what casts.** `casts_shadow` is already in the contract, so the viewer reads
  it off the prototype instead of keeping its own list of prop kinds. `build_scene_dressing.py` is
  the authority. A shared kernel has no business knowing that a district has bollards or that a
  bridge has gantries. Bollards, bins, hydrants and benches receive but do not cast: a few pixels
  each, and there are hundreds of them.
- **The far field must not cast.** Horizon blocks 1-3 km out would wreck the frustum. They default
  to not casting, which was verified rather than assumed.

### The one real defect this uncovered

Turning shadows on took the darkest walls to **lightness 0.078**, under the 0.09 floor. The cause was
in the lighting rig, not in shadows: all three lights are directional in effect -- the sun has a
direction, the fill has a direction, and a hemisphere light gives a vertical surface only about half
the sky. A wall turned away from both sun and fill therefore had almost nothing left. It had never
mattered, because without cast shadows such a wall always caught some sun.

Note *why* the fill could not rescue it: the fill sits opposite the sun, so the wall it cannot reach
is precisely the one that faces the sun's side of the street and stands in a taller building's
shadow. That is a common wall in DUMBO, not a corner case.

The fix is a fourth light: a small omnidirectional `bounce` term, which is the physical stand-in for
sunlight traded back and forth between facing buildings in a canyon. At 0.35 it lifts the worst walls
from 0.078 to 0.147 and costs 7 points of shadow contrast on the ground (44% down to 37%), which
still reads unmistakably as shadow. **Keep it small**: bounce is the one term a shadow cannot
occlude, so every unit of it is a unit of contrast removed from every shadow in the district.

The test gained an `occluded` column for exactly this surface, and a `shadowed` column for pavement
in shade. It also gained a correction that predates this work: it was giving a vertical surface half
the sky and *none* of the hemisphere's ground bounce, where three.js mixes both by the normal's tilt.

### How much of this was real

Three separate times during this work the conclusion "shadows are broken" turned out to be a
measurement error, and each cost more than the implementation did:

1. **Screenshots of a WebGL canvas in a backgrounded tab are stale.** Zeroing both the fill and the
   hemisphere changed nothing on screen, which is what finally gave it away. Every screenshot before
   that had been an old frame. The fix is to render synchronously via `captureFrame()`, push the
   result into an `<img>`, and `await img.decode()` before screenshotting -- the compositor will
   repaint a plain DOM image even when it will not repaint the canvas.
2. **`lookAt` straight down is a gimbal singularity.** It produced a degenerate camera matrix, so
   nothing rendered, and `preserveDrawingBuffer` faithfully returned the previous frame. Two
   "identical" captures that should have differed.
3. **"Props cast no shadows at all", twice.** The first test left props casting in both arms of the
   comparison. The second was valid but ran in a street already entirely inside a building's shadow,
   where prop shadows are genuinely redundant. Measured properly, against sunlit ground, props
   account for 1.4% of the frame.

The lesson that generalises: **when a renderer appears to be wrong, measure a number before forming
an opinion.** `shadowDiagnostics()` exists for this -- it reports whether the renderer draws shadows,
whether the light casts, how many meshes cast, and how many of them fall inside the shadow box, which
is the complete list of ways a missing shadow can happen. The very first call to it showed
`sunIntensity: 0` and the sun 199 m below the horizon: the default is `live sun`, and it was night.

---

## Water, bridges and the ground floor, 2026-08-14

### Why Poseidon was read carefully and then not used

`owenyuwono/poseidon` was suggested as a reference for the water. It is MIT licensed and genuinely
good: a Tessendorf/Horvath FFT ocean, JONSWAP spectrum with TMA depth correction, three wave
cascades at 250 m / 17 m / 5 m, Jacobian foam, and a half-vector subsurface approximation.

Two findings ruled it out, and neither is a criticism of it:

1. **It is WebGPU only, and says so.** The FFT runs as `renderer.compute()` dispatches -- 19 per
   frame at N=256 -- and WebGL2 has no compute shaders. The project deliberately aborts if it
   detects a WebGL2 fallback. This viewer is a WebGL2 `WebGLRenderer`.
2. **It does not reflect anything.** Its water samples an *analytic sky colour* along the reflected
   ray. No planar reflection, no SSR, no cubemap. That is the right call for open ocean with nothing
   to reflect, and exactly wrong for a 500 m river with the Brooklyn Bridge over it -- which means
   the one thing that was asked for is the one thing it does not do.

It is also an open-ocean model: 810,000 vertices over a 400 m patch, with 250 m swell. The East
River is a tidal strait seen from a promenade.

So the water is written here: procedural ripple from four summed directional waves (no texture, so
nothing to vendor and nothing to licence), a real planar reflection, Fresnel, and sun glitter that
follows the same solar rig as everything else.

### The reflection had to be paid for, and the bill was itemised

A planar reflection is a second render of the scene. Measured:

| | ms/frame |
|---|---|
| no reflection | 7.0 |
| reflecting everything | 30.9 |
| reflecting everything but props and paving | 13.0 |

**Reflecting the whole scene cost 24 ms -- three and a half times the entire rest of the frame.**
Almost all of it was street props: 26,000 instanced trees, railings, bollards and bins, none of
which are visible in a river from any viewpoint in this district. Excluding props and paving takes
the pass to **5.95 ms** and removes nothing a person would look for. Buildings, both context
bridges, the Manhattan skyline and the sky all still reflect, because those are what the water is
for.

A wrong turn worth recording: the first suspicion was that toggling `renderer.shadowMap.enabled`
around the reflection pass was recompiling every material, which is a real three.js trap. It was
measured and it was not the cause here -- but the toggle was removed anyway, because the shadow map
is not rebuilt for that pass in any case.

### Brooklyn and Williamsburg are context, and say so

The Manhattan Bridge has an owning module and arrives as that module's proxy; DUMBO-SCOPE.md is
explicit that its towers and cables are not ours. Nobody owns the other two, and leaving them out
does not make the view neutral -- the Brooklyn Bridge closes the view from Fulton Ferry and is half
of why anyone stands there.

So they are built like the Manhattan skyline: **grade C context from the mapped centreline**, with
a conventional suspension form fitted to published dimensions. If either ever gets a module, this is
deleted and the proxy takes over.

Two measurement notes:

- The longest OSM way for each bridge is the `man_made=bridge` **area**, which closes on itself.
  Resampling around it produced a deck following the outline and towers 40 m apart instead of 486.
  A centreline is now required to carry a road and not return to where it started.
- Tower positions are measured where the data allows. OSM maps the Williamsburg main span as its own
  way -- 488 m against a published 1,600 ft -- so its endpoints *are* the towers. Brooklyn has no
  such way, but its longest roadway is 1,054 m against a published main span plus two side spans of
  1,053 m, so that way is the suspended structure and the towers sit at the side-span offsets.
  Both come out within a metre of the published main span.

Detail follows distance: suspenders inside 900 m, cables inside 3.2 km, silhouette beyond. The
Williamsburg Bridge is 1.9 km from the district origin and never draws its suspenders from anywhere
in DUMBO.
