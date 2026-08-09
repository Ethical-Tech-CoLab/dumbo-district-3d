import { useCallback, useEffect, useRef, useState } from 'react';
import * as THREE from 'three';
import type { AssetMetadata, BasemapSet, TourScript, ViewerMode } from '@d3d/contracts';
import {
  BasemapController,
  EventBus,
  Frame,
  LodSelector,
  MapCamera,
  ModuleRegistry,
  TileStreamer,
  TourPlayer,
  type CapturedPhoto,
  type KernelEvents,
  type LoadedModule,
} from '@d3d/viewer-kernel';

import { DistrictScene, type TileBuilding } from './DistrictScene';
import { FrameLoop } from './FrameLoop';
import { GroundGrid, type GroundGridDocument } from './GroundGrid';
import { applyObservedPalette } from './SceneDressing';
import { WalkControls } from './WalkControls';
import { WalkRouter, type WalkNetwork } from './WalkRouter';
import MetadataPanel, { type FacadeEvidence } from './components/MetadataPanel';
import TourPanel from './components/TourPanel';
import Hud from './components/Hud';
import MapView from './components/MapView';
import PhotoStrip from './components/PhotoStrip';

const DISTRICT_MANIFEST = 'district/district-manifest.json';
const TOUR_INDEX = 'tours/index.json';

/** Eye height and pace defaults, mirroring DCTL-050 and DCTL-052. */
const EYE_HEIGHT_M = 1.65;
const WALK_PACE_MPS = 1.3;
const MAX_PACE_MPS = 2.2;
/** DCTL-055. Shift-to-hurry ceiling, deliberately above the free-walk clamp. */
const SPRINT_PACE_MPS = 6.6;
/**
 * How long a single click waits to see whether it is really the first half of a double click.
 * Long enough to catch a deliberate double click, short enough that selection still feels immediate.
 */
const DOUBLE_CLICK_GRACE_MS = 260;

interface TourSummary {
  id: string;
  title: string;
  url: string;
  description?: string;
}

export interface Diagnostics {
  fps: number;
  residentTiles: number;
  levels: number[];
  mode: ViewerMode;
  position: [number, number, number];
  heading: number;
  budgetPx: number;
}

/** Codes that describe a condition which can recover on its own, and so should not persist. */
const TRANSIENT_CODES = new Set(['tile.load_failed', 'tile.level_missing', 'foreign.proxy_failed']);

export default function App() {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const [status, setStatus] = useState('Loading district manifest…');
  const [error, setError] = useState<string | null>(null);
  const [ready, setReady] = useState(false);

  const [mode, setMode] = useState<ViewerMode>('walk');
  const [selected, setSelected] = useState<AssetMetadata | null>(null);
  const [overlay, setOverlay] = useState(false);
  const [attributions, setAttributions] = useState<string[]>([]);
  /**
   * Notices, grouped by code.
   *
   * Grouping matters: a brief outage can fail twenty tiles, and twenty near-identical lines will
   * bury the one notice that actually needs reading, such as a basemap licence restriction. Each
   * code collapses to a single entry with a count, and transient codes clear themselves once the
   * underlying condition recovers.
   */
  const [notices, setNotices] = useState<
    Record<string, { message: string; count: number; transient: boolean }>
  >({});
  const [diagnostics, setDiagnostics] = useState<Diagnostics | null>(null);

  const [tours, setTours] = useState<TourSummary[]>([]);
  const [basemap, setBasemap] = useState<BasemapController | null>(null);
  const [mapCamera, setMapCamera] = useState<MapCamera | null>(null);
  const [activeTour, setActiveTour] = useState<TourScript | null>(null);
  const [tourProgress, setTourProgress] = useState<KernelEvents['tour:progress'] | null>(null);
  const [narration, setNarration] = useState<string | null>(null);
  const [instruction, setInstruction] = useState<string | null>(null);
  const [photos, setPhotos] = useState<CapturedPhoto[]>([]);
  const [awaitingUser, setAwaitingUser] = useState(false);
  const [tourSpeed, setTourSpeed] = useState(1);
  /** Transient ripple showing where a double-click go-to landed. */
  const [goToFlash, setGoToFlash] = useState<{ x: number; y: number; at: number } | null>(null);
  const lastTapRef = useRef<{ x: number; y: number; at: number } | null>(null);
  /** Pending single-click selection, cancelled if a double click follows. */
  const pendingClick = useRef<number | null>(null);
  /** Photographic appearance evidence for the selected building, if it has any. */
  const [selectedEvidence, setSelectedEvidence] = useState<FacadeEvidence | null>(null);

  // Mutable engine state kept out of React so the frame loop never re-renders.
  const engine = useRef<{
    scene: DistrictScene;
    frame: Frame;
    bus: EventBus<KernelEvents>;
    registry: ModuleRegistry;
    district: LoadedModule;
    selector: LodSelector;
    streamer: TileStreamer;
    controls: WalkControls;
    router: WalkRouter | null;
    player: TourPlayer | null;
    mode: ViewerMode;
  } | null>(null);

  const pushNotice = useCallback((code: string, message: string) => {
    setNotices((previous) => {
      const existing = previous[code];
      return {
        ...previous,
        [code]: {
          message,
          count: (existing?.count ?? 0) + 1,
          transient: TRANSIENT_CODES.has(code),
        },
      };
    });
  }, []);

  const clearNotice = useCallback((code: string) => {
    setNotices((previous) => {
      if (!previous[code]) return previous;
      const next = { ...previous };
      delete next[code];
      return next;
    });
  }, []);

  /** Warnings without a code, e.g. from callers that only have a message. */
  const pushWarning = useCallback(
    (message: string) => pushNotice(message.slice(0, 48), message),
    [pushNotice],
  );

  // ------------------------------------------------------------------- boot

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;

    let disposed = false;
    let loop: FrameLoop | null = null;

    (async () => {
      try {
        const bus = new EventBus<KernelEvents>();
        const registry = new ModuleRegistry({ bus });

        bus.on('module:missing', ({ moduleId, reason }) => {
          pushNotice(
            `module.missing.${moduleId}`,
            `Optional module '${moduleId}' is unavailable (${reason}). ` +
              'The district still renders; anything that module owns will not.',
          );
        });
        bus.on('warning', ({ code, message }) => pushNotice(code ?? message.slice(0, 48), message));

        setStatus('Loading district manifest…');
        const district = await registry.load(DISTRICT_MANIFEST);
        if (disposed) return;

        if (!district.tileIndex) throw new Error('district manifest declares no tile index');

        const frame = new Frame(district.georeference);
        const selector = new LodSelector(district.ladder);
        const streamer = new TileStreamer(district.tileIndex, selector);

        // Map camera, framed on the whole district to start. A tour can fly it in from here.
        const xs = district.tileIndex.tiles.flatMap((t) => [t.bbox.min[0], t.bbox.max[0]]);
        const ys = district.tileIndex.tiles.flatMap((t) => [t.bbox.min[1], t.bbox.max[1]]);
        const districtBounds: [number, number, number, number] = [
          Math.min(...xs), Math.min(...ys), Math.max(...xs), Math.max(...ys),
        ];
        const districtSpan = Math.max(
          districtBounds[2] - districtBounds[0],
          districtBounds[3] - districtBounds[1],
        );
        const camera = new MapCamera(
          {
            center: [
              (districtBounds[0] + districtBounds[2]) / 2,
              (districtBounds[1] + districtBounds[3]) / 2,
            ],
            spanM: districtSpan * 1.05,
          },
          {
            minSpanM: 60,
            // Allow zooming out well past the district so the map can open on the wider harbour.
            maxSpanM: districtSpan * 6,
            bounds: [
              districtBounds[0] - districtSpan,
              districtBounds[1] - districtSpan,
              districtBounds[2] + districtSpan,
              districtBounds[3] + districtSpan,
            ],
          },
        );
        setMapCamera(camera);

        setStatus('Building scene…');
        const scene = new DistrictScene(canvas, {
          frame,
          selector,
          streamer,
          registry,
          district,
          bus,
        });

        // Terrain first: everything else, including where the camera's feet go, depends on it.
        try {
          const response = await fetch('district/ground-grid.json');
          if (response.ok) {
            scene.setGround(new GroundGrid((await response.json()) as GroundGridDocument));
          } else {
            pushWarning('Ground grid unavailable; the district will render flat at NAVD88 zero.');
          }
        } catch (groundError) {
          pushWarning(`Ground grid failed to load: ${String(groundError)}`);
        }

        // Scene dressing. Each piece is optional: the district renders without any of it, just
        // more plainly. Facades must arrive before streaming so tiles build with them.
        // The observed palette is loaded first: it re-tints the surface colours that the paving
        // pass then bakes into its geometry, so arriving after it would have no effect.
        let paletteCredits: string[] = [];
        try {
          const response = await fetch('district/photo-palette.json');
          if (response.ok) {
            const credits = applyObservedPalette(await response.json());
            // The corpus is hundreds of photographs; the footer can carry a few. The full credit
            // list ships in photo-survey.json and is shown per building when one is selected.
            paletteCredits = credits.slice(0, 2).map((line) => `Surface colours measured from: ${line}`);
          }
        } catch {
          /* no corpus yet; the built-in surface colours stand */
        }

        for (const [file, apply] of [
          ['district/facades.json', (d: unknown) => scene.setFacades(d as never)],
          ['district/paving.json', (d: unknown) => scene.setPaving(d as never)],
          ['district/props.json', (d: unknown) => scene.setProps(d as never)],
          ['district/horizon.json', (d: unknown) => scene.setHorizon(d as never)],
          ['district/water.json', (d: unknown) => scene.setWater(d as never)],
        ] as const) {
          try {
            const response = await fetch(file);
            if (response.ok) apply(await response.json());
          } catch (dressingError) {
            pushWarning(`${file} unavailable: ${String(dressingError)}`);
          }
        }

        // Boundary and street network give the ground legibility before any tile arrives.
        try {
          const boundary = await fetch('district/dumbo-district.geojson');
          if (boundary.ok) {
            const geo = await boundary.json();
            const ring = geo.features?.[0]?.geometry?.coordinates?.[0];
            if (ring) scene.addBoundary(ring);
          }
        } catch {
          /* boundary is decorative; its absence is not worth a warning */
        }

        let router: WalkRouter | null = null;
        try {
          const response = await fetch('district/walk-network.json');
          if (response.ok) {
            const network = (await response.json()) as WalkNetwork;
            scene.addWalkNetwork(network.nodes, network.edges);
            router = new WalkRouter(network, { avoidStairs: false });
          }
        } catch (walkError) {
          pushWarning(`Walk network unavailable: ${String(walkError)}`);
        }

        // Start on Washington Street at Water Street, looking toward the Manhattan Bridge: the view
        // the district exists to deliver.
        const start = frame.toScene(-73.98958, 40.7032, 0);
        const controls = new WalkControls(
          canvas,
          { position: [start[0], start[1], 0], headingDeg: 20, pitchDeg: 4, moving: false },
          { maxSpeed: MAX_PACE_MPS, sprintSpeed: SPRINT_PACE_MPS, walkSpeed: WALK_PACE_MPS },
        );

        scene.setTimeOfDay('16:30');
        setAttributions([...registry.attributions(), ...paletteCredits]);

        engine.current = {
          scene, frame, bus, registry, district, selector, streamer, controls,
          router, player: null, mode: 'walk',
        };

        // Dev-only handle so the running engine can be inspected from the console or a browser
        // automation harness. Never exposed in a production build.
        if (import.meta.env.DEV) {
          (window as unknown as { __d3d?: unknown }).__d3d = engine;
        }

        // ------------------------------------------------------------ events

        bus.on('tour:progress', (progress) => setTourProgress(progress));
        bus.on('tour:narrate', ({ text, durationS }) => {
          setNarration(text);
          window.setTimeout(() => {
            setNarration((current) => (current === text ? null : current));
          }, Math.max(2500, durationS * 1000));
        });
        bus.on('tour:instruction', ({ instruction: text, streetName }) => {
          const label = streetName ? `${text}` : text;
          setInstruction(label);
          window.setTimeout(() => setInstruction((c) => (c === label ? null : c)), 6000);
        });
        bus.on('tour:waiting', () => setAwaitingUser(true));
        bus.on('tour:capture', (photo) => {
          const dataUrl = scene.captureFrame(photo.width, photo.height);
          setPhotos((previous) => [...previous, { ...photo, dataUrl }]);
        });
        bus.on('tour:finished', () => setAwaitingUser(false));
        bus.on('environment:changed', ({ timeOfDay }) => {
          if (timeOfDay) scene.setTimeOfDay(timeOfDay);
        });
        bus.on('map:goto', ({ center, spanM, durationS, easing }) => {
          void camera.flyTo(
            { center, spanM },
            durationS,
            easing as 'linear' | 'ease_in' | 'ease_out' | 'ease_in_out',
          );
        });
        bus.on('mode:changed', ({ mode: next }) => {
          setMode(next);
          if (engine.current) engine.current.mode = next;
        });
        bus.on('handoff:enter', ({ moduleId, entryId }) => {
          pushWarning(
            `Tour requested inspect handoff to '${moduleId}' entry '${entryId}'. ` +
              'The bridge module publishes no inspect UI yet, so the camera framing was applied ' +
              'and the LOD budget tightened, but no CAD geometry is available to show.',
          );
        });
        bus.on('asset:selected', ({ assetId }) => {
          if (!assetId) {
            setSelected(null);
            scene.setHighlight(null);
            return;
          }
          const localId = assetId.split(':').pop()!;
          const building = scene.metadataFor(localId);
          if (building) {
            setSelected(scene.toAssetMetadata(building));
            scene.setHighlight(localId);
          }
        });

        // ------------------------------------------------------------- tours

        try {
          const response = await fetch(TOUR_INDEX);
          if (response.ok) setTours((await response.json()) as TourSummary[]);
        } catch {
          /* tours are optional */
        }

        // ----------------------------------------------------------- basemap

        try {
          const response = await fetch('district/basemap.json');
          if (response.ok) {
            const set = (await response.json()) as BasemapSet;
            // Credentials come from the build environment, never from the basemap document. Any
            // VITE_BASEMAP_KEY_* variable present is offered to the controller, which then treats
            // the layers naming it as usable.
            const credentials: Record<string, string> = {};
            for (const [key, value] of Object.entries(import.meta.env)) {
              if (key.startsWith('VITE_BASEMAP_KEY_') && typeof value === 'string') {
                credentials[key] = value;
              }
            }
            setBasemap(new BasemapController(set, credentials));
          }
        } catch (basemapError) {
          pushWarning(`Basemap configuration unavailable: ${String(basemapError)}`);
        }

        // -------------------------------------------------------- frame loop

        const clock = new THREE.Clock();
        let lastStream = 0;
        let frames = 0;
        let fpsClock = 0;
        let fps = 0;

        const resize = () => {
          const width = canvas.clientWidth || window.innerWidth;
          const height = canvas.clientHeight || window.innerHeight;
          scene.resize(width, height);
        };
        resize();
        window.addEventListener('resize', resize);

        const tick = (dtRaw: number) => {
          if (disposed) return;

          const dt = Math.min(dtRaw, 0.1);
          clock.getDelta();
          const state = engine.current;
          if (!state) return;

          let scenePosition: [number, number, number];
          let headingDeg: number;
          let pitchDeg: number;

          if (state.player && state.player.isPlaying) {
            const camera = state.player.update(dt);
            scenePosition = camera.position;
            headingDeg = camera.headingDeg;
            pitchDeg = camera.pitchDeg;
            // Keep manual controls in sync so releasing the tour does not teleport the user.
            state.controls.teleport(
              [camera.position[0], camera.position[1], 0],
              camera.headingDeg,
              camera.pitchDeg,
            );
          } else {
            state.controls.update(dt);
            const walk = state.controls.state;
            const groundZ = scene.groundHeightAt(walk.position[0], walk.position[1]);
            scenePosition = [walk.position[0], walk.position[1], groundZ + EYE_HEIGHT_M];
            headingDeg = walk.headingDeg;
            pitchDeg = walk.pitchDeg;
            if (state.player) state.player.update(0);
          }

          // Scene ENU (Z-up) to render (Y-up), by the contract's fixed convention.
          const render = Frame.sceneToRender(scenePosition);
          scene.camera.position.set(render[0], render[1], render[2]);

          scene.updateFarField(dt);

          const yaw = (headingDeg * Math.PI) / 180;
          const pitch = (pitchDeg * Math.PI) / 180;
          const lookScene: [number, number, number] = [
            scenePosition[0] + Math.sin(yaw) * Math.cos(pitch) * 50,
            scenePosition[1] + Math.cos(yaw) * Math.cos(pitch) * 50,
            scenePosition[2] + Math.sin(pitch) * 50,
          ];
          const lookRender = Frame.sceneToRender(lookScene);
          scene.camera.lookAt(lookRender[0], lookRender[1], lookRender[2]);

          // Stream at 4 Hz: tile decisions do not need to be a per-frame cost.
          lastStream += dt;
          if (lastStream > 0.25) {
            lastStream = 0;
            const forward = Frame.headingToForward(headingDeg);
            const planned = state.player?.isPlaying
              ? (state.player.plannedRoute(500, 50) as [number, number, number][])
              : undefined;
            void scene.updateStreaming(scenePosition, forward, state.mode, planned);
            // Once nothing is waiting on a retry, the transient tile notice has resolved itself.
            if (state.streamer.retrying === 0) clearNotice('tile.load_failed');
          }

          scene.render();

          frames += 1;
          fpsClock += dt;
          if (fpsClock >= 0.5) {
            fps = Math.round(frames / fpsClock);
            frames = 0;
            fpsClock = 0;
            setDiagnostics({
              fps,
              residentTiles: scene.residentTileCount,
              levels: scene.residentLevels,
              mode: state.mode,
              position: scenePosition,
              heading: headingDeg,
              budgetPx: state.selector.budgetFor(state.mode),
            });
          }
        };

        setReady(true);
        setStatus('');
        loop = new FrameLoop(tick);
        loop.start();

        return () => {
          window.removeEventListener('resize', resize);
        };
      } catch (bootError) {
        if (!disposed) {
          setError(bootError instanceof Error ? bootError.message : String(bootError));
        }
      }
    })();

    return () => {
      disposed = true;
      loop?.stop();
      engine.current?.controls.dispose();
      engine.current = null;
    };
  }, [pushWarning]);

  // --------------------------------------------------------------- handlers

  /**
   * Single click selects a building.
   *
   * Deliberately does three things it did not before. It ignores the click that ends a drag, so
   * panning never selects by accident. It no longer grabs pointer lock, because dragging is now the
   * primary way to look around and stealing the cursor on every click was hostile. And it defers
   * briefly, so a double-click can cancel it: double-click means "walk there" and should not also
   * leave a building selected, which was the awkward part.
   */
  const handleCanvasClick = useCallback((event: React.MouseEvent<HTMLCanvasElement>) => {
    const state = engine.current;
    if (!state) return;
    if (state.controls.consumeDragClick()) return;

    const rect = event.currentTarget.getBoundingClientRect();
    const ndcX = ((event.clientX - rect.left) / rect.width) * 2 - 1;
    const ndcY = -((event.clientY - rect.top) / rect.height) * 2 + 1;

    if (pendingClick.current) window.clearTimeout(pendingClick.current);
    pendingClick.current = window.setTimeout(() => {
      pendingClick.current = null;
      const hit = state.scene.pick(ndcX, ndcY);
      if (hit) {
        setSelected(state.scene.toAssetMetadata(hit.metadata));
        state.scene.setHighlight(hit.id);
        const style = state.scene.facadeStyle(hit.id);
        setSelectedEvidence(
          style?.basis === 'observed'
            ? {
                color: style.color,
                observed_grade: style.observed_grade,
                colour_source: style.colour_source,
                attribution_text: style.attribution_text,
              }
            : null,
        );
      }
    }, DOUBLE_CLICK_GRACE_MS);
  }, []);

  /**
   * Double-click, or double-tap on touch, walks the party to that spot.
   *
   * Uses the pointer's own coordinates rather than the canvas centre, so you go where you pointed.
   * `onDoubleClick` fires for synthesised double-taps on mobile browsers, so one handler covers
   * both; the explicit touch fallback below catches the ones that do not synthesise it.
   */
  const goToPointer = useCallback((clientX: number, clientY: number, element: HTMLCanvasElement) => {
    const state = engine.current;
    if (!state) return;
    const rect = element.getBoundingClientRect();
    const ndcX = ((clientX - rect.left) / rect.width) * 2 - 1;
    const ndcY = -((clientY - rect.top) / rect.height) * 2 + 1;
    const ground = state.scene.pickGround(ndcX, ndcY);
    if (!ground) return;
    // Snap onto the walk network so you land somewhere a person could stand.
    const snapped = state.router?.snap(ground[0], ground[1]) ?? ground;
    state.controls.teleport([snapped[0], snapped[1], 0]);
    setGoToFlash({ x: clientX - rect.left, y: clientY - rect.top, at: Date.now() });
  }, []);

  const handleCanvasDoubleClick = useCallback(
    (event: React.MouseEvent<HTMLCanvasElement>) => {
      event.preventDefault();
      // Cancel the pending single click: a double-click moves you, and moving you should not also
      // select whatever you happened to aim at.
      if (pendingClick.current) {
        window.clearTimeout(pendingClick.current);
        pendingClick.current = null;
      }
      goToPointer(event.clientX, event.clientY, event.currentTarget);
    },
    [goToPointer],
  );

  /**
   * Touch double-tap for browsers that do not synthesise a dblclick.
   *
   * Ignores anything that was a multi-touch gesture or a drag: lifting the last of two fingers
   * after a pinch used to register as a tap, and a second pinch shortly after would then teleport
   * the walker across the district.
   */
  const handleCanvasTouchEnd = useCallback(
    (event: React.TouchEvent<HTMLCanvasElement>) => {
      const state = engine.current;
      const touch = event.changedTouches[0];
      if (!touch || event.touches.length > 0) return;
      if (state?.controls.consumeDragClick()) {
        lastTapRef.current = null;
        return;
      }
      const now = Date.now();
      const previous = lastTapRef.current;
      lastTapRef.current = { x: touch.clientX, y: touch.clientY, at: now };
      if (
        previous &&
        now - previous.at < 320 &&
        Math.hypot(touch.clientX - previous.x, touch.clientY - previous.y) < 36
      ) {
        if (pendingClick.current) {
          window.clearTimeout(pendingClick.current);
          pendingClick.current = null;
        }
        goToPointer(touch.clientX, touch.clientY, event.currentTarget);
        lastTapRef.current = null;
      }
    },
    [goToPointer],
  );

  /** Double-click on the map walks there too, and switches back to the walk view. */
  const handleMapPick = useCallback(
    (scene: [number, number]) => {
      const state = engine.current;
      if (!state) return;
      const snapped = state.router?.snap(scene[0], scene[1]) ?? scene;
      state.controls.teleport([snapped[0], snapped[1], 0]);
      setMode('walk');
      state.mode = 'walk';
      state.streamer.reset();
    },
    [],
  );

  const changeMode = useCallback((next: ViewerMode) => {
    const state = engine.current;
    setMode(next);
    if (state) {
      state.mode = next;
      state.streamer.reset();
    }
  }, []);

  const toggleOverlay = useCallback(() => {
    const state = engine.current;
    if (!state) return;
    const next = !state.scene.overlayActive;
    state.scene.setConfidenceOverlay(next);
    setOverlay(next);
  }, []);

  const startTour = useCallback(
    async (summary: TourSummary) => {
      const state = engine.current;
      if (!state) return;
      setStatus(`Loading tour ${summary.title}…`);
      try {
        const response = await fetch(summary.url);
        if (!response.ok) throw new Error(`${response.status}`);
        const script = (await response.json()) as TourScript;

        const missing = (script.requires_modules ?? []).filter(
          (moduleId) => !state.registry.module(moduleId),
        );
        if (missing.length) {
          pushWarning(
            `Tour '${script.title}' requires module(s) ${missing.join(', ')} which are not loaded. ` +
              'Stops that depend on them will still play, but their targets will not resolve.',
          );
        }

        const player = new TourPlayer(script, {
          frame: state.frame,
          bus: state.bus,
          resolveAsset: ({ asset }) => {
            const localId = asset.split(':').pop()!;
            const direct = state.scene.resolveBuildingAnchor(localId);
            if (direct) return direct;
            const entry = state.registry.resolve(asset);
            const anchor = entry?.asset.metadata?.anchor?.xyz;
            if (anchor) return anchor;
            // Foreign module with a placement but no registry anchor: aim at its placement origin.
            const parsed = asset.split(':');
            const foreign = state.registry.module(parsed[2]);
            const placement = foreign?.manifest.placement;
            if (placement) {
              return [
                placement.translation_m[0],
                placement.translation_m[1],
                placement.translation_m[2] + 60,
              ];
            }
            return null;
          },
          router: (from, to) => state.router?.route(from, to) ?? null,
          groundHeight: (x, y) => state.scene.groundHeightAt(x, y),
        });

        const scriptSpeed = script.defaults?.speed_multiplier ?? 1;
        player.setSpeed(scriptSpeed);
        setTourSpeed(scriptSpeed);
        state.player = player;
        setActiveTour(script);
        setPhotos([]);
        setAwaitingUser(false);
        changeMode('tour');
        player.play();
        setStatus('');
      } catch (tourError) {
        pushWarning(`Tour failed to load: ${String(tourError)}`);
        setStatus('');
      }
    },
    [changeMode, pushWarning],
  );

  const stopTour = useCallback(() => {
    const state = engine.current;
    if (!state) return;
    state.player?.pause();
    state.player = null;
    setActiveTour(null);
    setTourProgress(null);
    setNarration(null);
    setInstruction(null);
    setAwaitingUser(false);
    changeMode('walk');
  }, [changeMode]);

  const tourControl = useCallback(
    (action: 'toggle' | 'restart' | 'resume' | 'next' | 'previous') => {
      const player = engine.current?.player;
      if (!player) return;
      switch (action) {
        case 'toggle':
          player.toggle();
          break;
        case 'restart':
          player.restart();
          player.play();
          setPhotos([]);
          break;
        case 'resume':
          player.resume();
          setAwaitingUser(false);
          break;
        case 'next':
          player.seekToStop(Math.min((tourProgress?.stopIndex ?? 0) + 1, player.script.stops.length - 1));
          break;
        case 'previous':
          player.seekToStop(Math.max((tourProgress?.stopIndex ?? 0) - 1, 0));
          break;
      }
    },
    [tourProgress],
  );

  const changeSpeed = useCallback((multiplier: number) => {
    setTourSpeed(multiplier);
    engine.current?.player?.setSpeed(multiplier);
  }, []);

  if (error) {
    return (
      <div className="fatal">
        <h1>The district viewer could not start</h1>
        <p className="fatal-message">{error}</p>
        <p>
          Build the district payloads first, from the repository root:
        </p>
        <pre>
{`python scripts/ingest_sources.py --all
python scripts/build_boundaries.py
python scripts/build_district_assets.py
python scripts/propose_bridge_placement.py --write`}
        </pre>
      </div>
    );
  }

  return (
    <div className="app">
      <header className="topbar">
        <div className="brand">
          <strong>DUMBO District</strong>
          <span>digital twin · walkable</span>
        </div>

        <nav className="modes">
          {(['walk', 'map', 'tour'] as ViewerMode[]).map((candidate) => (
            <button
              key={candidate}
              className={mode === candidate ? 'active' : ''}
              onClick={() => (candidate === 'tour' ? undefined : changeMode(candidate))}
              disabled={candidate === 'tour' && !activeTour}
            >
              {candidate}
            </button>
          ))}
          <button className={overlay ? 'active' : ''} onClick={toggleOverlay}>
            confidence
          </button>
        </nav>

        <div className="tour-launcher">
          {activeTour ? (
            <button onClick={stopTour}>exit tour</button>
          ) : (
            tours.map((tour) => (
              <button key={tour.id} onClick={() => void startTour(tour)}>
                ▶ {tour.title}
              </button>
            ))
          )}
        </div>
      </header>

      <main className="stage">
        <canvas
          ref={canvasRef}
          onClick={handleCanvasClick}
          onDoubleClick={handleCanvasDoubleClick}
          onTouchEnd={handleCanvasTouchEnd}
        />

        {goToFlash && Date.now() - goToFlash.at < 700 && (
          <span className="goto-flash" style={{ left: goToFlash.x, top: goToFlash.y }} />
        )}

        {mode === 'map' && engine.current && mapCamera && (
          <MapView
            tileIndex={engine.current.district.tileIndex!}
            frame={engine.current.frame}
            camera={mapCamera}
            position={diagnostics?.position ?? [0, 0, 0]}
            heading={diagnostics?.heading ?? 0}
            tour={activeTour}
            progressStopIndex={tourProgress?.stopIndex ?? null}
            basemap={basemap}
            onWarning={pushWarning}
            onPickPosition={handleMapPick}
          />
        )}

        {status && <div className="status">{status}</div>}

        <Hud
          diagnostics={diagnostics}
          instruction={instruction}
          narration={narration}
          ready={ready}
        />

        {activeTour && (
          <TourPanel
            tour={activeTour}
            progress={tourProgress}
            speed={tourSpeed}
            awaitingUser={awaitingUser}
            onControl={tourControl}
            onSpeed={changeSpeed}
          />
        )}

        {photos.length > 0 && <PhotoStrip photos={photos} />}
      </main>

      <aside className="right">
        <MetadataPanel
          metadata={selected}
          evidence={selectedEvidence}
          onClose={() => {
            setSelected(null);
            setSelectedEvidence(null);
            engine.current?.scene.setHighlight(null);
          }}
        />
        {Object.keys(notices).length > 0 && (
          <section className="warnings">
            <h3>Integration notices</h3>
            <ul>
              {Object.entries(notices).map(([code, notice]) => (
                <li key={code}>
                  {notice.message}
                  {notice.count > 1 && <span className="muted"> ×{notice.count}</span>}
                  <button
                    className="icon-button notice-dismiss"
                    onClick={() => clearNotice(code)}
                    title="Dismiss"
                  >
                    ✕
                  </button>
                </li>
              ))}
            </ul>
          </section>
        )}
        <footer className="attribution">
          {attributions.map((line) => (
            <div key={line}>{line}</div>
          ))}
        </footer>
      </aside>
    </div>
  );
}
