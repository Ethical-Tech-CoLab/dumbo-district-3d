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
  /** Flat quads: [ax, ay, bx, by, cx, cy, dx, dy] in scene meters. */
  quads: number[][];
}

export interface PavingDocument {
  surfaces: PavingSurface[];
  attribution?: string;
}

export interface FacadeStyle {
  family: string;
  color: string;
  glazing: number;
  era: string;
  floors?: number;
}

export interface FacadeDocument {
  styles: Record<string, FacadeStyle>;
}

/** Surface colours by kind. Deliberately desaturated so buildings stay the subject. */
const SURFACE_STYLE: Record<string, { color: number; height: number }> = {
  roadway: { color: 0x3c3a38, height: 0.02 },
  sidewalk: { color: 0x8c8880, height: 0.14 },
  plaza: { color: 0x7d7a72, height: 0.1 },
  cycleway: { color: 0x4a4340, height: 0.05 },
  steps: { color: 0x827d76, height: 0.16 },
};

export function buildPaving(doc: PavingDocument, groundAt: (x: number, y: number) => number): THREE.Group {
  const group = new THREE.Group();
  const byKind = new Map<string, number[]>();

  for (const surface of doc.surfaces) {
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
  const [sx, , sz] = prototype.size_m ?? [6, 6, 8];

  switch (prototype.kind) {
    case 'tree': {
      // Trunk plus a two-lobe canopy. Low-poly on purpose: at 1,252 instances the budget is tight,
      // and a street tree read at walking distance is mostly silhouette.
      const trunk = new THREE.CylinderGeometry(sx * 0.035, sx * 0.055, sz * 0.42, 5);
      trunk.translate(0, sz * 0.21, 0);

      const lower = new THREE.IcosahedronGeometry(sx * 0.34, 0);
      lower.scale(1, 0.78, 1);
      lower.translate(0, sz * 0.58, 0);

      const upper = new THREE.IcosahedronGeometry(sx * 0.25, 0);
      upper.scale(1, 0.85, 1);
      upper.translate(0, sz * 0.8, 0);

      return [trunk, lower, upper];
    }
    case 'bench': {
      const seat = new THREE.BoxGeometry(sx, 0.08, sz * 0.4);
      seat.translate(0, 0.45, 0);
      return [seat];
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
    default: {
      const box = new THREE.BoxGeometry(sx, sz, sx);
      box.translate(0, sz / 2, 0);
      return [box];
    }
  }
}

/** Parse the foliage tint a prototype records in its notes, falling back to a neutral green. */
function tintFor(prototype: ScenePrototype): number {
  const match = /#([0-9a-f]{6})/i.exec(prototype.notes ?? '');
  return match ? parseInt(match[1], 16) : 0x4e6c3c;
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
      const isTrunk = prototype.kind === 'tree' && partIndex === 0;
      const material = new THREE.MeshLambertMaterial({
        color: isTrunk ? TRUNK_COLOR : tint,
        flatShading: true,
      });
      const mesh = new THREE.InstancedMesh(geometry, material, capped.length);
      mesh.frustumCulled = true;

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

export function parseColor(hex: string, fallback: number): number {
  const match = /#?([0-9a-f]{6})/i.exec(hex);
  return match ? parseInt(match[1], 16) : fallback;
}
