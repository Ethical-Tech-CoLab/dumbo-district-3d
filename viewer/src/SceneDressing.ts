/**
 * Scene dressing: paving, street trees and facade appearance.
 *
 * The viewer stays asset-agnostic. It knows the prop *vocabulary* from scene-props.schema.json and
 * how to draw a paved quad, but it knows nothing about DUMBO: no tree species list, no street
 * names, no building classes are hard-coded here. Point it at another district's props.json,
 * paving.json and facades.json and it dresses that district instead.
 *
 * Everything is instanced or merged. 1,252 trees are three draw calls, not 1,252.
 */

import * as THREE from 'three';
import type { ScenePropSet, ScenePrototype } from '@d3d/contracts';
import { Frame } from '@d3d/viewer-kernel';

export interface PavingSurface {
  kind: string;
  name: string | null;
  /** Flat quads: [ax, ay, bx, by, cx, cy, dx, dy] in scene meters. Fallback format. */
  quads: number[][];
}

/** A surveyed surface polygon: an outer ring in scene metres. */
export interface PavingPolygon {
  kind: string;
  name: string | null;
  /** What OSM says the ground is made of, where a way could be matched to this polygon. */
  surface?: string;
  ring: Array<[number, number]>;
}

/** A surveyed kerb line, extruded to a face at render time. */
export interface PavingKerb {
  line: Array<[number, number]>;
}

export interface PavingDocument {
  /** Surveyed polygons, preferred. */
  polygons?: PavingPolygon[];
  kerbs?: PavingKerb[];
  kerb_height_m?: number;
  /** Widened-centreline quads, used when the planimetric layers were not ingested. */
  surfaces?: PavingSurface[];
  attribution?: string;
}

export interface FacadeStyle {
  family: string;
  color: string;
  glazing: number;
  era: string;
  floors?: number;
  /**
   * "inferred" when the appearance came from building class and age, "observed" when a photograph
   * of the building supplied it, "designated" when the city's landmark register named the material.
   * Observed styles are held against the procedural pass rather than being regenerated, so a real
   * facade is never silently replaced by a guess.
   */
  basis?: 'inferred' | 'observed' | 'designated';
  observed_grade?: string;
  observation_id?: string;
  attribution_text?: string;
  observed_distance_m?: number;
  colour_source?: string;
  /**
   * Bay pitch in metres: the spacing of the punched openings across the facade. From the city's
   * designation register, which names the building type, and the single number that most decides
   * whether a wall reads as a row house or a factory.
   */
  bay_m?: number;
  material?: string;
  designated_style?: string;
  designated_type?: string;
  designation?: string;
}

export interface FacadeDocument {
  styles: Record<string, FacadeStyle>;
  /** Parapet rim height above the roof deck, DCTL-081. Taken out of the building's declared height. */
  parapet_height_m?: number;
}

/**
 * Surface colours and how far each sits above the terrain.
 *
 * The lifts are larger than they look like they need to be. The ground is now a real 8 m DEM rather
 * than a smooth interpolation, so it has centimetre-scale relief of its own; a carriageway floating
 * 2 cm above it disappeared under the terrain wherever the terrain happened to rise, and the whole
 * district read as one continuous pavement. The relative order — carriageway lowest, kerb top and
 * pavement above it — is what carries the meaning, so it is the order that is fixed and the absolute
 * heights that give it room.
 */
const SURFACE_STYLE: Record<string, { color: number; height: number }> = {
  roadway: { color: 0x3c3a38, height: 0.1 },
  cycleway: { color: 0x4a4340, height: 0.13 },
  park: { color: 0x53663c, height: 0.16 },
  plaza: { color: 0x7d7a72, height: 0.22 },
  sidewalk: { color: 0x8c8880, height: 0.25 },
  steps: { color: 0x827d76, height: 0.27 },
  boardwalk: { color: 0x8a6f4e, height: 0.28 },
};

/**
 * Colour by what the ground is made of, where OSM says so.
 *
 * The lift still comes from the polygon's kind — a cobbled carriageway is at carriageway height —
 * but the colour comes from the material, because the whole reason DUMBO looks like DUMBO is that
 * its streets are Belgian block and the pavements beside them are not.
 */
const MATERIAL_STYLE: Record<string, number> = {
  cobblestone: 0x6a5c4f,
  paving_stones: 0x8a857c,
  concrete: 0x928e86,
  asphalt: 0x3c3a38,
  wood: 0x8a6f4e,
  gravel: 0x8d8477,
};

/**
 * The colour for a named material, pulled towards the measured paving hue like everything else.
 *
 * The blend keeps the material's own lightness for the same reason the surfaces do: cobblestone is
 * darker and warmer than the concrete beside it, and flattening the two loses the only cue that
 * tells a walker which is the road.
 */
function materialColour(material: string, fallback: number): number {
  const base = MATERIAL_STYLE[material];
  if (base === undefined) return fallback;
  if (!observedPaving) return base;
  const original = new THREE.Color(base);
  const originalHsl = { h: 0, s: 0, l: 0 };
  original.getHSL(originalHsl);
  const tinted = new THREE.Color().setHSL(
    observedPaving.h,
    originalHsl.s + (observedPaving.s - originalHsl.s) * observedPaving.strength,
    originalHsl.l,
  );
  return original.lerp(tinted, observedPaving.strength).getHex();
}

/** Hue and saturation measured from photographs of DUMBO's paving, if the corpus has any. */
let observedPaving: { h: number; s: number; strength: number } | null = null;

/** A colour measured from photographs of the district, per surface material. */
export interface ObservedPalette {
  available?: boolean;
  surfaces?: Record<string, { mean_hex?: string; observations?: number; credits?: string[] }>;
}

/**
 * Re-tint the built-in surface colours towards what photographs of DUMBO actually show.
 *
 * Takes the measured *hue and saturation* but keeps each surface's own lightness. Blending straight
 * towards the measured colour looked right in isolation and was wrong in place: the road and the
 * pavement converged on the same mid grey, and a street whose carriageway and footway are the same
 * value is unreadable at eye level — the kerb line is the only thing left telling you where you can
 * walk. The relative contrast between roadway, kerb and plaza is a legibility decision; the colour
 * temperature is the measurement. This adopts the second without discarding the first.
 */
export function applyObservedPalette(palette: ObservedPalette | null, strength = 0.8): string[] {
  const paving = palette?.surfaces?.paving;
  if (!palette?.available || !paving?.mean_hex) return [];

  const measured = new THREE.Color(paving.mean_hex);
  const measuredHsl = { h: 0, s: 0, l: 0 };
  measured.getHSL(measuredHsl);
  // Recorded as well as applied, so the per-material colours can adopt the same measurement without
  // the two drifting apart.
  observedPaving = { h: measuredHsl.h, s: measuredHsl.s, strength: strength * 0.6 };

  for (const kind of ['roadway', 'sidewalk', 'plaza', 'cycleway', 'steps', 'park', 'boardwalk']) {
    const style = SURFACE_STYLE[kind];
    if (!style) continue;
    const weight = strength * relativeWeight(kind);
    if (weight <= 0) continue;
    const original = new THREE.Color(style.color);
    const originalHsl = { h: 0, s: 0, l: 0 };
    original.getHSL(originalHsl);
    const tinted = new THREE.Color().setHSL(
      measuredHsl.h,
      originalHsl.s + (measuredHsl.s - originalHsl.s) * weight,
      originalHsl.l,
    );
    style.color = original.lerp(tinted, weight).getHex();
  }
  return paving.credits ?? [];
}

/** Fewer than this many photographs and a measured foliage colour is one tree's summer, not DUMBO's. */
const MIN_FOLIAGE_OBSERVATIONS = 2;

/** Hue and saturation measured from photographs of DUMBO's planting, if the corpus has enough. */
let observedFoliage: { h: number; s: number; strength: number } | null = null;

/**
 * Adopt the green that photographs of DUMBO actually show.
 *
 * Records the measurement rather than rewriting the prototypes, so it does not matter whether the
 * palette or the prop set loads first, and calling it twice cannot drag the canopies further each
 * time. `tintFor` applies it when geometry is built.
 *
 * The blend keeps each genus's own lightness, exactly as the surfaces do, so a plane still reads
 * lighter than an oak and a mixed street does not flatten into one wall of green.
 *
 * Gated on a minimum count because foliage is the thinnest evidence in the corpus: a canopy fills a
 * small part of most frames, and one photograph taken in October would repaint the district's
 * summer. Returns credits only when the measurement will actually be used, so nothing is credited
 * for an influence it did not have.
 */
export function applyObservedFoliage(palette: ObservedPalette | null, strength = 0.7): string[] {
  const foliage = palette?.surfaces?.foliage;
  if (!palette?.available || !foliage?.mean_hex) return [];
  if ((foliage.observations ?? 0) < MIN_FOLIAGE_OBSERVATIONS) return [];

  const hsl = { h: 0, s: 0, l: 0 };
  new THREE.Color(foliage.mean_hex).getHSL(hsl);
  observedFoliage = { h: hsl.h, s: hsl.s, strength };
  return foliage.credits ?? [];
}

/**
 * How far each surface moves towards the measured colour.
 *
 * The corpus measures the road surface, which is what most of a street photograph's lower third is.
 * Kerbs and steps are usually lighter concrete and are not what was sampled, so they follow only
 * part of the way rather than being flattened to the same tone as the carriageway.
 */
function relativeWeight(kind: string): number {
  switch (kind) {
    case 'roadway':
    case 'cycleway':
      return 1.0;
    case 'plaza':
      return 0.6;
    // Grass and timber are not what the corpus measured, and dragging them towards a road colour
    // would undo the one distinction worth having underfoot.
    case 'park':
    case 'boardwalk':
      return 0;
    default:
      return 0.35;
  }
}

export function buildPaving(doc: PavingDocument, groundAt: (x: number, y: number) => number): THREE.Group {
  if (doc.polygons?.length) return buildSurveyedPaving(doc, groundAt);
  return buildQuadPaving(doc, groundAt);
}

/**
 * Draw the city's own surveyed surfaces, and give the kerbs a face.
 *
 * Two things make this read as a street rather than as a coloured plan. The polygons are the shapes
 * a surveyor traced, so the pavement runs where the pavement runs and a junction is one surface
 * instead of a heap of overlapping quads. And the kerb is extruded to a real height: without a
 * vertical face at the edge, a pavement is only a change of colour, and the eye has nothing to judge
 * distance or level against while walking.
 */
function buildSurveyedPaving(
  doc: PavingDocument,
  groundAt: (x: number, y: number) => number,
): THREE.Group {
  const group = new THREE.Group();
  const byKind = new Map<string, number[]>();

  for (const polygon of doc.polygons ?? []) {
    const style = SURFACE_STYLE[polygon.kind] ?? SURFACE_STYLE.roadway;    // Normalise winding before triangulating. The published layers do not agree with each other:
    // sidewalk rings come back one way round and roadbed the other, and since scene +Y maps to
    // render -Z the handedness flip turns that disagreement into whole layers facing downward and
    // vanishing. Signed area is the only thing that settles it.
    const ring = signedArea(polygon.ring) < 0 ? [...polygon.ring].reverse() : polygon.ring;
    const contour = ring.map(([x, y]) => new THREE.Vector2(x, y));
    if (contour.length < 3) continue;

    let faces: number[][];
    try {
      // three.js already ships a robust ear-clipper; a hand-rolled one would only be a worse copy.
      faces = THREE.ShapeUtils.triangulateShape(contour, []);
    } catch {
      continue;
    }

    // One mesh per kind *and* material: the lift is a property of the kind, the colour of the
    // material, and a cobbled carriageway needs both.
    const key = polygon.surface ? `${polygon.kind}|${polygon.surface}` : polygon.kind;
    let positions = byKind.get(key);
    if (!positions) {
      positions = [];
      byKind.set(key, positions);
    }    for (const face of faces) {
      // Scene +Y (north) maps to render -Z, flipping handedness, so the winding produced in plan
      // has to be reversed or every surface faces down and is silently culled.
      for (const index of [face[0], face[2], face[1]]) {
        const point = contour[index];
        const render = Frame.sceneToRender([point.x, point.y, groundAt(point.x, point.y) + style.height]);
        positions.push(render[0], render[1], render[2]);
      }
    }
  }

  for (const [key, positions] of byKind) {
    if (!positions.length) continue;
    const [kind, material] = key.split('|');
    const style = SURFACE_STYLE[kind] ?? SURFACE_STYLE.roadway;
    const color = material ? materialColour(material, style.color) : style.color;
    const geometry = new THREE.BufferGeometry();
    geometry.setAttribute('position', new THREE.Float32BufferAttribute(positions, 3));
    geometry.computeVertexNormals();
    const mesh = new THREE.Mesh(geometry, new THREE.MeshLambertMaterial({ color }));
    mesh.name = `paving:${key}`;
    group.add(mesh);
  }

  const kerbHeight = doc.kerb_height_m ?? 0.15;
  const roadLift = SURFACE_STYLE.roadway.height;
  const kerbPositions: number[] = [];
  for (const kerb of doc.kerbs ?? []) {
    for (let i = 0; i < kerb.line.length - 1; i++) {
      const [ax, ay] = kerb.line[i];
      const [bx, by] = kerb.line[i + 1];
      // Rise from the carriageway, not from bare terrain, so the face meets the road it belongs to.
      const az = groundAt(ax, ay) + roadLift;
      const bz = groundAt(bx, by) + roadLift;
      const a = Frame.sceneToRender([ax, ay, az]);
      const b = Frame.sceneToRender([bx, by, bz]);
      const at = Frame.sceneToRender([ax, ay, az + kerbHeight]);
      const bt = Frame.sceneToRender([bx, by, bz + kerbHeight]);
      // Two triangles per segment, doubled-sided by material so the face reads from either kerb.
      kerbPositions.push(...a, ...b, ...bt, ...a, ...bt, ...at);
    }
  }
  if (kerbPositions.length) {
    const geometry = new THREE.BufferGeometry();
    geometry.setAttribute('position', new THREE.Float32BufferAttribute(kerbPositions, 3));
    geometry.computeVertexNormals();
    const mesh = new THREE.Mesh(
      geometry,
      new THREE.MeshLambertMaterial({ color: 0xa8a49b, side: THREE.DoubleSide }),
    );
    mesh.name = 'paving:kerb';
    group.add(mesh);
  }

  return group;
}

function signedArea(ring: Array<[number, number]>): number {
  let total = 0;
  for (let i = 0; i < ring.length; i++) {
    const [ax, ay] = ring[i];
    const [bx, by] = ring[(i + 1) % ring.length];
    total += ax * by - bx * ay;
  }
  return total / 2;
}

/**
 * The four seasons a prop set can be rendered in.
 *
 * Imported rather than redefined: `FarField` already owns this vocabulary, because the water scene
 * picks its sailboats by season. One control should drive the whole scene, not two that agree by
 * coincidence.
 */
import type { Season } from './FarField';

export type { Season };

/** What a genus looks like through the year. First-class on the prototype since contracts v1. */
interface SeasonalFoliage {
  seasonal_foliage?: Partial<Record<Season, string>>;
  deciduous?: boolean;
}

let currentSeason: Season = 'summer';

/**
 * Choose the season the scene is dressed for.
 *
 * Returns whether anything changed, so a caller can skip a rebuild it does not need. The prop set
 * has to be rebuilt for a change to take effect: winter is not merely a different colour, because a
 * deciduous tree in January is a bare crown of twigs and drawing it as a solid mass of brown would
 * be a worse lie than leaving it green.
 */
export function setSeason(season: Season): boolean {
  if (season === currentSeason) return false;
  currentSeason = season;
  return true;
}

export function getSeason(): Season {
  return currentSeason;
}

function seasonalSpec(prototype: ScenePrototype): SeasonalFoliage {
  return prototype as SeasonalFoliage;
}

/** True when this prototype should be drawn without leaves in the current season. */
function isBare(prototype: ScenePrototype): boolean {
  return currentSeason === 'winter' && seasonalSpec(prototype).deciduous !== false;
}

function buildQuadPaving(doc: PavingDocument, groundAt: (x: number, y: number) => number): THREE.Group {
  const group = new THREE.Group();
  const byKind = new Map<string, number[]>();

  for (const surface of doc.surfaces ?? []) {
    const style = SURFACE_STYLE[surface.kind] ?? SURFACE_STYLE.roadway;
    let positions = byKind.get(surface.kind);
    if (!positions) {
      positions = [];
      byKind.set(surface.kind, positions);
    }
    for (const q of surface.quads) {
      const corners: Array<[number, number]> = [
        [q[0], q[1]],
        [q[2], q[3]],
        [q[4], q[5]],
        [q[6], q[7]],
      ];
      // Two triangles. Winding matches the terrain mesh, which is flipped by the ENU-to-render
      // handedness change; get it wrong and the paving is culled and invisible.
      const order = [0, 1, 2, 0, 2, 3];
      for (const i of order) {
        const [x, y] = corners[i];
        const r = Frame.sceneToRender([x, y, groundAt(x, y) + style.height]);
        positions.push(r[0], r[1], r[2]);
      }
    }
  }

  for (const [kind, positions] of byKind) {
    if (!positions.length) continue;
    const style = SURFACE_STYLE[kind] ?? SURFACE_STYLE.roadway;
    const geometry = new THREE.BufferGeometry();
    geometry.setAttribute('position', new THREE.Float32BufferAttribute(positions, 3));
    geometry.computeVertexNormals();
    const mesh = new THREE.Mesh(
      geometry,
      new THREE.MeshLambertMaterial({
        color: style.color,
        side: THREE.DoubleSide,
        polygonOffset: true,
        polygonOffsetFactor: -1,
        polygonOffsetUnits: -1,
      }),
    );
    mesh.userData = { paving: kind };
    group.add(mesh);
  }

  return group;
}

/**
 * Procedural geometry for a prototype whose payload is absent.
 *
 * This is what makes `format: "procedural"` useful rather than a placeholder: a district looks
 * inhabited from the first build, before anyone models a single asset, and swapping in a real GLB
 * later is a change to the prototype's `url` and nothing else.
 */
function proceduralPrototype(prototype: ScenePrototype): THREE.BufferGeometry[] {
  const [sx, sy, sz] = prototype.size_m ?? [6, 6, 8];

  switch (prototype.kind) {
    case 'tree': {
      // Trunk plus canopy. Low-poly on purpose: at 1,300 instances the budget is tight, and a
      // street tree read at walking distance is mostly silhouette.
      //
      // The instance's uniform scale carries its real trunk diameter, so this shape is authored for
      // a nominal 10 m tree and everything else follows from that one number. What it cannot carry
      // is *proportion*: a young whip is a thin stick with a small crown high up, a mature plane is
      // a short trunk under a wide one, and drawing both with the same profile is most of why a
      // street of them read as one prop repeated.
      const bare = isBare(prototype);
      // sz is the prototype's nominal height; the ratio of instance scale to it is unavailable here,
      // so maturity is expressed through the prototype's own spread instead.
      const slender = sx < 8;

      const trunk = new THREE.CylinderGeometry(
        sx * (slender ? 0.028 : 0.038),
        sx * (slender ? 0.045 : 0.062),
        sz * (bare ? 0.52 : 0.42),
        5,
      );
      trunk.translate(0, sz * (bare ? 0.26 : 0.21), 0);

      if (bare) {
        // Winter: a crown of bare limbs rather than a mass. Four angled branches read as filigree
        // against the sky at distance and cost four boxes, where a solid brown blob would read as a
        // dead tree.
        const parts: THREE.BufferGeometry[] = [trunk];
        for (let i = 0; i < 4; i++) {
          const limb = new THREE.CylinderGeometry(sx * 0.012, sx * 0.022, sz * 0.42, 4);
          limb.rotateZ((i % 2 === 0 ? 1 : -1) * 0.55);
          limb.rotateY((i * Math.PI) / 4);
          limb.translate(0, sz * 0.72, 0);
          parts.push(limb);
        }
        return parts;
      }

      const lower = new THREE.IcosahedronGeometry(sx * 0.34, 0);
      lower.scale(1, 0.78, 1);
      lower.translate(0, sz * 0.58, 0);

      const upper = new THREE.IcosahedronGeometry(sx * 0.25, 0);
      upper.scale(1, 0.85, 1);
      upper.translate(0, sz * 0.8, 0);

      return [trunk, lower, upper];
    }
    case 'bench': {
      // Seat, legs and a back. It was a single floating slab before, which from across a plaza read
      // as litter dropped on the pavement rather than as somewhere to sit -- and there are 354 of
      // them, so the mistake was 354 times over.
      const depth = sz * 0.42;
      const seat = new THREE.BoxGeometry(sx, 0.07, depth);
      seat.translate(0, 0.45, 0);

      const parts: THREE.BufferGeometry[] = [seat];
      for (const side of [-0.42, 0.42]) {
        const leg = new THREE.BoxGeometry(0.07, 0.45, depth * 0.85);
        leg.translate(sx * side, 0.225, 0);
        parts.push(leg);
      }

      const back = new THREE.BoxGeometry(sx, 0.42, 0.06);
      back.rotateX(-0.12);
      back.translate(0, 0.7, -depth * 0.42);
      parts.push(back);

      return parts;
    }
    case 'lamp': {
      const post = new THREE.CylinderGeometry(0.06, 0.08, sz, 6);
      post.translate(0, sz / 2, 0);
      return [post];
    }
    case 'bollard': {
      const post = new THREE.CylinderGeometry(0.09, 0.11, sz, 6);
      post.translate(0, sz / 2, 0);
      return [post];
    }
    case 'fence': {
      // A panel authored along local X, so an instance's yaw aligns it with the run it sits on.
      // Two rails and three balusters read as a railing at walking distance and cost 5 boxes;
      // a real model later is a change to the prototype's url.
      const depth = sy || 0.12;
      const parts: THREE.BufferGeometry[] = [];

      const topRail = new THREE.BoxGeometry(sx, 0.06, depth * 0.5);
      topRail.translate(0, sz - 0.03, 0);
      parts.push(topRail);

      const midRail = new THREE.BoxGeometry(sx, 0.04, depth * 0.35);
      midRail.translate(0, sz * 0.55, 0);
      parts.push(midRail);

      for (const offset of [-0.5, 0, 0.5]) {
        const baluster = new THREE.BoxGeometry(0.05, sz, 0.05);
        baluster.translate(sx * offset, sz / 2, 0);
        parts.push(baluster);
      }
      return parts;
    }
    case 'awning': {
      // A canopy sloping down and away from the wall, plus the valance that hangs off its lip.
      // Authored along local X like a fence panel, so an instance's yaw aligns it with the facade,
      // and offset in +Z so it projects outward rather than straddling the wall.
      const depth = sy || 1.5;
      const canopy = new THREE.BoxGeometry(sx, 0.08, depth);
      canopy.rotateX(-0.18);
      canopy.translate(0, sz, depth * 0.5);

      const valance = new THREE.BoxGeometry(sx, 0.35, 0.06);
      valance.translate(0, sz - 0.3, depth);

      return [canopy, valance];
    }
    case 'wall': {
      // Solid, unlike a fence. Rendering a wall with balusters would let a walker see through a
      // retaining wall, which is precisely the thing a wall is there to stop.
      const slab = new THREE.BoxGeometry(sx, sz, sy || 0.3);
      slab.translate(0, sz / 2, 0);
      const coping = new THREE.BoxGeometry(sx, 0.07, (sy || 0.3) * 1.15);
      coping.translate(0, sz, 0);
      return [slab, coping];
    }
    case 'bin': {
      const body = new THREE.CylinderGeometry(sx * 0.4, sx * 0.34, sz, 8);
      body.translate(0, sz / 2, 0);
      return [body];
    }
    case 'hydrant': {
      const body = new THREE.CylinderGeometry(sx * 0.3, sx * 0.36, sz * 0.8, 6);
      body.translate(0, sz * 0.4, 0);
      const cap = new THREE.SphereGeometry(sx * 0.32, 6, 4);
      cap.translate(0, sz * 0.82, 0);
      return [body, cap];
    }
    case 'traffic_light': {
      const post = new THREE.CylinderGeometry(0.05, 0.07, sz, 6);
      post.translate(0, sz / 2, 0);
      const head = new THREE.BoxGeometry(0.24, 0.6, 0.2);
      head.translate(0, sz - 0.35, 0);
      return [post, head];
    }
    case 'rooftop_structure': {
      // A unit box standing on its base, so the instance's per-axis scale is read directly as the
      // structure's surveyed length, width and height. Deliberately plain: what these are -- stair
      // bulkhead, lift overrun, water tank -- was not surveyed, so claiming a shape would be
      // inventing evidence. A parapet-less box at the right size in the right place is the whole
      // of what the model actually knows, and at the distance a roofline is read, it is enough.
      const box = new THREE.BoxGeometry(1, 1, 1);
      box.translate(0, 0.5, 0);
      return [box];
    }
    default: {
      const box = new THREE.BoxGeometry(sx, sz, sx);
      box.translate(0, sz / 2, 0);
      return [box];
    }
  }
}

/** The tint a prototype was authored with for the current season, ignoring any measurement. */
function authoredTint(prototype: ScenePrototype): number {
  const seasonal = seasonalSpec(prototype).seasonal_foliage?.[currentSeason];
  if (seasonal) {
    const match = /#?([0-9a-f]{6})/i.exec(seasonal);
    if (match) return parseInt(match[1], 16);
  }
  const match = /#([0-9a-f]{6})/i.exec(prototype.notes ?? '');
  return match ? parseInt(match[1], 16) : 0x4e6c3c;
}

/**
 * The tint to render a prototype with: the authored colour, pulled towards the measured foliage
 * hue when the corpus has one and the prototype is a plant.
 *
 * The measurement is only applied in summer. It was taken from photographs of DUMBO in leaf, so
 * using it to tint an October canopy would drag the whole district back to green and throw away the
 * one thing the seasonal table is for.
 */
function tintFor(prototype: ScenePrototype): number {
  const original = new THREE.Color(authoredTint(prototype));
  const isPlant = prototype.kind === 'tree' || prototype.kind === 'shrub';
  if (!observedFoliage || !isPlant || currentSeason !== 'summer') {
    return original.getHex();
  }
  const originalHsl = { h: 0, s: 0, l: 0 };
  original.getHSL(originalHsl);
  const tinted = new THREE.Color().setHSL(
    observedFoliage.h,
    originalHsl.s + (observedFoliage.s - originalHsl.s) * observedFoliage.strength,
    originalHsl.l,
  );
  return original.lerp(tinted, observedFoliage.strength).getHex();
}

const TRUNK_COLOR = 0x5a4634;

export interface PropBuildResult {
  group: THREE.Group;
  instanceCount: number;
  drawCalls: number;
}

/**
 * Build instanced meshes for a prop set.
 *
 * One InstancedMesh per prototype part, so 1,252 trees across ten species cost about thirty draw
 * calls rather than thousands. Per-instance scale carries the real trunk-diameter variation, which
 * is what stops a street of trees reading as copies of one asset.
 */
export function buildProps(
  set: ScenePropSet,
  groundAt: (x: number, y: number) => number,
  options: { maxInstances?: number } = {},
): PropBuildResult {
  const group = new THREE.Group();
  const prototypes = new Map(set.prototypes.map((p) => [p.prototype_id, p]));

  const byPrototype = new Map<string, typeof set.instances>();
  for (const instance of set.instances) {
    const list = byPrototype.get(instance.p);
    if (list) list.push(instance);
    else byPrototype.set(instance.p, [instance]);
  }

  let drawCalls = 0;
  let total = 0;
  const matrix = new THREE.Matrix4();
  const quaternion = new THREE.Quaternion();
  const scaleVec = new THREE.Vector3();
  const positionVec = new THREE.Vector3();

  for (const [prototypeId, instances] of byPrototype) {
    const prototype = prototypes.get(prototypeId);
    if (!prototype) continue;

    const capped = options.maxInstances
      ? instances.slice(0, options.maxInstances)
      : instances;
    if (!capped.length) continue;

    const parts = proceduralPrototype(prototype);
    const tint = tintFor(prototype);

    parts.forEach((geometry, partIndex) => {
      // In winter every part of a deciduous tree is wood: the trunk and the bare limbs above it.
      const woody = prototype.kind === 'tree' && (partIndex === 0 || isBare(prototype));
      const material = new THREE.MeshLambertMaterial({
        color: woody ? TRUNK_COLOR : tint,
        flatShading: true,
      });
      const mesh = new THREE.InstancedMesh(geometry, material, capped.length);
      mesh.frustumCulled = true;
      // What casts, and what only receives, is a budget decision taken per kind rather than per
      // prototype. A tree's shadow across a pavement is most of what says "there is a tree here";
      // a litter bin's is four pixels nobody will miss, and there are 7,044 instances in total.
      // Whether a prop casts is the module's decision, not the viewer's: the contract carries
      // `casts_shadow` per prototype, and a shared kernel has no business knowing that a district
      // has bollards or that a bridge has gantries. Absent the field the answer is no, which is
      // both the schema's default and the safe one — a missing shadow is cheaper than a wrong one.
      mesh.castShadow = prototype.casts_shadow === true;
      mesh.receiveShadow = true;
      // Which prototype drew this. Costs nothing and is the only way to tell one instanced mesh
      // from another at runtime, which matters when checking whether a class of prop is actually
      // reaching the screen.
      mesh.userData.prototype_id = prototype.prototype_id;
      mesh.userData.kind = prototype.kind;

      capped.forEach((instance, i) => {
        const [x, y] = instance.xy;
        const z = instance.z ?? groundAt(x, y);
        const scale = instance.s ?? 1;
        // Scene (Z-up) to render (Y-up); the prototype geometry is authored Y-up already.
        const r = Frame.sceneToRender([x, y, z]);
        positionVec.set(r[0], r[1], r[2]);
        // Slight per-instance colour variation would need a colour attribute; yaw plus scale is
        // enough to break the pattern at a fraction of the cost.
        quaternion.setFromAxisAngle(
          new THREE.Vector3(0, 1, 0),
          ((instance.r ?? 0) * Math.PI) / 180,
        );
        scaleVec.set(scale, scale, scale);
        // Per-axis scale wins where present. A uniform scale cannot describe a roof bulkhead, which
        // is measured as a plan extent and a height, not as a multiple of anything. Note the axis
        // swap: the contract's [x, y, z] is scene-frame (Z-up) and the geometry is render-frame.
        if (instance.s3) {
          const [ex, ey, ez] = instance.s3;
          scaleVec.set(ex, ez, ey);
        }
        matrix.compose(positionVec, quaternion, scaleVec);
        mesh.setMatrixAt(i, matrix);
      });

      mesh.instanceMatrix.needsUpdate = true;
      mesh.computeBoundingSphere();
      group.add(mesh);
      drawCalls += 1;
    });

    total += capped.length;
  }

  return { group, instanceCount: total, drawCalls };
}

/**
 * Window-band shading for a facade.
 *
 * Returns how much to darken a wall vertex at a given height, producing horizontal glazing bands
 * without a texture, a UV set, or an image request. Cheap, and it reads correctly at walking
 * distance, which is the only place it is seen.
 */
export function facadeBandFactor(
  heightFraction: number,
  style: FacadeStyle | undefined,
  buildingHeight: number,
): number {
  if (!style || style.glazing <= 0.02) return 1;

  // Ground floor reads differently everywhere: taller, usually retail or a loading bay.
  const groundFraction = Math.min(0.34, 4.2 / Math.max(buildingHeight, 4));
  if (heightFraction < groundFraction) {
    return style.family === 'retail' ? 0.62 : 0.86;
  }

  const floors =
    style.floors && style.floors > 1 ? style.floors : Math.max(2, Math.round(buildingHeight / 3.5));
  const band = ((heightFraction - groundFraction) / (1 - groundFraction)) * floors;
  const withinFloor = band - Math.floor(band);

  // Window occupies the middle of each storey. Depth is driven by the glazing ratio but floored,
  // because a warehouse with 10% glazing still has visibly punched openings and a facade with no
  // readable articulation looks like an untextured box rather than a building.
  const windowHalf = Math.min(0.42, 0.18 + style.glazing * 0.5);
  const inWindow = Math.abs(withinFloor - 0.45) < windowHalf;
  return inWindow ? 1 - Math.min(0.62, 0.28 + style.glazing * 0.8) : 1.06;
}

/**
 * How much of a window band survives at this point *across* a bay: 1 in the middle of the opening,
 * 0 on the pier between openings.
 *
 * This is the other half of the window. `facadeBandFactor` says which horizontal courses are glazed;
 * without this the result is a continuous ribbon at every storey, which reads as a striped box
 * rather than a building. Masonry piers between punched openings are what give a wall its vertical
 * rhythm, and the rhythm is most of what tells you a row house from a warehouse at a glance.
 *
 * Returned as a multiplier rather than a hard mask so the transition covers a whole bay smoothly;
 * the geometry is only a few quads wide per bay and a step edge would alias badly at distance.
 */
export function facadeBayFactor(
  acrossFraction: number,
  bays: number,
  style: FacadeStyle | undefined,
): number {
  if (!style || bays < 2) return 1;
  const within = acrossFraction * bays - Math.floor(acrossFraction * bays);
  // Wider openings for glassier buildings; a Federal row house keeps a lot of wall.
  const openHalf = Math.min(0.42, 0.16 + style.glazing * 0.55);
  const distance = Math.abs(within - 0.5);
  if (distance >= openHalf) return 0;
  // Ease the last 25% so the pier edge does not shimmer when the bay is only a few pixels wide.
  return Math.min(1, (openHalf - distance) / (openHalf * 0.25));
}

export function parseColor(hex: string, fallback: number): number {  const match = /#?([0-9a-f]{6})/i.exec(hex);
  return match ? parseInt(match[1], 16) : fallback;
}
