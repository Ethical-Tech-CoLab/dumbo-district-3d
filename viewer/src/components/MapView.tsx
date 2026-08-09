import { useEffect, useMemo, useRef, useState } from 'react';
import type { BasemapLayer, TileIndex, TourScript } from '@d3d/contracts';
import { BasemapController, Frame, toSceneVec, type TileQuad } from '@d3d/viewer-kernel';

interface Props {
  tileIndex: TileIndex;
  frame: Frame;
  position: [number, number, number];
  heading: number;
  tour: TourScript | null;
  progressStopIndex: number | null;
  basemap: BasemapController | null;
  onWarning?: (message: string) => void;
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
 * Two layers of information, deliberately kept distinct:
 *
 *  1. A raster basemap behind, in the terrain / street / satellite idiom users already know from
 *     Google, Bing and Apple Maps. Fetched through the shared kernel, which knows the tile protocol
 *     but no vendor.
 *  2. The district's own structure on top — tile grid, fidelity zones, foreign-asset corridors,
 *     tour route — drawn from the same tile index the 3D view streams from, so the map and the
 *     scene cannot disagree about what exists.
 *
 * Overlay opacity drops when a basemap is active so the imagery stays readable underneath.
 */
export default function MapView({
  tileIndex,
  frame,
  position,
  heading,
  tour,
  progressStopIndex,
  basemap,
  onWarning,
}: Props) {
  const containerRef = useRef<HTMLDivElement>(null);
  const [layerId, setLayerId] = useState(() => basemap?.active.layer_id ?? 'plain');
  const [size, setSize] = useState({ w: 800, h: 600 });

  const extent = useMemo(() => {
    const xs = tileIndex.tiles.flatMap((t) => [t.bbox.min[0], t.bbox.max[0]]);
    const ys = tileIndex.tiles.flatMap((t) => [t.bbox.min[1], t.bbox.max[1]]);
    return {
      minX: Math.min(...xs),
      maxX: Math.max(...xs),
      minY: Math.min(...ys),
      maxY: Math.max(...ys),
    };
  }, [tileIndex]);

  useEffect(() => {
    const element = containerRef.current;
    if (!element) return;
    const observe = () =>
      setSize({ w: element.clientWidth || 800, h: element.clientHeight || 600 });
    observe();
    const ro = new ResizeObserver(observe);
    ro.observe(element);
    return () => ro.disconnect();
  }, []);

  const width = extent.maxX - extent.minX;
  const height = extent.maxY - extent.minY;

  // Ground resolution the SVG is actually displayed at, so basemap zoom matches what the user can
  // resolve rather than a fixed level.
  const mpp = useMemo(() => {
    const scale = Math.min(size.w / width, size.h / height);
    return scale > 0 ? 1 / scale : 1;
  }, [size, width, height]);

  const activeLayer: BasemapLayer | null = basemap?.active ?? null;

  const quads: TileQuad[] = useMemo(() => {
    if (!basemap || !activeLayer || !activeLayer.url_template) return [];
    try {
      return basemap.coverage(frame, {
        bounds: [extent.minX, extent.minY, extent.maxX, extent.maxY],
        metersPerPixel: mpp,
        maxTiles: 80,
      });
    } catch (error) {
      onWarning?.(
        `Basemap tiles unavailable: ${error instanceof Error ? error.message : String(error)}`,
      );
      return [];
    }
    // layerId participates so switching layers recomputes the coverage.
  }, [basemap, activeLayer, frame, extent, mpp, onWarning, layerId]);

  const stops = useMemo(() => {
    if (!tour) return [];
    return tour.stops.map((stop) => {
      const xyz = toSceneVec(stop.position, frame) ?? [0, 0, 0];
      return { id: stop.stop_id, name: stop.name, x: xyz[0], y: xyz[1] };
    });
  }, [tour, frame]);

  const hasBasemap = quads.length > 0;
  const gridOpacity = hasBasemap ? 0.3 : 0.62;

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
        viewBox={`${extent.minX} ${-extent.maxY} ${width} ${height}`}
        preserveAspectRatio="xMidYMid meet"
      >
        {/* Basemap tiles. Each is placed by its own scene-space corners, so Web Mercator imagery
            lines up with the local ENU scene without a global approximation. */}
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
          <rect x={extent.minX} y={-extent.maxY} width={width} height={height} fill="#11151a" />
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
              strokeWidth={1.5}
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
              strokeWidth={3}
              strokeDasharray="10 6"
            />
          ))}

        {stops.length > 1 && (
          <polyline
            points={stops.map((s) => `${s.x},${-s.y}`).join(' ')}
            fill="none"
            stroke="#ffd479"
            strokeWidth={5}
            strokeDasharray="14 10"
            opacity={0.9}
          />
        )}

        {stops.map((stop, index) => (
          <g key={stop.id}>
            <circle
              cx={stop.x}
              cy={-stop.y}
              r={progressStopIndex === index ? 20 : 13}
              fill={progressStopIndex === index ? '#ffd479' : '#f0f3f6'}
              stroke="#11151a"
              strokeWidth={3}
            />
            <text
              x={stop.x}
              y={-stop.y + 7}
              textAnchor="middle"
              fontSize={18}
              fontWeight="700"
              fill="#11151a"
            >
              {String.fromCharCode(65 + index)}
            </text>
          </g>
        ))}

        <g transform={`translate(${position[0]} ${-position[1]}) rotate(${heading})`}>
          <polygon points="0,-26 15,18 0,8 -15,18" fill="#5ce1a6" stroke="#0b0e12" strokeWidth={3} />
        </g>
      </svg>

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
      </div>

      {activeLayer && activeLayer.url_template && (
        <div className="map-attribution small">{basemap?.activeAttribution().join(' · ')}</div>
      )}
    </div>
  );
}
