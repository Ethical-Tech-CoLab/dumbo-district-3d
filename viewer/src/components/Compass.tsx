interface Props {
  /** Where the walker is facing, degrees clockwise from true north. */
  headingDeg: number;
  /** Recentre the map on the walker. */
  onRecentre?: () => void;
}

const CARDINALS: Array<[string, number]> = [
  ['N', 0],
  ['E', 90],
  ['S', 180],
  ['W', 270],
];

/**
 * Compass rose for the map view.
 *
 * The map is drawn north-up and does not rotate, so the ring is fixed and the needle turns. That is
 * the honest arrangement: rotating the map under a fixed needle would be prettier but would mean
 * the district's streets no longer line up with how every other map of Brooklyn is drawn.
 *
 * The needle carries the *walker's* heading rather than a map bearing, which is the question someone
 * in map view actually has — not "which way is north", which never changes here, but "which way am I
 * pointing, and where will I be looking when I switch back to walking".
 */
export default function Compass({ headingDeg, onRecentre }: Props) {
  const heading = ((headingDeg % 360) + 360) % 360;
  const label = bearingName(heading);

  return (
    <button
      className="compass"
      onClick={onRecentre}
      title={`Facing ${label} ${Math.round(heading)}° · click to centre on the walker`}
      aria-label={`Compass, facing ${label} ${Math.round(heading)} degrees`}
    >
      <svg viewBox="-50 -50 100 100" aria-hidden="true">
        <circle className="compass-dial" cx="0" cy="0" r="40" />
        {CARDINALS.map(([name, angle]) => (
          <text
            key={name}
            className={name === 'N' ? 'compass-north' : 'compass-cardinal'}
            x={0}
            y={-31}
            transform={`rotate(${angle}) `}
            textAnchor="middle"
            dominantBaseline="middle"
          >
            {name}
          </text>
        ))}
        {[45, 135, 225, 315].map((angle) => (
          <line key={angle} className="compass-tick" x1="0" y1="-40" x2="0" y2="-35"
                transform={`rotate(${angle})`} />
        ))}
        {/* The needle: red half points where the walker faces. */}
        <g transform={`rotate(${heading})`}>
          <polygon className="compass-needle" points="0,-26 6,6 0,0 -6,6" />
          <polygon className="compass-needle-tail" points="0,26 6,-6 0,0 -6,-6" />
        </g>
        <circle className="compass-hub" cx="0" cy="0" r="3" />
      </svg>
      <span className="compass-label mono">{label} {Math.round(heading)}°</span>
    </button>
  );
}

/** Sixteen-point compass name, which reads far better than a bare number at a glance. */
function bearingName(deg: number): string {
  const points = ['N', 'NNE', 'NE', 'ENE', 'E', 'ESE', 'SE', 'SSE',
                  'S', 'SSW', 'SW', 'WSW', 'W', 'WNW', 'NW', 'NNW'];
  return points[Math.round(deg / 22.5) % 16];
}
