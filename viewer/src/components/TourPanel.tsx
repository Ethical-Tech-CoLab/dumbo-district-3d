import type { TourScript } from '@d3d/contracts';
import type { KernelEvents } from '@d3d/viewer-kernel';
import CollapsiblePanel from './CollapsiblePanel';

interface Props {
  tour: TourScript;
  progress: KernelEvents['tour:progress'] | null;
  speed: number;
  awaitingUser: boolean;
  onControl: (action: 'toggle' | 'restart' | 'resume' | 'next' | 'previous') => void;
  onSpeed: (multiplier: number) => void;
}

function clock(seconds: number): string {
  const total = Math.max(0, Math.round(seconds));
  return `${Math.floor(total / 60)}:${String(total % 60).padStart(2, '0')}`;
}

export default function TourPanel({
  tour,
  progress,
  speed,
  awaitingUser,
  onControl,
  onSpeed,
}: Props) {
  const ratio = progress && progress.totalS > 0 ? progress.elapsedS / progress.totalS : 0;

  // The summary row has to carry enough that a collapsed tour is still followable: where the party
  // is, what is happening, and how far through it is.
  const summary = (
    <div className="tour-summary">
      <span className="tour-summary-title">{tour.title}</span>
      <span className="muted small tour-summary-status">
        {progress
          ? progress.phase === 'travelling'
            ? `→ ${progress.nextStopName ?? 'next stop'} · ${Math.round(progress.distanceRemainingM)} m`
            : progress.phase === 'dwelling'
              ? `at ${progress.stopName}`
              : progress.phase
          : 'ready'}
      </span>
      <span className="muted small">
        {progress ? `${clock(progress.elapsedS)} / ${clock(progress.totalS)}` : ''}
      </span>
    </div>
  );

  return (
    <CollapsiblePanel storageKey="tour" className="tour-panel" summary={summary}>
      <div className="tour-progress">
        <div className="tour-progress-fill" style={{ width: `${Math.min(100, ratio * 100)}%` }} />
      </div>

      <ol className="tour-stops">
        {tour.stops.map((stop, index) => (
          <li
            key={stop.stop_id}
            className={
              progress?.nextStopIndex === index
                ? 'next'
                : progress?.stopIndex === index
                  ? 'current'
                  : progress && index < progress.stopIndex
                    ? 'done'
                    : ''
            }
          >
            <span className="stop-index">{String.fromCharCode(65 + index)}</span>
            <span className="stop-name">{stop.name}</span>
            {stop.dwell_s ? <span className="muted small">{stop.dwell_s}s</span> : null}
          </li>
        ))}
      </ol>

      <div className="tour-controls">
        <button onClick={() => onControl('previous')} title="Previous stop">◀</button>
        <button onClick={() => onControl('toggle')}>play / pause</button>
        <button onClick={() => onControl('next')} title="Next stop">▶</button>
        <button onClick={() => onControl('restart')}>restart</button>
        <label className="speed">
          speed
          <select value={speed} onChange={(event) => onSpeed(Number(event.target.value))}>
            {[1, 2, 4, 8, 16].map((value) => (
              <option key={value} value={value}>
                {value}×
              </option>
            ))}
          </select>
        </label>
      </div>

      {awaitingUser && (
        <button className="await" onClick={() => onControl('resume')}>
          The tour is waiting for you — continue
        </button>
      )}
    </CollapsiblePanel>
  );
}
