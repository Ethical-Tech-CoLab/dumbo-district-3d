/**
 * District scene: three.js rendering driven entirely by the shared kernel.
 *
 * The division of labour matters. Everything about *what* should be on screen — which tiles, at
 * which level, where the camera is during a tour — is decided by @d3d/viewer-kernel. This file only
 * turns those decisions into three.js objects. That is what keeps the district shell and the bridge
 * shell from re-implementing each other.
 */

import * as THREE from 'three';
import type { AssetMetadata, Placement, Tile, ViewerMode } from '@d3d/contracts';
import {
  EventBus,
  Frame,
  LodSelector,
  TileStreamer,
  applyPlacement,
  resolvePlacement,
  type KernelEvents,
  type LoadedModule,
  type ModuleRegistry,
} from '@d3d/viewer-kernel';

import type { GroundGrid } from './GroundGrid';
import {
  buildPaving,
  buildProps,
  facadeBandFactor,
  facadeBayFactor,
  parseColor,
  setSeason,
  type FacadeDocument,
  type FacadeStyle,
  type PavingDocument,
} from './SceneDressing';
import type { ScenePropSet } from '@d3d/contracts';
import {
  LIGHTING_PRESETS,
  skyLighting,
  sunPosition,
  type LightingPreset,
  type SkyLighting,
} from './Sky';
import {
  WaterScene,
  buildHorizon,
  seasonForDate,
  type HorizonDocument,
  type Season,
  type WaterDocument,
} from './FarField';

export interface TileBuilding {
  id: string;
  ring: [number, number][];
  base: number;
  h: number;
  c: 'A' | 'B' | 'C' | 'D';
  name?: string;
  attrs?: Record<string, string | number | boolean | null>;
  basis?: string[];
  ctl?: string[];
  oq?: string[];
}

interface TilePayload {
  tile_id: string;
  level: number;
  representation: string;
  origin_m: [number, number, number];
  carries_metadata: boolean;
  buildings: TileBuilding[];
}

const CONFIDENCE_COLOR: Record<string, number> = {
  A: 0x2e9e4f,
  B: 0x3b7dd8,
  C: 0xd89a3b,
  D: 0xc4453c,
};

/** Facade tints, chosen to read as brick-and-warehouse DUMBO rather than as data. */
const PALETTE = [0x8d6e5a, 0x9a7b64, 0x7d6455, 0xa3856d, 0x6f5a4d, 0x8a7263];

/**
 * Half-width of the shadow box, in metres.
 *
 * 220 m across a 2048 map is about 0.1 m per texel, which resolves a window reveal and a kerb. It is
 * also comfortably further than a walker can see down a DUMBO street, so nothing that matters falls
 * outside it. Widening this trades shadow sharpness for range and there is nothing to spend it on.
 */
const SHADOW_HALF_EXTENT_M = 110;

/**
 * How far the viewer moves before the shadow box is re-centred.
 *
 * Snapping rather than following continuously. A shadow map that slides by a fraction of a texel
 * every frame makes every straight edge crawl, and this district is nothing but straight edges. A
 * whole-stride step means the texel grid stays put between jumps.
 */
const SHADOW_STEP_M = 16;

/**
 * How close a walker may bring their eye to a wall, in metres.
 *
 * A person is not a point, so stopping the eye exactly on the facade plane puts it where a face
 * would already be inside the brick. Kept small because this margin also *widens* every wall, and
 * the outlines are simplified enough to overhang the pavement in places already.
 */
const BODY_RADIUS_M = 0.25;

/**
 * Cell size for the footprint collision index, in metres.
 *
 * A DUMBO block face runs 60-80 m, so at 32 m most buildings touch two or three cells and the
 * per-step test looks at a handful of rings rather than several hundred.
 */
const COLLISION_CELL_M = 32;

function hashColor(id: string): number {
  let hash = 0;
  for (let i = 0; i < id.length; i++) hash = (hash * 31 + id.charCodeAt(i)) >>> 0;
  return PALETTE[hash % PALETTE.length];
}

/** Even-odd point-in-polygon. The rings are simple and closed, so this is exact. */
function pointInRing(x: number, y: number, ring: [number, number][]): boolean {
  let inside = false;
  for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) {
    const [xi, yi] = ring[i];
    const [xj, yj] = ring[j];
    if (yi > y !== yj > y && x < ((xj - xi) * (y - yi)) / (yj - yi) + xi) inside = !inside;
  }
  return inside;
}

/** Distance from a point to the nearest edge of a ring, so a body can be held off the wall. */
function distanceToRing(x: number, y: number, ring: [number, number][]): number {
  let best = Infinity;
  for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) {
    const [xi, yi] = ring[i];
    const [xj, yj] = ring[j];
    const dx = xj - xi;
    const dy = yj - yi;
    const lengthSq = dx * dx + dy * dy;
    let t = lengthSq > 0 ? ((x - xi) * dx + (y - yi) * dy) / lengthSq : 0;
    t = t < 0 ? 0 : t > 1 ? 1 : t;
    const d = Math.hypot(x - (xi + t * dx), y - (yi + t * dy));
    if (d < best) best = d;
  }
  return best;
}

interface ResidentTile {
  group: THREE.Group;
  level: number;
  buildings: Map<string, TileBuilding>;
  /**
   * Scene offset of this tile's local coordinates.
   *
   * Building rings are stored tile-local, exactly as they arrive. Anything that reads a ring in
   * scene space has to add this first -- collision was written without it and put an invisible
   * wall across the viewer's own start position, several hundred metres from the building it
   * thought it had found.
   */
  origin: [number, number];
}

export interface SceneOptions {
  frame: Frame;
  selector: LodSelector;
  streamer: TileStreamer;
  registry: ModuleRegistry;
  district: LoadedModule;
  bus: EventBus<KernelEvents>;
}

export class DistrictScene {
  readonly scene = new THREE.Scene();
  readonly camera: THREE.PerspectiveCamera;
  readonly renderer: THREE.WebGLRenderer;

  private readonly options: SceneOptions;
  private readonly tileRoot = new THREE.Group();
  private readonly resident = new Map<string, ResidentTile>();
  private readonly inFlight = new Set<string>();
  private readonly metadataById = new Map<string, TileBuilding>();
  private readonly sun: THREE.DirectionalLight;
  private readonly sunOffset = new THREE.Vector3(-180, 260, 140);
  /** Scratch for aiming the shadow box; a fresh vector every frame would churn the heap. */
  private readonly viewDirection = new THREE.Vector3();
  private shadowDirty = true;
  /** Resident footprints bucketed by grid cell, for walking into things. */
  private readonly collisionGrid = new Map<string, [number, number][][]>();
  private collisionDirty = true;
  private readonly fill: THREE.DirectionalLight;
  private readonly hemi: THREE.HemisphereLight;
  private readonly bounce: THREE.AmbientLight;
  private readonly skyUniforms: { top: { value: THREE.Color }; horizon: { value: THREE.Color } };
  private lighting: SkyLighting | null = null;
  private readonly raycaster = new THREE.Raycaster();
  private highlight: THREE.Mesh | null = null;
  private confidenceOverlay = false;
  private bridgePlaceholder: THREE.Group | null = null;
  private bridgeProxyPending = false;
  private ground: GroundGrid | null = null;
  private groundMesh: THREE.Mesh | null = null;
  private pavingGroup: THREE.Group | null = null;
  private propsGroup: THREE.Group | null = null;
  private propSet: ScenePropSet | null = null;
  private facades: FacadeDocument | null = null;
  private propStats = { instances: 0, drawCalls: 0 };
  private horizonGroup: THREE.Group | null = null;
  private water: WaterScene | null = null;
  private waterDoc: WaterDocument | null = null;

  constructor(canvas: HTMLCanvasElement, options: SceneOptions) {
    this.options = options;

    this.renderer = new THREE.WebGLRenderer({ canvas, antialias: true, preserveDrawingBuffer: true });
    this.renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    this.renderer.shadowMap.enabled = true;
    // Soft edges. A hard shadow on a low-poly massing model reads as a rendering artefact; PCF makes
    // the same geometry read as a building in sunlight.
    this.renderer.shadowMap.type = THREE.PCFSoftShadowMap;
    // The sun does not move between preset changes, so the shadow map does not need redrawing every
    // frame. It is invalidated explicitly when the light moves, when a tile arrives, or when the prop
    // set is rebuilt. That turns shadows from a per-frame cost into a per-change one, which is what
    // makes them affordable at 7,044 prop instances.
    this.renderer.shadowMap.autoUpdate = false;
    // Tone mapping, because without it the ground was lying about its own colour.
    //
    // The light rig is tuned for building faces, which are vertical and catch the sun at a glancing
    // angle. A pavement is horizontal and catches it square, so with hemisphere and sun summing to
    // nearly 4x it was multiplied straight past white and clipped: a carriageway authored at #3b3b39
    // rendered as #a2a19e, indistinguishable from the pavement beside it. Every colour measured from
    // the photo corpus was being flattened the same way, which is why the district read as uniform
    // grey no matter what the palette said.
    //
    // ACES compresses the highlight instead of clipping it, so relative values survive; the exposure
    // brings the overall level back to where the buildings were already right.
    this.renderer.toneMapping = THREE.ACESFilmicToneMapping;
    this.renderer.toneMappingExposure = 1.15;

    this.camera = new THREE.PerspectiveCamera(62, 1, 0.15, 6000);

    // A gradient sky dome rather than a flat background colour. Half the view from DUMBO is across
    // open water to the horizon, and a flat fill makes that read as a painted backdrop; the pale
    // band where the sky meets the river is what sells the distance.
    //
    // Rendered on the inside of a large sphere with depth writing off, so it is always behind
    // everything and costs one draw call.
    this.skyUniforms = {
      top: { value: new THREE.Color(0x74a8e8) },
      horizon: { value: new THREE.Color(0xd6e8f8) },
    };
    const skyDome = new THREE.Mesh(
      new THREE.SphereGeometry(4200, 24, 12),
      new THREE.ShaderMaterial({
        uniforms: this.skyUniforms,
        side: THREE.BackSide,
        depthWrite: false,
        fog: false,
        vertexShader: `
          varying vec3 vWorld;
          void main() {
            vWorld = (modelMatrix * vec4(position, 1.0)).xyz;
            gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
          }
        `,
        fragmentShader: `
          uniform vec3 top;
          uniform vec3 horizon;
          varying vec3 vWorld;
          void main() {
            // Height above the horizon, eased so the pale band hugs the horizon rather than
            // washing out the whole lower half of the sky.
            float h = clamp(normalize(vWorld).y, 0.0, 1.0);
            gl_FragColor = vec4(mix(horizon, top, pow(h, 0.42)), 1.0);
          }
        `,
      }),
    );
    skyDome.renderOrder = -1000;
    skyDome.frustumCulled = false;
    this.scene.add(skyDome);

    this.scene.fog = new THREE.Fog(0xd6e8f8, 500, 3200);

    this.hemi = new THREE.HemisphereLight(0xcfe0f2, 0x6b6154, 1.5);
    this.scene.add(this.hemi);

    // Bounce between facing buildings. See Sky.ts for why a fourth light is needed at all: the other
    // three are directional in effect, so a wall turned away from both sun and fill had nothing left
    // once cast shadows stopped a stray sun term from rescuing it.
    this.bounce = new THREE.AmbientLight(0xe4e2de, 0);
    this.scene.add(this.bounce);

    this.sun = new THREE.DirectionalLight(0xfff2df, 2.1);
    this.sun.position.set(-180, 260, 140);
    this.sun.castShadow = true;
    // The shadow camera follows the viewer rather than covering the district. The district is about
    // 1.8 km across after the boundary extension, and a 2048 map stretched over that gives 0.9 m per
    // texel, which is coarser than the kerbs it would be shadowing. Fitted to a 220 m box around the
    // camera instead it gives about 0.1 m per texel, which resolves a window reveal.
    this.sun.shadow.mapSize.set(2048, 2048);
    this.sun.shadow.camera.near = 1;
    this.sun.shadow.camera.far = 1400;
    // normalBias rather than bias, because this district is full of thin geometry -- awning canopies
    // are 80 mm thick and fence rails 40 mm -- and a constant depth bias either leaves acne on those
    // or makes everything else peter-pan. Offsetting along the normal scales with the surface angle,
    // which is what thin extrusions need.
    this.sun.shadow.normalBias = 0.06;
    this.sun.shadow.bias = -0.0004;
    this.scene.add(this.sun);
    this.scene.add(this.sun.target);

    // The fill: a weak second sun, opposite the first and low, so it lands on the wall faces the sun
    // has left dark without adding much to the ground. See Sky.ts for why a hemisphere light alone
    // cannot do this job.
    this.fill = new THREE.DirectionalLight(0xd6e2f4, 0.9);
    this.fill.position.set(180, 80, -140);
    this.scene.add(this.fill);

    this.scene.add(this.tileRoot);
  }

  // ------------------------------------------------------------- far field

  /** Distant skyline across the river. Real footprints, reduced to silhouettes. */
  setHorizon(doc: HorizonDocument): void {
    if (this.horizonGroup) this.scene.remove(this.horizonGroup);
    this.horizonGroup = buildHorizon(doc);
    this.scene.add(this.horizonGroup);
  }

  /** Water surface plus ferries and seasonal recreational craft. */
  setWater(doc: WaterDocument, season: Season = seasonForDate()): void {
    this.waterDoc = doc;
    if (this.water) {
      this.scene.remove(this.water.group);
      this.water.dispose();
    }
    this.water = new WaterScene(doc, season);
    this.scene.add(this.water.group);
  }

  get vesselCount(): number {
    return this.water?.vesselCount ?? 0;
  }

  /** Advance animated far-field content. Called from the shell's frame loop. */
  updateFarField(dtSeconds: number): void {
    this.water?.update(dtSeconds);
  }

  // ------------------------------------------------------------------ ground

  /** Ground height in scene meters, or 0 when no grid has been supplied. */
  groundHeightAt(x: number, y: number): number {
    return this.ground?.heightAt(x, y) ?? 0;
  }

  /**
   * Whether a walker standing here would be inside a building.
   *
   * Walking through walls is the single loudest way a twin stops being a twin: it says the
   * buildings are pictures rather than things. The footprints are already authoritative geometry
   * (grade A, NYC Open Data), so the test is exact rather than an approximation with boxes.
   *
   * A body radius is included so you stop with your face at the wall rather than with the camera
   * halfway through it -- a zero-radius test lets the eye, which is a point, pass the plane while
   * the person it belongs to would not have.
   */
  isInsideBuilding(x: number, y: number, radius = BODY_RADIUS_M): boolean {
    if (this.collisionDirty) this.rebuildCollisionIndex();
    const cell = COLLISION_CELL_M;
    // A body can straddle a cell boundary, so the neighbours have to be consulted too.
    const gx = Math.floor(x / cell);
    const gy = Math.floor(y / cell);
    for (let ix = gx - 1; ix <= gx + 1; ix++) {
      for (let iy = gy - 1; iy <= gy + 1; iy++) {
        const bucket = this.collisionGrid.get(`${ix}:${iy}`);
        if (!bucket) continue;
        for (const ring of bucket) {
          if (pointInRing(x, y, ring)) return true;
          if (radius > 0 && distanceToRing(x, y, ring) < radius) return true;
        }
      }
    }
    return false;
  }

  /**
   * Index the resident footprints by grid cell.
   *
   * Rebuilt wholesale when tiles change rather than maintained incrementally: the district holds a
   * few hundred footprints, so a rebuild is microseconds, and an incremental index that drifts out
   * of step with streaming would produce invisible walls in empty air -- the worst possible bug in
   * a walk mode, because nothing on screen explains it.
   */
  private rebuildCollisionIndex(): void {
    this.collisionGrid.clear();
    const cell = COLLISION_CELL_M;
    const seen = new Set<string>();
    // Finest first, so a building that is resident at both levels is indexed from its best ring.
    // Simplification pushes an outline outward, and the outward error lands on the pavement people
    // walk along: measured against the walk network, LOD 2 outlines swallow 20.8% of the nodes
    // beside them where the true footprints swallow 4%.
    const tiles = [...this.resident.values()].sort((a, b) => a.level - b.level);
    for (const tile of tiles) {
      // Detailed footprints only. A coarse tile simplifies a whole block to its bounding box --
      // one resident LOD 2 "building" here is 153 x 90 m -- and those boxes swallow the streets
      // between the real buildings. Where only coarse tiles are resident there is simply no
      // collision, which is right: that is far away, and a missing wall out there is invisible
      // while a false one is not.
      if (tile.level > 1) continue;
      const [ox, oy] = tile.origin;
      for (const [id, building] of tile.buildings) {
        // The same building is present at several LODs; index it once.
        if (seen.has(id)) continue;
        seen.add(id);
        const local = building.ring;
        if (!local || local.length < 3) continue;
        // Tile-local to scene, once here rather than on every test.
        const ring = local.map(([x, y]) => [ox + x, oy + y] as [number, number]);
        let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
        for (const [px, py] of ring) {
          if (px < minX) minX = px;
          if (px > maxX) maxX = px;
          if (py < minY) minY = py;
          if (py > maxY) maxY = py;
        }
        for (let ix = Math.floor(minX / cell); ix <= Math.floor(maxX / cell); ix++) {
          for (let iy = Math.floor(minY / cell); iy <= Math.floor(maxY / cell); iy++) {
            const key = `${ix}:${iy}`;
            let bucket = this.collisionGrid.get(key);
            if (!bucket) {
              bucket = [];
              this.collisionGrid.set(key, bucket);
            }
            bucket.push(ring);
          }
        }
      }
    }
    this.collisionDirty = false;
  }

  /** Footprints moved, so the collision index is stale. */
  private invalidateCollision(): void {
    this.collisionDirty = true;
  }

  /**
   * Build the terrain mesh from the interpolated ground grid.
   *
   * Replaces the flat plane a district-scale scene would otherwise use. DUMBO rises roughly 23 m
   * from the waterfront to its southern edge, so this is the difference between a walk that reads
   * correctly and one where the camera is buried.
   */
  setGround(grid: GroundGrid): void {
    this.ground = grid;

    if (this.groundMesh) {
      this.scene.remove(this.groundMesh);
      this.groundMesh.geometry.dispose();
      (this.groundMesh.material as THREE.Material).dispose();
    }

    const { cols, rows, cell_m, origin_xy_m } = grid.doc;
    const positions: number[] = [];
    const indices: number[] = [];
    const colors: number[] = [];
    const colour = new THREE.Color();

    for (let row = 0; row < rows; row++) {
      for (let col = 0; col < cols; col++) {
        const x = origin_xy_m[0] + col * cell_m;
        const y = origin_xy_m[1] + row * cell_m;
        const z = grid.doc.heights[row][col];
        const render = Frame.sceneToRender([x, y, z]);
        positions.push(render[0], render[1], render[2]);
        // Slight tint with height so the district's rise is legible without textures.
        const t = (z - grid.doc.min_m) / Math.max(1, grid.doc.max_m - grid.doc.min_m);
        colour.setRGB(0.36 + t * 0.1, 0.35 + t * 0.09, 0.33 + t * 0.07);
        colors.push(colour.r, colour.g, colour.b);
      }
    }

    for (let row = 0; row < rows - 1; row++) {
      for (let col = 0; col < cols - 1; col++) {
        // Skip quads whose corners are all water, so the East River is water rather than
        // extrapolated land. A quad touching the shore is kept, which gives a bank at the edge.
        if (
          !grid.isLand(col, row) &&
          !grid.isLand(col + 1, row) &&
          !grid.isLand(col, row + 1) &&
          !grid.isLand(col + 1, row + 1)
        ) {
          continue;
        }
        const a = row * cols + col;
        const b = a + 1;
        const c = a + cols;
        const d = c + 1;
        // Scene +Y (north) maps to render -Z, which flips the handedness of the grid. Winding is
        // therefore a-b-c, not a-c-b, or every triangle faces downward and is culled.
        indices.push(a, b, c, b, d, c);
      }
    }

    const geometry = new THREE.BufferGeometry();
    geometry.setAttribute('position', new THREE.Float32BufferAttribute(positions, 3));
    geometry.setAttribute('color', new THREE.Float32BufferAttribute(colors, 3));
    geometry.setIndex(indices);
    geometry.computeVertexNormals();

    const mesh = new THREE.Mesh(geometry, new THREE.MeshLambertMaterial({ vertexColors: true }));
    // Receives but does not cast. The terrain is the thing shadows land on; letting it cast onto
    // itself buys nothing at this relief and invites acne across the whole grid.
    mesh.receiveShadow = true;
    this.scene.add(mesh);
    this.groundMesh = mesh;
  }

  // --------------------------------------------------------------- dressing

  /** Paved roadway and sidewalk surfaces. Replaces bare lines with something walkable. */
  setPaving(doc: PavingDocument): void {
    if (this.pavingGroup) this.scene.remove(this.pavingGroup);
    this.pavingGroup = buildPaving(doc, (x, y) => this.groundHeightAt(x, y));
    // Pavement receives; kerbs cast, because a kerb face with no shadow at its foot looks painted on.
    this.pavingGroup.traverse((node) => {
      if (node instanceof THREE.Mesh) {
        node.receiveShadow = true;
        node.castShadow = node.name === 'paving:kerb';
      }
    });
    this.scene.add(this.pavingGroup);
    this.invalidateShadows();
  }

  /** Instanced street furniture and vegetation. */
  setProps(set: ScenePropSet): void {
    this.propSet = set;
    this.rebuildProps();
  }

  /**
   * Re-dress the scene for a season.
   *
   * The prop set has to be rebuilt rather than re-tinted, because winter is not a colour change: a
   * deciduous tree in January is a bare crown of twigs, so the geometry itself differs. Cheap enough
   * to do on a click -- it is one pass over 1,306 instances -- and it does not touch the tiles.
   *
   * The water is re-dressed at the same time. It already chose its recreational craft by season, so
   * without this a click could put sailboats on the river under bare trees.
   */
  setSeason(season: Season): void {
    if (!setSeason(season)) return;
    if (this.propSet) this.rebuildProps();
    if (this.waterDoc) this.setWater(this.waterDoc, season);
  }

  private rebuildProps(): void {
    if (!this.propSet) return;
    if (this.propsGroup) this.scene.remove(this.propsGroup);
    const result = buildProps(this.propSet, (x, y) => this.groundHeightAt(x, y));
    this.propsGroup = result.group;
    this.propStats = { instances: result.instanceCount, drawCalls: result.drawCalls };
    this.scene.add(this.propsGroup);
    this.invalidateShadows();
  }

  /**
   * Per-building facade appearance. Applied when tiles are built, so it must be supplied before
   * streaming starts; any tiles already resident are dropped so they pick it up.
   */
  setFacades(doc: FacadeDocument): void {
    this.facades = doc;
    for (const tileId of [...this.resident.keys()]) this.unloadTile(tileId);
    this.options.streamer.reset();
  }

  get propDiagnostics(): { instances: number; drawCalls: number } {
    return this.propStats;
  }

  /** The facade style for a building, so the shell can show where its appearance came from. */
  facadeStyle(localId: string): FacadeStyle | null {
    return this.facades?.styles?.[localId] ?? null;
  }

  /** Draw the district boundary as a ground line so the walkable extent is legible. */
  addBoundary(ringLonLat: [number, number][]): void {
    const points = ringLonLat.map(([lon, lat]) => {
      const [x, y] = this.options.frame.toScene(lon, lat, 0);
      const r = Frame.sceneToRender([x, y, this.groundHeightAt(x, y) + 0.6]);
      return new THREE.Vector3(r[0], r[1], r[2]);
    });
    const line = new THREE.Line(
      new THREE.BufferGeometry().setFromPoints(points),
      new THREE.LineBasicMaterial({ color: 0xffd479, transparent: true, opacity: 0.55 }),
    );
    this.scene.add(line);
  }

  /** Street centerlines from the walk network, so the ground is not featureless. */
  addWalkNetwork(nodes: [number, number][], edges: Array<{ a: number; b: number; kind: string }>): void {
    const streetPoints: THREE.Vector3[] = [];
    const footPoints: THREE.Vector3[] = [];
    for (const edge of edges) {
      const a = nodes[edge.a];
      const b = nodes[edge.b];
      if (!a || !b) continue;
      const ra = Frame.sceneToRender([a[0], a[1], this.groundHeightAt(a[0], a[1]) + 0.12]);
      const rb = Frame.sceneToRender([b[0], b[1], this.groundHeightAt(b[0], b[1]) + 0.12]);
      const target = edge.kind === 'footway' ? footPoints : streetPoints;
      target.push(new THREE.Vector3(ra[0], ra[1], ra[2]), new THREE.Vector3(rb[0], rb[1], rb[2]));
    }
    for (const [points, color, width] of [
      [streetPoints, 0x2b2a28, 1],
      [footPoints, 0x9a958c, 1],
    ] as const) {
      if (!points.length) continue;
      const segments = new THREE.LineSegments(
        new THREE.BufferGeometry().setFromPoints(points),
        new THREE.LineBasicMaterial({ color, linewidth: width, transparent: true, opacity: 0.85 }),
      );
      this.scene.add(segments);
    }
  }

  // ----------------------------------------------------------------- streaming

  async updateStreaming(
    cameraScenePos: [number, number, number],
    forward: [number, number, number],
    mode: ViewerMode,
    plannedRoute?: [number, number, number][],
  ): Promise<void> {
    const viewport = {
      fovY: (this.camera.fov * Math.PI) / 180,
      heightPx: this.renderer.domElement.height,
    };

    const update = this.options.streamer.update(
      { position: cameraScenePos, forward },
      viewport,
      mode,
      { plannedRoute },
    );

    for (const tileId of update.removed) this.unloadTile(tileId);

    for (const decision of update.added) {
      const key = `${decision.tile.tile_id}@${decision.level}`;
      if (this.inFlight.has(key)) continue;
      this.inFlight.add(key);
      void this.loadTile(decision.tile, decision.level).finally(() => this.inFlight.delete(key));
    }

    if (update.added.length || update.removed.length) {
      this.options.bus.emit('tiles:changed', {
        resident: this.options.streamer.resident,
        added: update.added.map((d) => d.tile.tile_id),
        removed: update.removed,
      });
    }

    if (update.foreignAssets.length) this.ensureBridgePlaceholder();
  }

  private async loadTile(tile: Tile, level: number): Promise<void> {
    const content = tile.content.find((c) => c.level === level);
    if (!content) {
      // The index claims a level this tile does not ship. Tell the streamer, or it will keep
      // asking forever.
      this.options.streamer.markFailed(tile.tile_id);
      this.options.bus.emit('warning', {
        code: 'tile.level_missing',
        message: `tile ${tile.tile_id} has no payload for level ${level}`,
      });
      return;
    }
    const url = this.options.registry.urlFor(this.options.district, content.url, 'tiles');
    try {
      const response = await fetch(url);
      if (!response.ok) throw new Error(`${response.status} ${response.statusText}`);
      const payload = (await response.json()) as TilePayload;
      this.unloadTile(tile.tile_id);
      this.resident.set(tile.tile_id, this.buildTileGroup(payload, level));
      // Only now is the tile genuinely present. The streamer records nothing until this point,
      // so a failed fetch cannot leave a hole it believes is filled.
      this.options.streamer.markLoaded(tile.tile_id, level);
    } catch (error) {
      this.options.streamer.markFailed(tile.tile_id);
      this.options.bus.emit('warning', {
        code: 'tile.load_failed',
        message: `tile ${tile.tile_id} level ${level} failed to load; will retry`,
        detail: error instanceof Error ? error.message : error,
      });
    }
  }

  private unloadTile(tileId: string): void {
    const existing = this.resident.get(tileId);
    if (!existing) return;
    this.tileRoot.remove(existing.group);
    existing.group.traverse((node) => {
      if (node instanceof THREE.Mesh) {
        node.geometry.dispose();
        (Array.isArray(node.material) ? node.material : [node.material]).forEach((m) => m.dispose());
      }
    });
    for (const id of existing.buildings.keys()) this.metadataById.delete(id);
    this.resident.delete(tileId);
    this.invalidateShadows();
    this.invalidateCollision();
  }

  /**
   * Extrude a tile's footprints.
   *
   * One merged BufferGeometry per tile keeps draw calls at one per tile rather than one per
   * building. Per-building identity survives via a vertex-range table on the mesh's userData, which
   * is what makes picking work without a mesh per building.
   */
  private buildTileGroup(payload: TilePayload, level: number): ResidentTile {
    const group = new THREE.Group();
    const buildings = new Map<string, TileBuilding>();

    const positions: number[] = [];
    const normals: number[] = [];
    const colors: number[] = [];
    const ranges: Array<{ id: string; start: number; count: number }> = [];

    const [ox, oy] = payload.origin_m;
    const color = new THREE.Color();

    for (const building of payload.buildings) {
      const start = positions.length / 3;
      const ring = building.ring;
      if (ring.length < 3) continue;

      buildings.set(building.id, building);
      if (payload.carries_metadata) this.metadataById.set(building.id, building);

      const tint = this.confidenceOverlay
        ? CONFIDENCE_COLOR[building.c] ?? 0x888888
        : this.facadeColor(building);

      const style = this.confidenceOverlay ? undefined : this.facades?.styles[building.id];
      const baseZ = building.base;
      const height = Math.max(building.h, 1.5);
      const topZ = building.base + height;

      // The roof deck sits below the top of the wall, not level with it. DUMBO is a flat-roofed
      // district -- 73 of the 81 buildings here that carry an OSM roof:shape tag are flat -- and a
      // flat roof rendered as a bare plane meets the sky as a knife edge. A real one has a parapet
      // standing above the deck, and that rim is most of the roofline's silhouette from the bridge.
      //
      // Taken out of the declared height rather than added to it: `height_roof` is an authoritative
      // measurement of the building's extent, so the walls still reach exactly that and only the
      // deck moves down. Suppressed on very low structures, where a 0.9 m parapet would be most of
      // the building.
      const parapet = style && height > 4 ? this.facades?.parapet_height_m ?? 0.9 : 0;
      const deckZ = topZ - parapet;

      // Walls. Scene (x, y, z) -> render (x, z, -y).
      for (let i = 0; i < ring.length; i++) {
        const a = ring[i];
        const b = ring[(i + 1) % ring.length];
        const ax = ox + a[0];
        const ay = oy + a[1];
        const bx = ox + b[0];
        const by = oy + b[1];

        const dx = bx - ax;
        const dy = by - ay;
        const len = Math.hypot(dx, dy) || 1;
        // Outward normal of a counter-clockwise ring, in render space.
        const nx = dy / len;
        const nz = dx / len;

        // Shade walls slightly by orientation so massing reads without textures.
        const shade = 0.78 + 0.22 * Math.abs(nx);

        // Split each wall into horizontal courses so procedural window bands have somewhere to
        // live. Two courses per storey, capped: enough to resolve a window band and the spandrel
        // above it, cheap enough for a whole district. Without this a wall is two triangles and
        // cannot show banding at all.
        const courses = style ? Math.max(4, Math.min(28, Math.round(height / 1.75))) : 1;

        // ...and into vertical bays, which is what stops a facade reading as a striped box. Courses
        // alone give continuous ribbons at every storey; real windows are punched openings with
        // masonry piers between them, and the pier is the thing the eye uses to judge a building's
        // width and scale. The pitch comes from the city's designation register where it has an
        // opinion -- a row house's two-bay front and a daylight factory's wide industrial opening are
        // genuinely different -- and falls back to a warehouse-ish 4 m where it does not.
        const bayPitch = style?.bay_m ?? 4.0;
        const bays = style ? Math.max(1, Math.min(24, Math.round(len / bayPitch))) : 1;

        for (let c = 0; c < courses; c++) {
          const f0 = c / courses;
          const f1 = (c + 1) / courses;
          const z0 = baseZ + height * f0;
          const z1 = baseZ + height * f1;

          const band = facadeBandFactor((f0 + f1) / 2, style, height);

          for (let s = 0; s < bays; s++) {
            const g0 = s / bays;
            const g1 = (s + 1) / bays;
            const px0 = ax + dx * g0;
            const py0 = ay + dy * g0;
            const px1 = ax + dx * g1;
            const py1 = ay + dy * g1;

            // Only the middle of a bay is glazed; the edges are the pier between openings. When the
            // course is not a window course this is 1 and the whole bay renders as plain wall.
            const pier = bays > 1 ? facadeBayFactor((g0 + g1) / 2, bays, style) : 1;
            color.setHex(tint).multiplyScalar(shade * (band < 1 ? 1 - (1 - band) * pier : band));

            const quad = [
              [px0, z0, py0], [px1, z0, py1], [px1, z1, py1],
              [px0, z0, py0], [px1, z1, py1], [px0, z1, py0],
            ];
            for (const [vx, vy, vy2] of quad) {
              positions.push(vx, vy, -vy2);
              normals.push(nx, 0, nz);
              colors.push(color.r, color.g, color.b);
            }
          }
        }
      }

      // Roof, fan-triangulated from the ring centroid. Adequate for the convex-ish footprints in
      // this district and free of a triangulation dependency; concave roofs get a slight overshoot
      // that is well inside the level's declared geometric error.
      let cx = 0;
      let cy = 0;
      for (const point of ring) {
        cx += point[0];
        cy += point[1];
      }
      cx = ox + cx / ring.length;
      cy = oy + cy / ring.length;

      color.setHex(tint).multiplyScalar(1.12);
      for (let i = 0; i < ring.length; i++) {
        const a = ring[i];
        const b = ring[(i + 1) % ring.length];
        positions.push(cx, deckZ, -cy);
        positions.push(ox + a[0], deckZ, -(oy + a[1]));
        positions.push(ox + b[0], deckZ, -(oy + b[1]));
        for (let k = 0; k < 3; k++) {
          normals.push(0, 1, 0);
          colors.push(color.r, color.g, color.b);
        }
      }

      // Coping: the flat top of the parapet, so the rim reads as a built edge rather than as a wall
      // that simply stops. Only worth the triangles where there is a parapet at all.
      if (parapet > 0) {
        color.setHex(tint).multiplyScalar(1.2);
        const inset = 0.28;
        // Pull each ring vertex toward the centroid by a fixed distance to get the inner edge.
        const inner = ring.map(([px, py]) => {
          const wx = ox + px;
          const wy = oy + py;
          const span = Math.hypot(cx - wx, cy - wy) || 1;
          return [wx + ((cx - wx) / span) * inset, wy + ((cy - wy) / span) * inset];
        });
        for (let i = 0; i < ring.length; i++) {
          const j = (i + 1) % ring.length;
          const outerA = [ox + ring[i][0], oy + ring[i][1]];
          const outerB = [ox + ring[j][0], oy + ring[j][1]];
          const quad = [outerA, outerB, inner[j], outerA, inner[j], inner[i]];
          for (const [vx, vy] of quad) {
            positions.push(vx, topZ, -vy);
            normals.push(0, 1, 0);
            colors.push(color.r, color.g, color.b);
          }
        }
      }

      ranges.push({ id: building.id, start, count: positions.length / 3 - start });
    }

    if (positions.length) {
      const geometry = new THREE.BufferGeometry();
      geometry.setAttribute('position', new THREE.Float32BufferAttribute(positions, 3));
      geometry.setAttribute('normal', new THREE.Float32BufferAttribute(normals, 3));
      geometry.setAttribute('color', new THREE.Float32BufferAttribute(colors, 3));
      geometry.computeBoundingSphere();

      const mesh = new THREE.Mesh(
        geometry,
        new THREE.MeshLambertMaterial({ vertexColors: true }),
      );
      // Buildings both cast and receive: a DUMBO street canyon is defined by the shadow the north
      // side throws across it, and by the one a warehouse throws onto its neighbour.
      mesh.castShadow = true;
      mesh.receiveShadow = true;
      mesh.userData = { tileId: payload.tile_id, level, ranges, selectable: level <= 1 };
      group.add(mesh);
    }

    this.tileRoot.add(group);
    this.invalidateShadows();
    this.invalidateCollision();
    return { group, level, buildings, origin: [ox, oy] };
  }

  /**
   * Facade colour for a building: the sourced style when one exists, otherwise a stable hash of the
   * ID so the district still varies before facades.json is built.
   */
  private facadeColor(building: TileBuilding): number {
    const style = this.facades?.styles[building.id];
    return style ? parseColor(style.color, hashColor(building.id)) : hashColor(building.id);
  }

  // ----------------------------------------------------------------- picking

  pick(ndcX: number, ndcY: number): { id: string; metadata: TileBuilding } | null {
    this.raycaster.setFromCamera(new THREE.Vector2(ndcX, ndcY), this.camera);
    const hits = this.raycaster.intersectObjects(this.tileRoot.children, true);
    for (const hit of hits) {
      const mesh = hit.object as THREE.Mesh;
      const data = mesh.userData as {
        ranges?: Array<{ id: string; start: number; count: number }>;
        selectable?: boolean;
      };
      if (!data.selectable || !data.ranges || hit.face == null) continue;
      const vertexIndex = hit.face.a;
      for (const range of data.ranges) {
        if (vertexIndex >= range.start && vertexIndex < range.start + range.count) {
          const metadata = this.metadataById.get(range.id);
          if (metadata) return { id: range.id, metadata };
        }
      }
    }
    return null;
  }

  /**
   * Ground point under a screen position, for double-click-to-walk-there.
   *
   * Raycasts against terrain and paving first; if the ray misses both — pointing at sky, say —
   * intersects the plane at the camera's own foot height, which keeps the gesture responsive
   * rather than silently doing nothing.
   */
  pickGround(ndcX: number, ndcY: number): [number, number] | null {
    this.raycaster.setFromCamera(new THREE.Vector2(ndcX, ndcY), this.camera);

    const targets: THREE.Object3D[] = [];
    if (this.groundMesh) targets.push(this.groundMesh);
    if (this.pavingGroup) targets.push(this.pavingGroup);
    // Buildings too: double-clicking a facade should walk you to its base, not do nothing.
    targets.push(this.tileRoot);

    const hits = this.raycaster.intersectObjects(targets, true);
    if (hits.length) {
      const p = hits[0].point;
      const scene = Frame.renderToScene([p.x, p.y, p.z]);
      return [scene[0], scene[1]];
    }

    // Nothing hit — the ray is pointing at sky. Fall back to the horizontal plane at the camera's
    // own foot level, so the gesture still does something sensible instead of silently failing.
    const origin = this.raycaster.ray.origin;
    const direction = this.raycaster.ray.direction;
    if (direction.y >= -1e-4) return null;
    const footY = origin.y - this.eyeHeightM;
    const t = (footY - origin.y) / direction.y;
    if (!Number.isFinite(t) || t <= 0) return null;
    const hit = origin.clone().addScaledVector(direction, t);
    const scene = Frame.renderToScene([hit.x, hit.y, hit.z]);
    return [scene[0], scene[1]];
  }

  /** Eye height the shell is using, so ground picking can fall back to the right plane. */
  eyeHeightM = 1.7;

  /** Where a registered asset ended up, so tour `look_at` can target it. */
  resolveBuildingAnchor(localId: string): [number, number, number] | null {
    const building = this.metadataById.get(localId);
    if (!building) return null;
    let cx = 0;
    let cy = 0;
    for (const point of building.ring) {
      cx += point[0];
      cy += point[1];
    }
    // Ring coordinates are tile-local; recover the tile origin from the owning tile.
    for (const [tileId, tile] of this.resident) {
      if (!tile.buildings.has(localId)) continue;
      const indexTile = this.options.streamer.tileIndex.tiles.find((t) => t.tile_id === tileId);
      if (!indexTile) break;
      const [ox, oy] = indexTile.bbox.min;
      return [
        ox + cx / building.ring.length,
        oy + cy / building.ring.length,
        building.base + building.h * 0.6,
      ];
    }
    return null;
  }

  setHighlight(localId: string | null): void {
    if (this.highlight) {
      this.scene.remove(this.highlight);
      this.highlight.geometry.dispose();
      (this.highlight.material as THREE.Material).dispose();
      this.highlight = null;
    }
    if (!localId) return;
    const anchor = this.resolveBuildingAnchor(localId);
    const building = this.metadataById.get(localId);
    if (!anchor || !building) return;

    const marker = new THREE.Mesh(
      new THREE.RingGeometry(6, 8, 32),
      new THREE.MeshBasicMaterial({ color: 0xffd479, side: THREE.DoubleSide, transparent: true, opacity: 0.9 }),
    );
    const render = Frame.sceneToRender([anchor[0], anchor[1], building.base + building.h + 2]);
    marker.position.set(render[0], render[1], render[2]);
    marker.rotation.x = -Math.PI / 2;
    this.scene.add(marker);
    this.highlight = marker;
  }

  setConfidenceOverlay(active: boolean): void {
    if (this.confidenceOverlay === active) return;
    this.confidenceOverlay = active;
    // Rebuild resident tiles so the recolour applies. Cheap at district scale.
    const tiles = [...this.resident.keys()];
    for (const tileId of tiles) this.unloadTile(tileId);
    this.options.streamer.reset();
  }

  get overlayActive(): boolean {
    return this.confidenceOverlay;
  }

  // -------------------------------------------------------------- bridge stub

  /**
   * Draw a labelled wireframe envelope where the Manhattan Bridge is.
   *
   * This is NOT bridge geometry and is never presented as such. It exists so the anti-duplication
   * rule has a visible, honest failure mode: when the bridge module ships a real proxy, this is
   * deleted and nothing else changes.
   */
  /**
   * Show a foreign module's content: its published proxy if it has one, otherwise a labelled
   * placeholder.
   *
   * The placeholder is deliberately ugly — a red wireframe envelope sized from the owner's own
   * published control dimensions — so that nobody mistakes it for their model. When they publish a
   * real proxy this method loads that instead and the placeholder never appears.
   */
  ensureBridgePlaceholder(): void {
    if (this.bridgePlaceholder || this.bridgeProxyPending) return;
    const bridge = this.options.registry.module('manhattan-bridge');
    if (!bridge?.manifest.placement) return;

    const proxyUrn = bridge.manifest.proxy?.asset_id;
    const entry = proxyUrn ? this.options.registry.resolve(proxyUrn) : null;
    const capped = bridge.manifest.proxy?.max_level ?? 2;
    const variant = entry?.asset.variants
      ?.filter((v) => v.level <= capped && v.url && (v.format === 'glb' || v.format === 'gltf'))
      .sort((a, b) => b.level - a.level)[0];

    if (variant?.url) {
      this.bridgeProxyPending = true;
      const url = this.options.registry.urlFor(entry!.module, variant.url);
      void this.loadForeignProxy(url, bridge.manifest.placement, proxyUrn!);
      return;
    }

    this.addPlaceholderEnvelope(bridge);
  }

  /**
   * Load a foreign module's GLB and place it by the placement it published.
   *
   * The module authors in its own engineering frame; `placement` composes that into the shared
   * scene frame, including the vertical datum correction. Nothing here knows what the geometry is.
   */
  private async loadForeignProxy(
    url: string,
    placement: Placement,
    urn: string,
  ): Promise<void> {
    try {
      const { GLTFLoader } = await import('three/examples/jsm/loaders/GLTFLoader.js');
      const loader = new GLTFLoader();
      const gltf = await loader.loadAsync(url);

      const resolved = resolvePlacement(placement);
      const group = new THREE.Group();
      group.add(gltf.scene);

      // Compose the module's local frame into the scene frame, then convert to render axes.
      const origin = applyPlacement(resolved, [0, 0, 0]);
      const renderOrigin = Frame.sceneToRender(origin);
      group.position.set(renderOrigin[0], renderOrigin[1], renderOrigin[2]);
      group.rotation.y = ((placement.yaw_deg ?? 0) * Math.PI) / 180;
      group.scale.setScalar(placement.scale ?? 1);

      group.traverse((node) => {
        if (node instanceof THREE.Mesh) {
          node.userData = { foreign: true, selectable: false, urn };
        }
      });
      group.userData = { foreignModule: 'manhattan-bridge', urn };

      this.scene.add(group);
      this.bridgePlaceholder = group;
      this.options.bus.emit('module:loaded', { moduleId: 'manhattan-bridge' });
    } catch (error) {
      this.options.bus.emit('warning', {
        code: 'foreign.proxy_failed',
        message:
          `The Manhattan Bridge proxy could not be loaded (${error instanceof Error ? error.message : String(error)}); ` +
          'falling back to a labelled placeholder.',
      });
      const bridge = this.options.registry.module('manhattan-bridge');
      if (bridge) this.addPlaceholderEnvelope(bridge);
    } finally {
      this.bridgeProxyPending = false;
    }
  }

  private addPlaceholderEnvelope(bridge: LoadedModule): void {
    const envelope = (bridge.manifest.extensions?.['dumbo-district'] as
      | { placeholder_envelope?: { length_m: number; tower_height_m: number; deck_width_m: number } }
      | undefined)?.placeholder_envelope;
    if (!envelope) return;

    const placement = bridge.manifest.placement;
    if (!placement) return;
    const group = new THREE.Group();

    const box = new THREE.BoxGeometry(envelope.length_m, 3, envelope.deck_width_m);
    const deck = new THREE.LineSegments(
      new THREE.EdgesGeometry(box),
      new THREE.LineBasicMaterial({ color: 0xc4453c, transparent: true, opacity: 0.75 }),
    );
    group.add(deck);

    for (const sign of [-1, 1]) {
      const tower = new THREE.LineSegments(
        new THREE.EdgesGeometry(new THREE.BoxGeometry(18, envelope.tower_height_m, envelope.deck_width_m)),
        new THREE.LineBasicMaterial({ color: 0xc4453c, transparent: true, opacity: 0.75 }),
      );
      tower.position.set((sign * 448) / 2, envelope.tower_height_m / 2 - 25, 0);
      group.add(tower);
    }

    group.position.set(placement.translation_m[0], placement.translation_m[2] + 41, -placement.translation_m[1]);
    group.rotation.y = ((placement.yaw_deg ?? 0) * Math.PI) / 180;
    group.userData = { placeholder: true, moduleId: 'manhattan-bridge' };

    this.scene.add(group);
    this.bridgePlaceholder = group;
  }

  setTimeOfDay(hhmm: string): void {
    const [hours, minutes] = hhmm.split(':').map(Number);
    const when = new Date();
    when.setHours(hours, minutes, 0, 0);
    this.setSunFor(when);
  }

  /**
   * Light the district for a real instant, at its real latitude.
   *
   * The old rig swept a light along a fixed arc and painted a flat blue-grey behind it, which is why
   * the buildings looked dark and the sky dull: at its own default of 16:30 it put the sun 20° up
   * and rendered a sunlit brick wall at lightness 0.11, where brick actually photographs around
   * 0.40. Real geometry fixes the sun; a brighter sky reference and an exposure that falls with the
   * sun fix the rest.
   */
  setSunFor(when: Date, lat = 40.703, lon = -73.989): void {
    this.applyLighting(skyLighting(sunPosition(lat, lon, when)));
  }

  /** Light the district for a named look rather than a moment. */
  setLightingPreset(preset: LightingPreset): void {
    this.applyLighting(skyLighting(LIGHTING_PRESETS[preset]));
  }

  private applyLighting(rig: SkyLighting): void {
    const [dx, dy, dz] = rig.sunDirection;
    // Positioned relative to the shadow target, not the world origin. The target follows the viewer,
    // so an absolute position would swing the light direction as you walked across the district.
    this.sunOffset.set(dx * 700, dy * 700, dz * 700);
    this.sun.position.copy(this.sun.target.position).add(this.sunOffset);
    this.sun.color.setHex(rig.sunColour);
    this.sun.intensity = rig.sunIntensity;

    const [fx, fy, fz] = rig.fillDirection;
    this.fill.position.set(fx * 900, fy * 900, fz * 900);
    this.fill.color.setHex(rig.fillColour);
    this.fill.intensity = rig.fillIntensity;

    this.hemi.color.setHex(rig.hemiSky);
    this.hemi.groundColor.setHex(rig.hemiGround);
    this.hemi.intensity = rig.hemiIntensity;

    this.bounce.color.setHex(rig.bounceColour);
    this.bounce.intensity = rig.bounceIntensity;

    this.renderer.toneMappingExposure = rig.exposure;

    // The sky is a gradient rather than a flat fill. A real sky is much paler at the horizon than
    // overhead, and that gradient is most of what makes it read as sky rather than as a background
    // colour -- particularly here, where half the view is across open water to the horizon.
    this.skyUniforms.top.value.setHex(rig.skyTop);
    this.skyUniforms.horizon.value.setHex(rig.skyHorizon);

    // Fog takes the horizon colour, so distance fades into the sky instead of into a grey band.
    if (this.scene.fog) (this.scene.fog as THREE.Fog).color.setHex(rig.skyHorizon);

    this.lighting = rig;
    this.invalidateShadows();
  }

  get lightingState(): SkyLighting | null {
    return this.lighting;
  }

  resize(width: number, height: number): void {
    this.camera.aspect = width / Math.max(height, 1);
    this.camera.updateProjectionMatrix();
    this.renderer.setSize(width, height, false);
  }

  render(): void {
    this.updateShadowFrustum();
    this.renderer.render(this.scene, this.camera);
  }

  /**
   * Keep the shadow camera over what the viewer is looking at, and redraw the map only when
   * something moved.
   *
   * A directional shadow map is a fixed-size box, so it has to be aimed. Aiming it at the district
   * would waste almost all of its resolution on places nobody is standing; aiming it at the camera
   * keeps the texels where the eye is.
   *
   * It is aimed *ahead* of the camera rather than at it. Centring on the camera spends half the box
   * on ground behind the viewer's head, and from any raised viewpoint — the aerial look, a bridge
   * walkway — the ground actually in frame falls outside the box entirely and loses its shadows.
   * The lead scales with how far the eye is above the ground, because that is what decides how much
   * ground the view covers.
   *
   * The box is re-centred in whole strides rather than continuously, because a shadow map that
   * shifts by a fraction of a texel every frame shimmers along every straight edge -- and DUMBO is
   * nothing but straight edges.
   */
  private updateShadowFrustum(): void {
    const target = this.sun.target.position;
    const cam = this.camera.position;
    const half = SHADOW_HALF_EXTENT_M;

    this.camera.getWorldDirection(this.viewDirection);
    this.viewDirection.y = 0;
    // Straight down leaves nothing to project; keep the box under the camera in that case.
    const lead =
      this.viewDirection.lengthSq() < 1e-6
        ? 0
        : Math.min(half * 0.55 + Math.max(0, cam.y) * 0.8, half * 2);
    if (lead > 0) this.viewDirection.normalize().multiplyScalar(lead);
    else this.viewDirection.set(0, 0, 0);

    const step = SHADOW_STEP_M;
    const x = Math.round((cam.x + this.viewDirection.x) / step) * step;
    const z = Math.round((cam.z + this.viewDirection.z) / step) * step;

    if (x === target.x && z === target.z && !this.shadowDirty) return;

    target.set(x, 0, z);
    this.sun.target.updateMatrixWorld();
    this.sun.position.copy(target).add(this.sunOffset);

    const shadowCamera = this.sun.shadow.camera;
    shadowCamera.left = -half;
    shadowCamera.right = half;
    shadowCamera.top = half;
    shadowCamera.bottom = -half;
    shadowCamera.updateProjectionMatrix();

    this.renderer.shadowMap.needsUpdate = true;
    this.shadowDirty = false;
  }

  /** Mark the shadow map stale: the sun moved, a tile arrived, or the props were rebuilt. */
  private invalidateShadows(): void {
    this.shadowDirty = true;
  }

  /**
   * The numbers that decide whether a shadow appears. Exposed because when one is missing the
   * cause is always one of these and never visible in a screenshot: either the renderer is not
   * drawing shadows, or the light is not casting, or no mesh is casting, or the shadow camera's
   * box does not contain the geometry that ought to be casting into view.
   */
  shadowDiagnostics(): Record<string, unknown> {
    let casters = 0;
    let receivers = 0;
    let castersInBox = 0;
    const half = SHADOW_HALF_EXTENT_M;
    const target = this.sun.target.position;
    const box = new THREE.Box3();

    this.scene.traverse((object) => {
      const mesh = object as THREE.Mesh;
      if (!mesh.isMesh && !(mesh as unknown as THREE.InstancedMesh).isInstancedMesh) return;
      if (mesh.receiveShadow) receivers++;
      if (!mesh.castShadow) return;
      casters++;
      box.setFromObject(mesh);
      if (
        box.max.x >= target.x - half &&
        box.min.x <= target.x + half &&
        box.max.z >= target.z - half &&
        box.min.z <= target.z + half
      ) {
        castersInBox++;
      }
    });

    return {
      shadowMapEnabled: this.renderer.shadowMap.enabled,
      shadowMapAutoUpdate: this.renderer.shadowMap.autoUpdate,
      sunCastShadow: this.sun.castShadow,
      sunIntensity: this.sun.intensity,
      sunPosition: this.sun.position.toArray().map((v) => Math.round(v)),
      shadowTarget: target.toArray().map((v) => Math.round(v)),
      shadowCamera: {
        near: this.sun.shadow.camera.near,
        far: this.sun.shadow.camera.far,
        halfExtent: half,
      },
      casters,
      castersInBox,
      receivers,
    };
  }

  captureFrame(width: number, height: number): string {
    this.render();
    const source = this.renderer.domElement;
    const canvas = document.createElement('canvas');
    canvas.width = width;
    canvas.height = height;
    const ctx = canvas.getContext('2d');
    if (!ctx) return source.toDataURL('image/png');
    // Cover-fit the framebuffer into the requested aspect.
    const scale = Math.max(width / source.width, height / source.height);
    const dw = source.width * scale;
    const dh = source.height * scale;
    ctx.drawImage(source, (width - dw) / 2, (height - dh) / 2, dw, dh);
    return canvas.toDataURL('image/png');
  }

  get residentTileCount(): number {
    return this.resident.size;
  }

  get residentLevels(): number[] {
    return [...new Set([...this.resident.values()].map((t) => t.level))].sort();
  }

  metadataFor(localId: string): TileBuilding | undefined {
    return this.metadataById.get(localId);
  }

  toAssetMetadata(building: TileBuilding): AssetMetadata {
    return {
      asset_id: `urn:d3d:dumbo-district:${building.id}`,
      module_id: 'dumbo-district',
      local_id: building.id,
      display_name: building.name ?? building.id,
      category: 'building',
      source_basis: (building.basis as AssetMetadata['source_basis']) ?? ['official_dataset'],
      source_refs: ['DSRC-001', 'DSRC-002'],
      confidence: building.c,
      control_refs: building.ctl,
      open_questions: building.oq,
      review_status: 'unreviewed',
      last_modified_by: 'dumbo-district-3d/scripts@1.0.0',
      units: 'meters',
      attributes: building.attrs,
    };
  }
}
