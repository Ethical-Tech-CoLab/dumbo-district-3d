/**
 * First-person walk controls: pointer lock look, WASD movement, shift to hurry.
 *
 * Speed is clamped to DCTL-054 so a user cannot outrun the streaming manager, which is a real
 * failure mode in a browser twin and a cheap one to design out.
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
  walkSpeed: number;
  onPointerLockChange?: (locked: boolean) => void;
}

export class WalkControls {
  state: WalkState;
  enabled = true;

  private readonly element: HTMLElement;
  private readonly options: WalkControlsOptions;
  private readonly keys = new Set<string>();
  private locked = false;
  private disposers: Array<() => void> = [];

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
      this.state.headingDeg = (this.state.headingDeg + event.movementX * 0.12 + 360) % 360;
      this.state.pitchDeg = Math.max(
        -85,
        Math.min(85, this.state.pitchDeg - event.movementY * 0.12),
      );
    };

    const onPointerLockChange = () => {
      this.locked = document.pointerLockElement === this.element;
      this.options.onPointerLockChange?.(this.locked);
      if (!this.locked) this.keys.clear();
    };

    window.addEventListener('keydown', onKeyDown);
    window.addEventListener('keyup', onKeyUp);
    document.addEventListener('mousemove', onMouseMove);
    document.addEventListener('pointerlockchange', onPointerLockChange);

    this.disposers = [
      () => window.removeEventListener('keydown', onKeyDown),
      () => window.removeEventListener('keyup', onKeyUp),
      () => document.removeEventListener('mousemove', onMouseMove),
      () => document.removeEventListener('pointerlockchange', onPointerLockChange),
    ];
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

    // Look-around with arrow keys when the pointer is not locked, so the viewer is usable without
    // capturing the mouse.
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
    const speed = Math.min(
      this.options.maxSpeed,
      this.options.walkSpeed * (hurrying ? 2.2 : 1),
    );

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
  }
}
