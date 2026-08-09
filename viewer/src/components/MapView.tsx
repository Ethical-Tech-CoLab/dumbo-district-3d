import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import type { BasemapLayer, TileIndex, TourScript } from '@d3d/contracts';
import {
  BasemapController,
  Frame,
  MapCamera,
  toSceneVec,
  type TileQuad,
} from '@d3d/viewer-kernel';

interface Props {
  tileIndex: TileIndex;
  frame: Frame;
  camera: MapCamera;
  position: [number, number, number];
  heading: number;
  tour: TourScript | null;
  progressStopIndex: number | null;
  basemap: BasemapController | null;
  onWarning?: (message: string) => void;
  onPickPosition?: (scene: [number, number]) => void;
}

const ZONE_STROKE: Record<string, string> = {
  hero: '#d8a13b',
  walkable: '#3b7dd8',
  context: '#8a9099',
  outside: '#5a6069',
};

/**
 * Plan view.
 *
 * Three layers, deliberately distinct:
 *  1. A raster basemap, fetched through the kernel, which knows the tile protocol but no vendor.
 *  2. The district's own structure — tile grid, fidelity zones, foreign-asset corridors — drawn
 *     from the same tile index the 3D view streams from, so map and scene cannot disagree.
 *  3. The active tour route.
 *
 * The view is driven by a shared `MapCamera`, so the same camera responds to a user's wheel and to
 * a tour script that wants to open over the whole district and fly in to stop A.
 */
export default function MapView({
  tileIndex,
  frame,
  camera,
  position,
  heading,
  tour,
  progressStopIndex,
  basemap,
  onWarning,
  onPickPosition,
}: Props) {
  const containerRef = useRef<HTMLDivElement>(null);
  const svgRef = useRef<SVGSVGElement>(null);
  const [layerId, setLayerId] = useState(() => basemap?.active.layer_id ?? 'plain');
  const [size, setSize] = useState({ w: 900, h: 700 });
  const [view, setView] = useState(() => camera.current);
  const dragRef = useRef<{ x: number; y: number } | null>(null);

  const districtBounds = useMemo((): [number, number, number, number] => {
    const xs = tileIndex.tiles.flatMap((t) => [t.bbox.min[0], t.bbox.max[0]]);
    const ys = tileIndex.tiles.flatMap((t) => [t.bbox.min[1], t.bbox.max[1]]);
    return [Math.min(...xs), Math.min(...ys), Math.max(...xs), Math.max(...ys)];
  }, [tileIndex]);

  useEffect(() => {
    const element = containerRef.current;
    if (!element) return;
    const observe = () => setSize({ w: element.clientWidth || 900, h: element.clientHeight || 700 });
    observe();
    const ro = new ResizeObserver(observe);
    ro.observe(element);
    return () => ro.disconnect();
  }, []);

  // Follow the camera. It may be animating from a tour, so poll it on a frame loop while mounted.
  useEffect(() => {
    let handle = 0;
    let last = performance.now();
    const tick = () => {
      const now = performance.now();
      const dt = (now - last) / 1000;
      last = now;
      if (camera.update(dt)) setView(camera.current);
      handle = requestAnimationFrame(tick);
    };
    handle = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(handle);
  }, [camera]);

  const aspect = size.w / Math.max(size.h, 1);
  const viewport = useMemo(() => camera.viewportBounds(aspect), [camera, aspect, view]);
  const [vMinX, vMinY, vMaxX, vMaxY] = viewport;
  const viewWidth = vMaxX - vMinX;
  const viewHeight = vMaxY - vMinY;

  /** Client pixel to scene meters. */
  const toScene = useCallback(
    (clientX: number, clientY: number): [number, number] => {
      const rect = svgRef.current?.getBoundingClientRect();
      if (!rect) return [view.center[0], view.center[1]];
      const fx = (clientX - rect.left) / rect.width;
      const fy = (clientY - rect.top) / rect.height;
      return [vMinX + fx * viewWidth, vMaxY - fy * viewHeight];
    },
    [view, vMinX, vMaxY, viewWidth, viewHeight],
  );

  const metersPerPixel = viewWidth / Math.max(size.w, 1);
  const activeLayer: BasemapLayer | null = basemap?.active ?? null;

  const quads: TileQuad[] = useMemo(() => {
    if (!basemap || !activeLayer || !activeLayer.url_template) return [];
    try {
      return basemap.coverage(frame, {
        bounds: viewport,
        metersPerPixel,
        maxTiles: 96,
      });
    } catch (error) {
      onWarning?.(
        `Basemap tiles unavailable: ${error instanceof Error ? error.message : String(error)}`,
      );
      return [];
    }
  }, [basemap, activeLayer, frame, viewport, metersPerPixel, onWarning, layerId]);

  const stops = useMemo(() => {
    if (!tour) return [];
    return tour.stops.map((stop) => {
      const xyz = toSceneVec(stop.position, frame) ?? [0, 0, 0];
      return { id: stop.stop_id, name: stop.name, x: xyz[0], y: xyz[1] };
    });
  }, [tour, frame]);

  const hasBasemap = quads.length > 0;
  const gridOpacity = hasBasemap ? 0.3 : 0.62;
  // Marker sizes are in scene units, so they must shrink as the view zooms in or they swamp it.
  const markerScale = Math.max(0.25, Math.min(3, view.spanM / 1400));

  function selectLayer(next: string) {
    if (!basemap) return;
    if (basemap.select(next)) {
      setLayerId(next);
      const warning = basemap.commercialWarning();
      if (warning) onWarning?.(warning);
    }
  }

  return (
    <div className="map-view" ref={containerRef}>
      <svg
        ref={svgRef}
        viewBox={`${vMinX} ${-vMaxY} ${viewWidth} ${viewHeight}`}
        preserveAspectRatio="xMidYMid slice"
        onWheel={(event) => {
          event.preventDefault();
          const anchor = toScene(event.clientX, event.clientY);
          camera.zoomBy(Math.exp(event.deltaY * 0.0016), anchor);
          setView(camera.current);
        }}
        onPointerDown={(event) => {
          (event.target as Element).setPointerCapture?.(event.pointerId);
          dragRef.current = { x: event.clientX, y: event.clientY };
        }}
        onPointerMove={(event) => {
          const drag = dragRef.current;
          if (!drag) return;
          const rect = svgRef.current?.getBoundingClientRect();
          if (!rect) return;
          const dx = ((event.clientX - drag.x) / rect.width) * viewWidth;
          const dy = ((event.clientY - drag.y) / rect.height) * viewHeight;
          camera.panBy(-dx, dy);
          setView(camera.current);
          dragRef.current = { x: event.clientX, y: event.clientY };
        }}
        onPointerUp={() => {
          dragRef.current = null;
        }}
        onPointerCancel={() => {
          dragRef.current = null;
        }}
        onDoubleClick={(event) => {
          const scene = toScene(event.clientX, event.clientY);
          onPickPosition?.(scene);
        }}
      >
        {/* Basemap tiles, each placed by its own scene-space corners so Web Mercator imagery lines
            up with the local ENU scene without a global approximation. */}
        <g opacity={activeLayer?.opacity ?? 1}>
          {quads.map((quad) => (
            <image
              key={`${quad.z}/${quad.x}/${quad.y}`}
              href={quad.url}
              x={quad.bounds[0]}
              y={-quad.bounds[3]}
              width={quad.bounds[2] - quad.bounds[0]}
              height={quad.bounds[3] - quad.bounds[1]}
              preserveAspectRatio="none"
            />
          ))}
        </g>

        {!hasBasemap && (
          <rect x={vMinX} y={-vMaxY} width={viewWidth} height={viewHeight} fill="#11151a" />
        )}

        <g opacity={gridOpacity}>
          {tileIndex.tiles.map((tile) => (
            <rect
              key={tile.tile_id}
              x={tile.bbox.min[0]}
              y={-tile.bbox.max[1]}
              width={tile.bbox.max[0] - tile.bbox.min[0]}
              height={tile.bbox.max[1] - tile.bbox.min[1]}
              fill={tile.content.length ? ZONE_STROKE[tile.zone] : 'none'}
              fillOpacity={tile.content.length ? (hasBasemap ? 0.16 : 0.4) : 0}
              stroke={ZONE_STROKE[tile.zone]}
              strokeOpacity={0.5}
              strokeWidth={1.2 * markerScale}
            />
          ))}
        </g>

        {tileIndex.tiles
          .filter((tile) => tile.foreign_assets?.length)
          .map((tile) => (
            <rect
              key={`bridge-${tile.tile_id}`}
              x={tile.bbox.min[0]}
              y={-tile.bbox.max[1]}
              width={tile.bbox.max[0] - tile.bbox.min[0]}
              height={tile.bbox.max[1] - tile.bbox.min[1]}
              fill="none"
              stroke="#c4453c"
              strokeWidth={2.5 * markerScale}
              strokeDasharray={`${9 * markerScale} ${6 * markerScale}`}
            />
          ))}

        {stops.length > 1 && (
          <polyline
            points={stops.map((s) => `${s.x},${-s.y}`).join(' ')}
            fill="none"
            stroke="#ffd479"
            strokeWidth={4 * markerScale}
            strokeDasharray={`${12 * markerScale} ${9 * markerScale}`}
            opacity={0.9}
          />
        )}

        {stops.map((stop, index) => (
          <g key={stop.id}>
            <circle
              cx={stop.x}
              cy={-stop.y}
              r={(progressStopIndex === index ? 17 : 11) * markerScale}
              fill={progressStopIndex === index ? '#ffd479' : '#f0f3f6'}
              stroke="#11151a"
              strokeWidth={2.5 * markerScale}
            />
            <text
              x={stop.x}
              y={-stop.y + 6 * markerScale}
              textAnchor="middle"
              fontSize={16 * markerScale}
              fontWeight="700"
              fill="#11151a"
            >
              {String.fromCharCode(65 + index)}
            </text>
          </g>
        ))}

        <g
          transform={`translate(${position[0]} ${-position[1]}) rotate(${heading}) scale(${markerScale})`}
        >
          <polygon points="0,-24 14,17 0,7 -14,17" fill="#5ce1a6" stroke="#0b0e12" strokeWidth={3} />
        </g>
      </svg>

      <div className="map-controls">
        <button onClick={() => { camera.zoomBy(0.6); setView(camera.current); }} title="Zoom in">＋</button>
        <button onClick={() => { camera.zoomBy(1 / 0.6); setView(camera.current); }} title="Zoom out">－</button>
        <button
          onClick={() => { void camera.frameBounds(districtBounds, 1.05, 0.8); }}
          title="Fit the district"
        >
          ⤢
        </button>
        {stops.length > 0 && (
          <button
            onClick={() => { void camera.framePoints(stops.map((s) => [s.x, s.y]), 1.3, 0.8); }}
            title="Fit the tour"
          >
            ⌖
          </button>
        )}
      </div>

      {basemap && (
        <div className="basemap-switcher">
          {basemap.usableLayers.map((layer) => (
            <button
              key={layer.layer_id}
              className={layer.layer_id === layerId ? 'active' : ''}
              onClick={() => selectLayer(layer.layer_id)}
              title={layer.usage_policy ?? layer.label}
            >
              {layer.label}
            </button>
          ))}
        </div>
      )}

      <div className="map-legend mono small">
        <span><i style={{ background: ZONE_STROKE.hero }} /> hero (LOD0-2)</span>
        <span><i style={{ background: ZONE_STROKE.walkable }} /> walkable (LOD1-2)</span>
        <span><i style={{ background: ZONE_STROKE.context }} /> context (LOD2)</span>
        <span>
          <i style={{ background: 'transparent', border: '2px dashed #c4453c' }} /> declares Manhattan Bridge
        </span>
        <span className="muted">scroll to zoom · drag to pan · double-click to walk there</span>
      </div>

      {activeLayer && activeLayer.url_template && (
        <div className="map-attribution small">{basemap?.activeAttribution().join(' · ')}</div>
      )}
    </div>
  );
}
