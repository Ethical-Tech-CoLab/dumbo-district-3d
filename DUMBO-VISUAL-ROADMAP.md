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

## 3. The sun is in one place and nothing casts a shadow

**What is wrong.** `setTimeOfDay` moves a directional light, but `shadowMap.enabled = false`. Nothing
occludes anything. A street canyon in DUMBO is defined by the shadow the north side throws across it;
without shadows the massing has to be read from silhouette alone, which is why the screenshots look
flat even where the geometry is right.

**What fixes it.** Enable shadow mapping for the directional light with a tight cascade around the
camera. At walking scale only the near tiles need to cast, so a single 2048 map over a 150 m box is
enough and costs one extra pass.

**Watch out for.** The scene already uses ACES tone mapping tuned against unshadowed light. Turning
shadows on will darken everything and the exposure will need re-checking against the measured paving
colour, exactly as when tone mapping went in.

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
