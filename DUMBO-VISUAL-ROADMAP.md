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

## 3. The sun is in one place and nothing casts a shadow — **partly done 2026-08-12**

**What was wrong.** `setTimeOfDay` moved a directional light along a fixed arc, and
`shadowMap.enabled = false`. The lighting half is now fixed; shadows are still off. See the lighting
section at the end of this document for what was done and why.

**What remains.** Enable shadow mapping for the directional light with a tight cascade around the
camera. At walking scale only the near tiles need to cast, so a single 2048 map over a 150 m box is
enough and costs one extra pass. This is now much more worthwhile than it was, because with a
physically-placed sun the shadows fall in the right direction at the right hour.

**Watch out for.** Turning shadows on will darken the scene, and the exposure balance will need
re-reading against `npm run test:lighting`.

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

## 5. Roofs are empty

`DOQ-012`. Roof *shape* is settled — 73 of 81 flat, measured — but DUMBO rooflines carry bulkheads,
stair houses, lift overruns and timber water tanks, and from the Manhattan Bridge that clutter is the
skyline. The NYC DCP LOD2 model has them and `rhino3dm` (MIT) is proven to read the format; the
download page has moved and needs locating.

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

1. **Shadows.** Still off, so a street canyon has no canyon. Much more worthwhile now that the sun is
   physically placed, because the shadows would fall correctly for the hour.
2. **Specular windows.** More visible now: a brighter sky gives glass something to reflect.
3. **Cloud cover from a weather API**, as above.
