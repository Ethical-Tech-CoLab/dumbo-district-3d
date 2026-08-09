/**
 * Navigation for walk mode: keyboard walking plus direct manipulation with mouse or touch.
 *
 * The original scheme was pointer-lock-first, which is right for a game and wrong for a twin that
 * people open on a phone or glance at on a laptop. Direct manipulation is now primary:
 *
 *   left drag / one finger      grab the ground and slide it, the way a map behaves
 *   right drag / three fingers  orbit the view about the walker
 *   wheel / two-finger pinch    move along the view direction
 *   two-finger drag             pan
 *   WASD, shift to hurry        still there, and still the fastest way to cover ground
 *
 * Pointer lock remains available for anyone who wants true first-person look, but nothing depends
 * on it any more, so a single click is free to mean "select this building" and nothing else.
 *
 * Speed is clamped: free walking to DCTL-054, sprinting to DCTL-055. The clamp exists so a user
 * cannot outrun the streaming manager, which is a real failure mode in a browser twin.
 */

export interface WalkState {
  /** Scene ENU position of the feet. */
  position: [number, number, number];
  headingDeg: number;
  pitchDeg: number;
  moving: boolean;
}

export interface WalkControlsOptions {
  maxSpeed: number;
  /** Cap while shift is held. Deliberately higher than maxSpeed; see DCTL-055. */
  sprintSpeed?: number;
  walkSpeed: number;
  onPointerLockChange?: (locked: boolean) => void;
}

interface PointerSample {
  x: number;
  y: number;
  type: string;
  button: number;
}

/** Metres of ground travel per pixel of drag. Tuned for an eye at ~1.7 m looking slightly down. */
const PAN_METRES_PER_PIXEL = 0.06;
const ORBIT_DEG_PER_PIXEL = 0.22;
const DOLLY_METRES_PER_NOTCH = 6;
/** Pixels a pointer may wander before it counts as a drag rather than a click. */
const DRAG_SLOP_PX = 5;
/** Multiplier applied to the walking pace while shift is held. */
const SPRINT_MULTIPLIER = 6.6;

export class WalkControls {
  state: WalkState;
  enabled = true;

  private readonly element: HTMLElement;
  private readonly options: WalkControlsOptions;
  private readonly keys = new Set<string>();
  private locked = false;
  private disposers: Array<() => void> = [];

  /** Live pointers, keyed by pointerId, so mouse and touch share one code path. */
  private readonly pointers = new Map<number, PointerSample>();
  private gestureMoved = false;
  private draggedRecently = 0;
  private lastCentroid: { x: number; y: number } | null = null;
  private lastSpread = 0;
  private lastTwistDeg = 0;

  constructor(element: HTMLElement, start: WalkState, options: WalkControlsOptions) {
    this.element = element;
    this.state = { ...start };
    this.options = options;

    const onKeyDown = (event: KeyboardEvent) => {
      this.keys.add(event.code);
      if (event.code === 'Space') event.preventDefault();
    };
    const onKeyUp = (event: KeyboardEvent) => this.keys.delete(event.code);

    const onMouseMove = (event: MouseEvent) => {
      if (!this.locked || !this.enabled) return;
      this.orbit(event.movementX, event.movementY);
    };

    const onPointerLockChange = () => {
      this.locked = document.pointerLockElement === this.element;
      this.options.onPointerLockChange?.(this.locked);
      if (!this.locked) this.keys.clear();
    };

    // ------------------------------------------------------------- gestures

    const onPointerDown = (event: PointerEvent) => {
      if (!this.enabled || this.locked) return;
      this.pointers.set(event.pointerId, {
        x: event.clientX, y: event.clientY, type: event.pointerType, button: event.button,
      });
      try {
        element.setPointerCapture(event.pointerId);
      } catch {
        /* capture is a convenience; losing it only means a drag ends at the window edge */
      }
      this.resetGestureFrame();
      this.gestureMoved = false;
    };

    const onPointerMove = (event: PointerEvent) => {
      if (!this.enabled || this.locked) return;
      const existing = this.pointers.get(event.pointerId);
      if (!existing) return;
      existing.x = event.clientX;
      existing.y = event.clientY;
      this.applyGesture();
    };

    const endPointer = (event: PointerEvent) => {
      if (!this.pointers.delete(event.pointerId)) return;
      try {
        element.releasePointerCapture(event.pointerId);
      } catch {
        /* already released */
      }
      this.resetGestureFrame();
      if (this.pointers.size === 0 && this.gestureMoved) {
        // Remember that a drag just ended. The click event that follows is the tail of that drag,
        // not an attempt to select, and the shell checks this before picking.
        this.draggedRecently = Date.now();
      }
    };

    // Right-drag orbits, so the browser context menu has to stay out of the way.
    const onContextMenu = (event: Event) => {
      if (this.enabled) event.preventDefault();
    };

    const onWheel = (event: WheelEvent) => {
      if (!this.enabled || this.locked) return;
      event.preventDefault();
      this.dolly(-Math.sign(event.deltaY) * DOLLY_METRES_PER_NOTCH);
    };

    window.addEventListener('keydown', onKeyDown);
    window.addEventListener('keyup', onKeyUp);
    document.addEventListener('mousemove', onMouseMove);
    document.addEventListener('pointerlockchange', onPointerLockChange);
    element.addEventListener('pointerdown', onPointerDown);
    element.addEventListener('pointermove', onPointerMove);
    element.addEventListener('pointerup', endPointer);
    element.addEventListener('pointercancel', endPointer);
    element.addEventListener('contextmenu', onContextMenu);
    element.addEventListener('wheel', onWheel, { passive: false });

    this.disposers = [
      () => window.removeEventListener('keydown', onKeyDown),
      () => window.removeEventListener('keyup', onKeyUp),
      () => document.removeEventListener('mousemove', onMouseMove),
      () => document.removeEventListener('pointerlockchange', onPointerLockChange),
      () => element.removeEventListener('pointerdown', onPointerDown),
      () => element.removeEventListener('pointermove', onPointerMove),
      () => element.removeEventListener('pointerup', endPointer),
      () => element.removeEventListener('pointercancel', endPointer),
      () => element.removeEventListener('contextmenu', onContextMenu),
      () => element.removeEventListener('wheel', onWheel),
    ];
  }

  // ----------------------------------------------------------------- gesture

  private resetGestureFrame(): void {
    this.lastCentroid = this.centroid();
    this.lastSpread = this.spread();
    this.lastTwistDeg = this.twistDeg();
  }

  private centroid(): { x: number; y: number } | null {
    if (this.pointers.size === 0) return null;
    let x = 0;
    let y = 0;
    for (const p of this.pointers.values()) {
      x += p.x;
      y += p.y;
    }
    return { x: x / this.pointers.size, y: y / this.pointers.size };
  }

  /** Mean distance of pointers from their centroid: a pinch measure that works for 2 or 3 fingers. */
  private spread(): number {
    const centre = this.centroid();
    if (!centre || this.pointers.size < 2) return 0;
    let total = 0;
    for (const p of this.pointers.values()) total += Math.hypot(p.x - centre.x, p.y - centre.y);
    return total / this.pointers.size;
  }

  /** Angle of the first pointer about the centroid, in degrees. Drives the three-finger twist. */
  private twistDeg(): number {
    const centre = this.centroid();
    if (!centre || this.pointers.size < 2) return 0;
    const first = this.pointers.values().next().value as PointerSample | undefined;
    if (!first) return 0;
    return (Math.atan2(first.y - centre.y, first.x - centre.x) * 180) / Math.PI;
  }

  /**
   * Turn the current pointer set into camera motion.
   *
   * One pointer pans, unless it is the right mouse button, which orbits. Two pointers pinch to move
   * along the view and drag to pan. Three pointers twist to turn, which is the touch equivalent of
   * a right-drag and leaves two-finger pan free to do what people expect it to do.
   */
  private applyGesture(): void {
    const centre = this.centroid();
    if (!centre) return;
    const previous = this.lastCentroid;
    const dx = previous ? centre.x - previous.x : 0;
    const dy = previous ? centre.y - previous.y : 0;
    if (Math.abs(dx) > DRAG_SLOP_PX || Math.abs(dy) > DRAG_SLOP_PX) this.gestureMoved = true;

    const count = this.pointers.size;
    const first = this.pointers.values().next().value as PointerSample | undefined;

    if (count === 1) {
      if (first && first.type === 'mouse' && first.button === 2) {
        this.orbit(dx, dy);
      } else {
        this.pan(dx, dy);
      }
    } else if (count === 2) {
      const spread = this.spread();
      if (this.lastSpread > 0 && spread > 0) {
        this.dolly((spread - this.lastSpread) * 0.08);
        if (Math.abs(spread - this.lastSpread) > 1) this.gestureMoved = true;
      }
      this.pan(dx, dy);
    } else {
      const twist = this.twistDeg();
      let delta = twist - this.lastTwistDeg;
      if (delta > 180) delta -= 360;
      if (delta < -180) delta += 360;
      this.state.headingDeg = (this.state.headingDeg - delta + 360) % 360;
      this.state.pitchDeg = clamp(this.state.pitchDeg - dy * ORBIT_DEG_PER_PIXEL, -85, 85);
      this.gestureMoved = true;
    }

    this.resetGestureFrame();
  }

  /** Slide the world under the walker. Dragging right moves the view left, as a map does. */
  private pan(dx: number, dy: number): void {
    if (dx === 0 && dy === 0) return;
    const heading = (this.state.headingDeg * Math.PI) / 180;
    const fx = Math.sin(heading);
    const fy = Math.cos(heading);
    const rx = Math.cos(heading);
    const ry = -Math.sin(heading);
    const right = -dx * PAN_METRES_PER_PIXEL;
    const forward = -dy * PAN_METRES_PER_PIXEL;
    this.state.position[0] += rx * right + fx * forward;
    this.state.position[1] += ry * right + fy * forward;
  }

  private orbit(dx: number, dy: number): void {
    this.state.headingDeg = (this.state.headingDeg + dx * ORBIT_DEG_PER_PIXEL + 360) % 360;
    this.state.pitchDeg = clamp(this.state.pitchDeg - dy * ORBIT_DEG_PER_PIXEL, -85, 85);
  }

  /** Move along the current heading. Used by the wheel and by pinch. */
  private dolly(metres: number): void {
    if (!metres) return;
    const heading = (this.state.headingDeg * Math.PI) / 180;
    this.state.position[0] += Math.sin(heading) * metres;
    this.state.position[1] += Math.cos(heading) * metres;
  }

  /**
   * True when the click now arriving is the tail of a drag rather than a selection.
   *
   * A drag almost always ends with a click event on the same element, and without this every pan
   * would select whatever building happened to be under the finger when it lifted.
   */
  consumeDragClick(): boolean {
    if (Date.now() - this.draggedRecently < 250) {
      this.draggedRecently = 0;
      return true;
    }
    return false;
  }

  requestLock(): void {
    void this.element.requestPointerLock();
  }

  get isLocked(): boolean {
    return this.locked;
  }

  /** Advance by `dt` seconds. Returns true when the party actually moved. */
  update(dt: number): boolean {
    if (!this.enabled) return false;

    let forward = 0;
    let strafe = 0;
    if (this.keys.has('KeyW') || this.keys.has('ArrowUp')) forward += 1;
    if (this.keys.has('KeyS') || this.keys.has('ArrowDown')) forward -= 1;
    if (this.keys.has('KeyD') || this.keys.has('ArrowRight')) strafe += 1;
    if (this.keys.has('KeyA') || this.keys.has('ArrowLeft')) strafe -= 1;

    // Look-around with Q and E when the pointer is not locked, so the viewer is usable from the
    // keyboard alone.
    if (!this.locked) {
      if (this.keys.has('KeyQ')) this.state.headingDeg = (this.state.headingDeg - 90 * dt + 360) % 360;
      if (this.keys.has('KeyE')) this.state.headingDeg = (this.state.headingDeg + 90 * dt) % 360;
    }

    if (forward === 0 && strafe === 0) {
      this.state.moving = false;
      return false;
    }

    const magnitude = Math.hypot(forward, strafe) || 1;
    const hurrying = this.keys.has('ShiftLeft') || this.keys.has('ShiftRight');
    const ceiling = hurrying
      ? (this.options.sprintSpeed ?? this.options.maxSpeed)
      : this.options.maxSpeed;
    const speed = Math.min(ceiling, this.options.walkSpeed * (hurrying ? SPRINT_MULTIPLIER : 1));

    const heading = (this.state.headingDeg * Math.PI) / 180;
    // Scene forward for a compass heading: (sin, cos, 0). Right is that rotated -90 degrees.
    const fx = Math.sin(heading);
    const fy = Math.cos(heading);
    const rx = Math.cos(heading);
    const ry = -Math.sin(heading);

    const step = (speed * dt) / magnitude;
    this.state.position[0] += (fx * forward + rx * strafe) * step;
    this.state.position[1] += (fy * forward + ry * strafe) * step;
    this.state.moving = true;
    return true;
  }

  teleport(position: [number, number, number], headingDeg?: number, pitchDeg?: number): void {
    this.state.position = [...position];
    if (headingDeg !== undefined) this.state.headingDeg = headingDeg;
    if (pitchDeg !== undefined) this.state.pitchDeg = pitchDeg;
  }

  dispose(): void {
    for (const dispose of this.disposers) dispose();
    this.disposers = [];
    this.pointers.clear();
  }
}

function clamp(value: number, low: number, high: number): number {
  return Math.max(low, Math.min(high, value));
}
