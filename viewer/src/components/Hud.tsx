import type { Diagnostics } from '../App';

interface Props {
  diagnostics: Diagnostics | null;
  instruction: string | null;
  narration: string | null;
  ready: boolean;
}

export default function Hud({ diagnostics, instruction, narration, ready }: Props) {
  return (
    <>
      {instruction && <div className="instruction">{instruction}</div>}
      {narration && <div className="narration">{narration}</div>}

      {ready && !instruction && !narration && (
        <div className="help muted small">
          drag to move · right-drag to look · scroll to zoom ·{' '}
          <kbd>W</kbd><kbd>A</kbd><kbd>S</kbd><kbd>D</kbd> to walk · <kbd>shift</kbd> to hurry ·
          double-click to go there
        </div>
      )}

      {diagnostics && (
        <div className="diagnostics mono small">
          <span>{diagnostics.fps} fps</span>
          <span>{diagnostics.residentTiles} tiles</span>
          <span>LOD {diagnostics.levels.join(',') || '–'}</span>
          <span>
            mode {diagnostics.mode} · budget {diagnostics.budgetPx}px
          </span>
          <span>
            {diagnostics.position[0].toFixed(0)}, {diagnostics.position[1].toFixed(0)} m ·{' '}
            {diagnostics.heading.toFixed(0)}°
          </span>
        </div>
      )}
    </>
  );
}
