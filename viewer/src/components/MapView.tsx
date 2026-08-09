import { useMemo } from 'react';
import type { TileIndex, TourScript } from '@d3d/contracts';
import { Frame, toSceneVec } from '@d3d/viewer-kernel';

interface Props {
  tileIndex: TileIndex;
  frame: Frame;
  position: [number, number, number];
  heading: number;
  tour: TourScript | null;
  progressStopIndex: number | null;
}

const ZONE_FILL: Record<string, string> = {
  hero: '#d8a13b',
  walkable: '#3b7dd8',
  context: '#3a4149',
  outside: '#23272c',
};

/**
 * Plan view over the tile grid.
 *
 * Deliberately drawn from the same tile index the 3D view streams from, rather than from a separate
 * basemap: the map and the scene cannot disagree about what exists, and the fidelity zones become
 * visible, which makes the LOD strategy something a reviewer can see rather than read about.
 */
export default function MapView({ tileIndex, frame, position, heading, tour, progressStopIndex }: Props) {
  const { viewBox, tiles } = useMemo(() => {
    const xs = tileIndex.tiles.flatMap((t) => [t.bbox.min[0], t.bbox.max[0]]);
    const ys = tileIndex.tiles.flatMap((t) => [t.bbox.min[1], t.bbox.max[1]]);
    const minX = Math.min(...xs);
    const maxX = Math.max(...xs);
    const minY = Math.min(...ys);
    const maxY = Math.max(...ys);
    return {
      viewBox: `${minX} ${-maxY} ${maxX - minX} ${maxY - minY}`,
      tiles: tileIndex.tiles,
    };
  }, [tileIndex]);

  const stops = useMemo(() => {
    if (!tour) return [];
    return tour.stops.map((stop) => {
      const xyz = toSceneVec(stop.position, frame) ?? [0, 0, 0];
      return { id: stop.stop_id, name: stop.name, x: xyz[0], y: xyz[1] };
    });
  }, [tour, frame]);

  return (
    <div className="map-view">
      {/* SVG y is flipped so that scene north is up. */}
      <svg viewBox={viewBox} preserveAspectRatio="xMidYMid meet">
        {tiles.map((tile) => (
          <rect
            key={tile.tile_id}
            x={tile.bbox.min[0]}
            y={-tile.bbox.max[1]}
            width={tile.bbox.max[0] - tile.bbox.min[0]}
            height={tile.bbox.max[1] - tile.bbox.min[1]}
            fill={ZONE_FILL[tile.zone]}
            fillOpacity={tile.content.length ? 0.55 : 0.16}
            stroke="#11151a"
            strokeWidth={1.5}
          />
        ))}

        {tiles
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
            opacity={0.8}
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

      <div className="map-legend mono small">
        <span><i style={{ background: ZONE_FILL.hero }} /> hero (LOD0-2)</span>
        <span><i style={{ background: ZONE_FILL.walkable }} /> walkable (LOD1-2)</span>
        <span><i style={{ background: ZONE_FILL.context }} /> context (LOD2)</span>
        <span><i style={{ background: 'transparent', border: '2px dashed #c4453c' }} /> declares Manhattan Bridge</span>
      </div>
    </div>
  );
}
