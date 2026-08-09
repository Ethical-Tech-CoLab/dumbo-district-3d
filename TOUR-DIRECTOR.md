# Tour Director

Driving the DUMBO viewer from external walking instructions.

The contract is `digital-3d-shared-contracts/TOUR-SCRIPT.md`. This file is the district-specific
companion: what exists here to point a tour at, and how to build one.

---

## 1. What ships

| Tour | Stops | Distance | Duration |
|---|---:|---:|---|
| `dumbo-family-of-four` | 4 | 871 m | 13.8 min walking + 4.3 min dwelling = 18:10 |
| `dumbo-step-free-short` | 2 | 183 m | a short level loop, wheelchair profile |

Both are in `viewer/public/tours/`, listed in `tours/index.json`, and both validate against
`tour-script.schema.json`.

### The family of four

> *"A family of 4 takes a tour starting at Brooklyn Bridge and hitting A, B, C, D stops, dwelling at
> each spot and taking pics."*

| | Stop | Position | Dwell | Beats |
|---|---|---|---:|---|
| **A** | Fulton Ferry Landing, under the Brooklyn Bridge | −73.995089, 40.703347 | 55 s | narrate · narrate · pan east along the waterfront · photo |
| **B** | Jane's Carousel, Brooklyn Bridge Park | −73.992385, 40.704434 | 60 s | narrate · look at the carousel · group photo · look at the Manhattan Bridge · photo |
| **C** | Washington Street at Water Street | −73.989580, 40.703201 | 70 s | narrate · pan north · portrait photo · group photo · golden hour · photo |
| **D** | Anchorage Place, beneath the Manhattan Bridge | −73.988012, 40.703296 | 75 s | narrate · look up · photo · **hand off to the bridge team's inspect mode** · wait for the user |

A note on stop A: the narration mentions the Brooklyn Bridge because that is genuinely where the party
is standing, but the camera deliberately pans *east along the waterfront* rather than up at the bridge.
The Brooklyn Bridge is not owned by any module in this stack and is therefore not rendered; aiming a
scripted photograph at geometry that does not exist would produce a picture of empty sky. A tour
should only frame assets some module actually publishes.

Three of the four positions are real OpenStreetMap named features. C is the surveyed
Washington × Water intersection, computed from the street network — the shot the neighbourhood is
famous for, where the bridge frames the Empire State Building.

The party is four people with individual eye heights, walking at 1.05 m/s (`DCTL-053`, a family
sightseeing, not a commuter), with `avoid_stairs: true`. Playback defaults to 4×, so the eighteen
minutes fit a four-and-a-half minute demo without pretending the walk is shorter than it is.

Stop D is the interesting one: the tour deliberately walks to the edge of what this module owns,
says so out loud in the narration, and hands over.

---

## 2. Building a tour

```bash
python scripts/build_tour.py
```

`build_tour.py` is a router, not a document. It:

1. Loads the pedestrian graph from `walk-network.json` (4,346 nodes, 5,131 edges, from OSM).
2. Routes each leg with A*, weighting footways below streets so a walking tour prefers a path.
3. Trims overshoot at both ends, so the nearest graph node sitting past a stop does not produce a
   spurious doubling-back manoeuvre before arrival.
4. Splits the path into maneuver steps at turns over 35° and at street-name changes.
5. Phrases instructions the way a directions API does — *"Turn left onto Adams Street"*.
6. Carries street names forward across unnamed connectors, and never backwards. An unnamed kerb cut
   between two blocks of Water Street is still Water Street; a park path is not.
7. Layers on the experience: dwell times, narration, look-at targets, photo moments, the handoff.

Sample output:

```
[depart          ] Head east on Water Street       (12.5 m)
[turn-slight-right] Bear right onto Water Street   (51.9 m)
[turn-left       ] Turn left onto Adams Street     (19.8 m)
[turn-left       ] Turn left onto Dumbo Archway    (46.7 m)
[arrive          ] Arrive at D · Anchorage Place   (0.0 m)
```

**To use a real provider** — Google, Bing, Apple, OSRM — rewrite `route_leg` and change nothing else.
The output shape is already a directions response.

---

## 3. Pointing at things

```json
{ "lon": -73.98958, "lat": 40.7032, "height_m": 0, "vertical_datum": "NAVD88" }
{ "frame": "nyc-harbor-enu", "xyz": [-49.2, 22.1, 5.0] }
{ "asset": "urn:d3d:dumbo-district:landmark_janes_carousel" }
{ "asset": "urn:d3d:manhattan-bridge:bridge_proxy" }
```

Prefer geodetic for portability. Use an asset URN for "look at that thing".

**29 landmark URNs** are published in `asset-registry.json`, derived from OSM named features:
`landmark_janes_carousel`, `landmark_fulton_ferry_landing`, `landmark_empire_fulton_ferry`,
`landmark_brooklyn_bridge_park`, `landmark_manhattan_bridge_viewpoint`, and so on. Every building is
also addressable as `urn:d3d:dumbo-district:bldg_<BIN>`, resolved from resident tiles.

Cross-module URNs resolve through the manifest. A district tour can aim a camera at a bridge tower
without the district owning a triangle of it.

---

## 4. What the viewer does with each action

| Action | Here |
|---|---|
| `look_at` | camera tracks the target, eased so a scripted turn reads as a head turn rather than a snap |
| `pan` | absolute heading and pitch |
| `narrate` | caption over the scene; duration defaults to reading time |
| `capture_photo` / `group_photo` | real framebuffer grab, cover-fitted to the requested aspect, added to the photo strip |
| `highlight` / `show_metadata` | selects the asset, opens the metadata panel |
| `set_time_of_day` | moves the sun, recolours sky and fog |
| `set_mode` | changes the LOD budget |
| `enter_inspect` | switches to inspect budget, lifts the proxy cap, emits `handoff:enter`; today this also raises an integration notice because the bridge module ships no inspect UI yet |
| `wait_for_user` | pauses with a continue button |

---

## 5. Playback controls

Play/pause, previous/next stop, restart, and speed from 1× to 16×. During travel the panel shows the
stop being left and the one being approached, plus distance remaining. Seeking marks skipped actions
as fired so nothing replays out of order.

---

## 6. Why this is a contract and not a feature

A tour crosses modules. It starts in the district, points at the bridge, and hands off to the bridge
team's viewer. If tour playback lived in this repository, the bridge team would have to reimplement
it to run a tour that starts on the bridge.

So the player is in `@d3d/viewer-kernel` and the format is in the shared schemas. This repository
supplies only the things a shell can know: where its assets are, how to route on its streets, and how
tall the ground is.
