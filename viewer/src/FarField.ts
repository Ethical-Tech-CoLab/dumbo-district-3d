/**
 * Far field: the Manhattan skyline, the water surface, and the vessels on it.
 *
 * As with everything else in this shell, the geometry-building is generic and the *content* comes
 * from the module. This file knows how to draw "a skyline block" and "a boat"; it does not know
 * that the skyline is Manhattan or that the ferry goes to Pier 11.
 */

import * as THREE from 'three';
import { Frame } from '@d3d/viewer-kernel';

export interface HorizonBlock {
  /** Scene-space centre [x, y]. */
  c: [number, number];
  /** Width, depth, base elevation, height, all meters. */
  w: number;
  d: number;
  b: number;
  h: number;
}

export interface HorizonDocument {
  blocks: HorizonBlock[];
  max_geometric_error_m?: number;
}

export interface VesselSpec {
  kind: string;
  length_m: number;
  beam_m: number;
  speed_mps: number;
  count: number;
  confidence: string;
  seasons: string[];
  area?: { center_xy: [number, number]; radius_m: number };
  notes?: string;
}

export interface WaterDocument {
  surface: { datum: string; elevation_m: number };
  routes: Array<{ name: string | null; path: [number, number][]; length_m: number }>;
  terminals: Array<{ name: string | null; xy: [number, number] }>;
  vessels: Record<string, VesselSpec>;
}

export type Season = 'winter' | 'spring' | 'summer' | 'autumn';

export function seasonForDate(date = new Date()): Season {
  const month = date.getMonth();
  if (month <= 1 || month === 11) return 'winter';
  if (month <= 4) return 'spring';
  if (month <= 8) return 'summer';
  return 'autumn';
}

/**
 * Build the distant skyline as one merged mesh.
 *
 * Every block is a box, shaded by distance so the far ones recede into haze. One geometry, one
 * draw call, a few thousand buildings. Explicitly excluded from picking and from the confidence
 * overlay: at this range the geometry is a silhouette, not a record.
 */
export function buildHorizon(doc: HorizonDocument): THREE.Group {
  const group = new THREE.Group();
  const positions: number[] = [];
  const normals: number[] = [];
  const colors: number[] = [];
  const color = new THREE.Color();

  // Faces of a unit box, as (corner offsets, normal). Only the four sides and the top are needed;
  // nobody sees the underside of a building across a river.
  const faces: Array<{ n: [number, number, number]; v: Array<[number, number, number]> }> = [
    { n: [0, 0, 1], v: [[-1, -1, 1], [1, -1, 1], [1, 1, 1], [-1, -1, 1], [1, 1, 1], [-1, 1, 1]] },
    { n: [0, 0, -1], v: [[1, -1, -1], [-1, -1, -1], [-1, 1, -1], [1, -1, -1], [-1, 1, -1], [1, 1, -1]] },
    { n: [1, 0, 0], v: [[1, -1, 1], [1, -1, -1], [1, 1, -1], [1, -1, 1], [1, 1, -1], [1, 1, 1]] },
    { n: [-1, 0, 0], v: [[-1, -1, -1], [-1, -1, 1], [-1, 1, 1], [-1, -1, -1], [-1, 1, 1], [-1, 1, -1]] },
    { n: [0, 1, 0], v: [[-1, 1, 1], [1, 1, 1], [1, 1, -1], [-1, 1, 1], [1, 1, -1], [-1, 1, -1]] },
  ];

  let maxDistance = 1;
  for (const block of doc.blocks) {
    maxDistance = Math.max(maxDistance, Math.hypot(block.c[0], block.c[1]));
  }

  for (const block of doc.blocks) {
    const distance = Math.hypot(block.c[0], block.c[1]);
    // Aerial perspective: distant mass desaturates toward the sky rather than staying solid.
    const haze = Math.min(0.82, (distance / maxDistance) * 0.9);
    const base = new THREE.Color(0x5d6470).lerp(new THREE.Color(0x9fb6cc), haze);

    const halfW = block.w / 2;
    const halfD = block.d / 2;
    const halfH = block.h / 2;
    const centreZ = block.b + halfH;
    const render = Frame.sceneToRender([block.c[0], block.c[1], centreZ]);

    for (const face of faces) {
      // Slight per-face shading so the mass is not a flat cut-out.
      const shade = face.n[1] === 1 ? 1.12 : 0.86 + 0.14 * Math.abs(face.n[0]);
      color.copy(base).multiplyScalar(shade);
      for (const [vx, vy, vz] of face.v) {
        // Box is authored in render space: x across, y up, z depth.
        positions.push(render[0] + vx * halfW, render[1] + vy * halfH, render[2] + vz * halfD);
        normals.push(face.n[0], face.n[1], face.n[2]);
        colors.push(color.r, color.g, color.b);
      }
    }
  }

  if (positions.length) {
    const geometry = new THREE.BufferGeometry();
    geometry.setAttribute('position', new THREE.Float32BufferAttribute(positions, 3));
    geometry.setAttribute('normal', new THREE.Float32BufferAttribute(normals, 3));
    geometry.setAttribute('color', new THREE.Float32BufferAttribute(colors, 3));
    geometry.computeBoundingSphere();
    const mesh = new THREE.Mesh(
      geometry,
      // Unlit: the skyline should not swing with the local sun, and at this range shading reads as
      // noise rather than form.
      new THREE.MeshBasicMaterial({ vertexColors: true }),
    );
    mesh.userData = { horizon: true, selectable: false };
    mesh.renderOrder = -2;
    group.add(mesh);
  }

  return group;
}

interface Vessel {
  mesh: THREE.Object3D;
  spec: VesselSpec;
  /** Route path in scene meters, or null for free-roaming recreational craft. */
  path: [number, number][] | null;
  distance: number;
  pathLength: number;
  /** For free-roaming craft: centre, radius, angle and orbit rate. */
  orbit?: { cx: number; cy: number; r: number; theta: number; rate: number };
}

const VESSEL_COLOR: Record<string, number> = {
  ferry: 0xd8d4cc,
  sailboat: 0xf2f0ea,
  jetski: 0xd85c3b,
};

function vesselMesh(spec: VesselSpec): THREE.Object3D {
  const group = new THREE.Group();
  const color = VESSEL_COLOR[spec.kind] ?? 0xcccccc;

  const hull = new THREE.Mesh(
    new THREE.BoxGeometry(spec.beam_m, Math.max(1.2, spec.length_m * 0.12), spec.length_m),
    new THREE.MeshLambertMaterial({ color }),
  );
  hull.position.y = Math.max(0.6, spec.length_m * 0.06);
  group.add(hull);

  if (spec.kind === 'ferry') {
    const house = new THREE.Mesh(
      new THREE.BoxGeometry(spec.beam_m * 0.78, 3.2, spec.length_m * 0.45),
      new THREE.MeshLambertMaterial({ color: 0x2f5f8f }),
    );
    house.position.set(0, hull.position.y + 2.2, -spec.length_m * 0.05);
    group.add(house);
  } else if (spec.kind === 'sailboat') {
    const mast = new THREE.Mesh(
      new THREE.CylinderGeometry(0.08, 0.1, spec.length_m * 1.25, 5),
      new THREE.MeshLambertMaterial({ color: 0xe8e4dc }),
    );
    mast.position.y = hull.position.y + spec.length_m * 0.62;
    group.add(mast);

    const sail = new THREE.Mesh(
      new THREE.PlaneGeometry(spec.length_m * 0.5, spec.length_m * 0.95),
      new THREE.MeshLambertMaterial({ color: 0xfbfaf6, side: THREE.DoubleSide }),
    );
    sail.position.set(0, hull.position.y + spec.length_m * 0.55, spec.length_m * 0.12);
    sail.rotation.y = Math.PI / 2;
    group.add(sail);
  }

  return group;
}

/**
 * Vessels on the water.
 *
 * Ferries follow real OSM route lines; recreational craft orbit inside a declared activity area.
 * The distinction matters and is preserved in the data: routes are grade B, invented craft are
 * grade D and only appear in their declared seasons.
 */
export class WaterScene {
  readonly group = new THREE.Group();
  private vessels: Vessel[] = [];
  private surfaceZ: number;
  private waterMesh: THREE.Mesh | null = null;

  constructor(doc: WaterDocument, season: Season) {
    this.surfaceZ = doc.surface.elevation_m;
    this.buildSurface();
    this.buildVessels(doc, season);
  }

  private buildSurface(): void {
    const geometry = new THREE.PlaneGeometry(6000, 6000, 1, 1);
    const mesh = new THREE.Mesh(
      geometry,
      new THREE.MeshLambertMaterial({
        color: 0x38566d,
        transparent: true,
        opacity: 0.94,
      }),
    );
    mesh.rotation.x = -Math.PI / 2;
    mesh.position.y = this.surfaceZ;
    mesh.renderOrder = -1;
    this.waterMesh = mesh;
    this.group.add(mesh);
  }

  private buildVessels(doc: WaterDocument, season: Season): void {
    const routes = doc.routes.filter((r) => r.path.length >= 2);

    for (const spec of Object.values(doc.vessels)) {
      if (!spec.seasons.includes(season)) continue;

      for (let i = 0; i < spec.count; i++) {
        const mesh = vesselMesh(spec);
        this.group.add(mesh);

        if (spec.area) {
          // Free-roaming craft: a slow orbit inside the declared area, offset so they do not move
          // as a formation.
          const angle = (i / spec.count) * Math.PI * 2;
          const radius = spec.area.radius_m * (0.35 + 0.55 * ((i * 7) % 5) / 5);
          this.vessels.push({
            mesh,
            spec,
            path: null,
            distance: 0,
            pathLength: 0,
            orbit: {
              cx: spec.area.center_xy[0],
              cy: spec.area.center_xy[1],
              r: radius,
              theta: angle,
              rate: (spec.speed_mps / Math.max(radius, 1)) * (i % 2 === 0 ? 1 : -1),
            },
          });
        } else if (routes.length) {
          const route = routes[i % routes.length];
          const pathLength = route.length_m;
          this.vessels.push({
            mesh,
            spec,
            path: route.path,
            // Spread vessels along their route rather than starting them all at the terminal.
            distance: (pathLength / spec.count) * i,
            pathLength,
          });
        } else {
          this.group.remove(mesh);
        }
      }
    }
  }

  get vesselCount(): number {
    return this.vessels.length;
  }

  /** Sample a polyline at an arc-length distance, wrapping and reversing at the ends. */
  private static sample(
    path: [number, number][],
    distance: number,
  ): { x: number; y: number; heading: number } {
    let remaining = distance;
    for (let i = 0; i < path.length - 1; i++) {
      const [ax, ay] = path[i];
      const [bx, by] = path[i + 1];
      const segment = Math.hypot(bx - ax, by - ay);
      if (remaining <= segment || i === path.length - 2) {
        const t = segment > 0 ? Math.min(1, remaining / segment) : 0;
        return {
          x: ax + (bx - ax) * t,
          y: ay + (by - ay) * t,
          heading: Math.atan2(bx - ax, by - ay),
        };
      }
      remaining -= segment;
    }
    const [x, y] = path[path.length - 1];
    return { x, y, heading: 0 };
  }

  update(dtSeconds: number): void {
    for (const vessel of this.vessels) {
      if (vessel.orbit) {
        vessel.orbit.theta += vessel.orbit.rate * dtSeconds;
        const x = vessel.orbit.cx + Math.cos(vessel.orbit.theta) * vessel.orbit.r;
        const y = vessel.orbit.cy + Math.sin(vessel.orbit.theta) * vessel.orbit.r;
        const render = Frame.sceneToRender([x, y, this.surfaceZ]);
        vessel.mesh.position.set(render[0], render[1], render[2]);
        // Tangent to the orbit, converted from scene heading to render yaw.
        vessel.mesh.rotation.y = -vessel.orbit.theta;
        continue;
      }

      if (!vessel.path) continue;
      vessel.distance += vessel.spec.speed_mps * dtSeconds;
      // Ping-pong along the route: a ferry that vanished at the end of its line and reappeared at
      // the start would read as a glitch.
      const cycle = vessel.pathLength * 2;
      const phase = vessel.distance % cycle;
      const forward = phase <= vessel.pathLength;
      const along = forward ? phase : cycle - phase;

      const sample = WaterScene.sample(vessel.path, along);
      const render = Frame.sceneToRender([sample.x, sample.y, this.surfaceZ]);
      vessel.mesh.position.set(render[0], render[1], render[2]);
      vessel.mesh.rotation.y = sample.heading + (forward ? 0 : Math.PI);
    }
  }

  dispose(): void {
    this.group.traverse((node) => {
      if (node instanceof THREE.Mesh) {
        node.geometry.dispose();
        (Array.isArray(node.material) ? node.material : [node.material]).forEach((m) => m.dispose());
      }
    });
    this.waterMesh = null;
    this.vessels = [];
  }
}
