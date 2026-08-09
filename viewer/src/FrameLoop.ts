/**
 * Frame loop with a hidden-document fallback.
 *
 * requestAnimationFrame is the right driver for an interactive viewer, but browsers suspend it
 * entirely while a document is hidden. That is correct for a game and wrong for this viewer, which
 * also has to run in contexts where nothing is on screen: automated screenshot capture, CI smoke
 * tests, and rendering a scripted tour to disk.
 *
 * So: use rAF when it is actually ticking, and fall back to a timer when it is not. The fallback
 * runs at a lower rate because nobody is watching, and it hands back to rAF the moment the document
 * becomes visible again.
 */

export type FrameCallback = (deltaSeconds: number) => void;

const FALLBACK_HZ = 30;
const RAF_STALL_MS = 400;

export class FrameLoop {
  private readonly callback: FrameCallback;
  private rafHandle = 0;
  private timerHandle = 0;
  private watchdog = 0;
  private lastTick = 0;
  private running = false;
  private usingFallback = false;

  constructor(callback: FrameCallback) {
    this.callback = callback;
  }

  start(): void {
    if (this.running) return;
    this.running = true;
    this.lastTick = performance.now();
    document.addEventListener('visibilitychange', this.onVisibilityChange);
    this.useRaf();
  }

  stop(): void {
    this.running = false;
    cancelAnimationFrame(this.rafHandle);
    window.clearTimeout(this.timerHandle);
    window.clearTimeout(this.watchdog);
    document.removeEventListener('visibilitychange', this.onVisibilityChange);
  }

  get isFallback(): boolean {
    return this.usingFallback;
  }

  private onVisibilityChange = (): void => {
    if (!this.running) return;
    if (!document.hidden && this.usingFallback) {
      window.clearTimeout(this.timerHandle);
      this.usingFallback = false;
      this.useRaf();
    }
  };

  private tick(): void {
    const now = performance.now();
    const delta = Math.min((now - this.lastTick) / 1000, 0.25);
    this.lastTick = now;
    this.callback(delta);
  }

  private useRaf(): void {
    if (!this.running) return;

    // If rAF does not fire within the stall window, the document is suspended: switch to the timer.
    window.clearTimeout(this.watchdog);
    this.watchdog = window.setTimeout(() => {
      if (!this.running || this.usingFallback) return;
      cancelAnimationFrame(this.rafHandle);
      this.usingFallback = true;
      this.useFallback();
    }, RAF_STALL_MS);

    this.rafHandle = requestAnimationFrame(() => {
      window.clearTimeout(this.watchdog);
      if (!this.running || this.usingFallback) return;
      this.tick();
      this.useRaf();
    });
  }

  private useFallback(): void {
    if (!this.running) return;
    this.timerHandle = window.setTimeout(() => {
      if (!this.running) return;
      this.tick();
      if (!document.hidden) {
        this.usingFallback = false;
        this.useRaf();
      } else {
        this.useFallback();
      }
    }, 1000 / FALLBACK_HZ);
  }
}
