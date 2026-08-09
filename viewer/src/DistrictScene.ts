/**
 * District scene: three.js rendering driven entirely by the shared kernel.
 *
 * The division of labour matters. Everything about *what* should be on screen — which tiles, at
 * which level, where the camera is during a tour — is decided by @d3d/viewer-kernel. This file only
 * turns those decisions into three.js objects. That is what keeps the district shell and the bridge
 * shell from re-implementing each other.
 */

import * as THREE from 'three';
import type { AssetMetadata, Tile, ViewerMode } from '@d3d/contracts';
import {
  EventBus,
  Frame,
  LodSelector,
  TileStreamer,
  type KernelEvents,
  type LoadedModule,
  type ModuleRegistry,
} from '@d3d/viewer-kernel';

import type { GroundGrid } from './GroundGrid';
import {
  buildPaving,
  buildProps,
  facadeBandFactor,
  parseColor,
  type FacadeDocument,
  type PavingDocument,
} from './SceneDressing';
import type { ScenePropSet } from '@d3d/contracts';

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

function hashColor(id: string): number {
  let hash = 0;
  for (let i = 0; i < id.length; i++) hash = (hash * 31 + id.charCodeAt(i)) >>> 0;
  return PALETTE[hash % PALETTE.length];
}
interface ResidentTile {
  group: THREE.Group;
  level: number;
  buildings: Map<string, TileBuilding>;
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
  private readonly hemi: THREE.HemisphereLight;
  private readonly raycaster = new THREE.Raycaster();
  private highlight: THREE.Mesh | null = null;
  private confidenceOverlay = false;
  private bridgePlaceholder: THREE.Group | null = null;
  private ground: GroundGrid | null = null;
  private groundMesh: THREE.Mesh | null = null;
  private pavingGroup: THREE.Group | null = null;
  private propsGroup: THREE.Group | null = null;
  private facades: FacadeDocument | null = null;
  private propStats = { instances: 0, drawCalls: 0 };

  constructor(canvas: HTMLCanvasElement, options: SceneOptions) {
    this.options = options;

    this.renderer = new THREE.WebGLRenderer({ canvas, antialias: true, preserveDrawingBuffer: true });
    this.renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    this.renderer.shadowMap.enabled = false;

    this.camera = new THREE.PerspectiveCamera(62, 1, 0.15, 6000);

    this.scene.background = new THREE.Color(0x9fb6cc);
    this.scene.fog = new THREE.Fog(0x9fb6cc, 500, 2400);

    this.hemi = new THREE.HemisphereLight(0xcfe0f2, 0x50493f, 1.5);
    this.scene.add(this.hemi);

    this.sun = new THREE.DirectionalLight(0xfff2df, 2.1);
    this.sun.position.set(-180, 260, 140);
    this.scene.add(this.sun);

    this.scene.add(this.tileRoot);
    this.addWater();
  }

  // ------------------------------------------------------------------ ground

  /** Ground height in scene meters, or 0 when no grid has been supplied. */
  groundHeightAt(x: number, y: number): number {
    return this.ground?.heightAt(x, y) ?? 0;
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
    this.scene.add(mesh);
    this.groundMesh = mesh;
  }

  private addWater(): void {
    // The East River, north and west of the district. Rendered at mean high water, which sits
    // 0.59 m above this frame's NAVD88 zero (DCTL-010).
    const water = new THREE.Mesh(
      new THREE.PlaneGeometry(5000, 5000),
      new THREE.MeshLambertMaterial({ color: 0x3f5c74, transparent: true, opacity: 0.92 }),
    );
    water.rotation.x = -Math.PI / 2;
    water.position.set(0, 0.59, 0);
    water.renderOrder = -1;
    this.scene.add(water);
  }

  // --------------------------------------------------------------- dressing

  /** Paved roadway and sidewalk surfaces. Replaces bare lines with something walkable. */
  setPaving(doc: PavingDocument): void {
    if (this.pavingGroup) this.scene.remove(this.pavingGroup);
    this.pavingGroup = buildPaving(doc, (x, y) => this.groundHeightAt(x, y));
    this.scene.add(this.pavingGroup);
  }

  /** Instanced street furniture and vegetation. */
  setProps(set: ScenePropSet): void {
    if (this.propsGroup) this.scene.remove(this.propsGroup);
    const result = buildProps(set, (x, y) => this.groundHeightAt(x, y));
    this.propsGroup = result.group;
    this.propStats = { instances: result.instanceCount, drawCalls: result.drawCalls };
    this.scene.add(this.propsGroup);
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
      const current = this.resident.get(decision.tile.tile_id);
      if (current && current.level === decision.level) continue;
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
    if (!content) return;
    const url = this.options.registry.urlFor(this.options.district, content.url, 'tiles');
    try {
      const response = await fetch(url);
      if (!response.ok) throw new Error(`${response.status}`);
      const payload = (await response.json()) as TilePayload;
      this.unloadTile(tile.tile_id);
      this.resident.set(tile.tile_id, this.buildTileGroup(payload, level));
    } catch (error) {
      this.options.bus.emit('warning', {
        code: 'tile.load_failed',
        message: `tile ${tile.tile_id} level ${level} failed to load`,
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

        for (let c = 0; c < courses; c++) {
          const f0 = c / courses;
          const f1 = (c + 1) / courses;
          const z0 = baseZ + height * f0;
          const z1 = baseZ + height * f1;

          const band = facadeBandFactor((f0 + f1) / 2, style, height);
          color.setHex(tint).multiplyScalar(shade * band);

          const quad = [
            [ax, z0, ay], [bx, z0, by], [bx, z1, by],
            [ax, z0, ay], [bx, z1, by], [ax, z1, ay],
          ];
          for (const [vx, vy, vy2] of quad) {
            positions.push(vx, vy, -vy2);
            normals.push(nx, 0, nz);
            colors.push(color.r, color.g, color.b);
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
        positions.push(cx, topZ, -cy);
        positions.push(ox + a[0], topZ, -(oy + a[1]));
        positions.push(ox + b[0], topZ, -(oy + b[1]));
        for (let k = 0; k < 3; k++) {
          normals.push(0, 1, 0);
          colors.push(color.r, color.g, color.b);
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
      mesh.userData = { tileId: payload.tile_id, level, ranges, selectable: level <= 1 };
      group.add(mesh);
    }

    this.tileRoot.add(group);
    return { group, level, buildings };
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
  ensureBridgePlaceholder(): void {
    if (this.bridgePlaceholder) return;
    const bridge = this.options.registry.module('manhattan-bridge');
    if (!bridge?.manifest.placement) return;

    const envelope = (bridge.manifest.extensions?.['dumbo-district'] as
      | { placeholder_envelope?: { length_m: number; tower_height_m: number; deck_width_m: number } }
      | undefined)?.placeholder_envelope;
    if (!envelope) return;

    const placement = bridge.manifest.placement;
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

    // The deck sits at the bridge's own clearance above its datum; the placement's z already
    // carries the MHW -> NAVD88 correction (DCTL-010).
    group.position.set(placement.translation_m[0], placement.translation_m[2] + 41, -placement.translation_m[1]);
    group.rotation.y = ((placement.yaw_deg ?? 0) * Math.PI) / 180;
    group.userData = { placeholder: true, moduleId: 'manhattan-bridge' };

    this.scene.add(group);
    this.bridgePlaceholder = group;
  }

  setTimeOfDay(hhmm: string): void {
    const [hours, minutes] = hhmm.split(':').map(Number);
    const t = (hours + minutes / 60 - 6) / 12; // 06:00 -> 0, 18:00 -> 1
    const angle = Math.max(0.03, Math.min(1, t)) * Math.PI;
    const elevation = Math.sin(angle);
    this.sun.position.set(Math.cos(angle) * -320, Math.max(20, elevation * 320), 160);
    this.sun.intensity = 0.5 + elevation * 1.8;
    const warmth = 1 - elevation;
    this.sun.color.setRGB(1, 0.95 - warmth * 0.2, 0.87 - warmth * 0.3);
    const sky = new THREE.Color().setHSL(0.58, 0.35, 0.35 + elevation * 0.35);
    this.scene.background = sky;
    if (this.scene.fog) (this.scene.fog as THREE.Fog).color = sky;
    this.hemi.intensity = 0.6 + elevation * 1.1;
  }

  resize(width: number, height: number): void {
    this.camera.aspect = width / Math.max(height, 1);
    this.camera.updateProjectionMatrix();
    this.renderer.setSize(width, height, false);
  }

  render(): void {
    this.renderer.render(this.scene, this.camera);
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
