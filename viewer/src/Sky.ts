/**
 * Sun position and sky colour for a place and a moment.
 *
 * Two things live here that were previously guessed inside the scene.
 *
 * **Where the sun is.** The old rig swept a light along a fixed arc from 06:00 to 18:00, which is
 * not a sky: it put the sun in the same place in December as in June, at a peak elevation of 63°
 * regardless of season, and it had no notion of azimuth at all. DUMBO's real sun runs from 72.7° at
 * midsummer noon to 25.8° at midwinter noon, and the azimuth matters more than the altitude for a
 * street grid -- Washington Street runs north-west, so it only fills with light for part of the day.
 *
 * **How bright the sky is.** The old sky was a fixed hue at lightness 0.35-0.70, which is a dull
 * blue-grey at every hour. A real clear zenith sits near 0.75-0.85 with the horizon much paler, and
 * that gradient is most of what makes a rendered sky look like sky rather than like a background
 * colour.
 *
 * Low-precision NOAA formulae, good to about a tenth of a degree. Far better than a renderer needs,
 * and cheap enough to call per frame.
 */

export interface SunPosition {
  /** Degrees above the horizon. Negative below it. */
  altitudeDeg: number;
  /** Degrees clockwise from true north. */
  azimuthDeg: number;
}

/** Solar altitude and azimuth for a geodetic position and an instant. */
export function sunPosition(lat: number, lon: number, when: Date): SunPosition {
  const rad = Math.PI / 180;
  const days = (when.getTime() - Date.UTC(2000, 0, 1, 12)) / 86400000;

  const meanLon = ((280.46 + 0.9856474 * days) % 360) * rad;
  const meanAnomaly = ((357.528 + 0.9856003 * days) % 360) * rad;
  const ecliptic =
    meanLon + 1.915 * rad * Math.sin(meanAnomaly) + 0.02 * rad * Math.sin(2 * meanAnomaly);
  const obliquity = (23.439 - 0.0000004 * days) * rad;

  const declination = Math.asin(Math.sin(obliquity) * Math.sin(ecliptic));
  const rightAscension = Math.atan2(Math.cos(obliquity) * Math.sin(ecliptic), Math.cos(ecliptic));

  const gmst = (18.697374558 + 24.06570982441908 * days) % 24;
  const localSidereal = (((gmst * 15 + lon) % 360) + 360) % 360 * rad;
  const hourAngle = localSidereal - rightAscension;

  const latRad = lat * rad;
  const altitude = Math.asin(
    Math.sin(latRad) * Math.sin(declination) +
      Math.cos(latRad) * Math.cos(declination) * Math.cos(hourAngle),
  );
  const azimuth = Math.atan2(
    -Math.sin(hourAngle),
    Math.tan(declination) * Math.cos(latRad) - Math.sin(latRad) * Math.cos(hourAngle),
  );

  return {
    altitudeDeg: altitude / rad,
    azimuthDeg: ((azimuth / rad) % 360 + 360) % 360,
  };
}

export interface SkyLighting {
  /** Unit vector toward the sun, in render space (Y up, scene north is -Z). */
  sunDirection: [number, number, number];
  sunColour: number;
  sunIntensity: number;
  /** Colour at the zenith. */
  skyTop: number;
  /** Colour at the horizon, paler and warmer than the zenith on a clear day. */
  skyHorizon: number;
  hemiSky: number;
  hemiGround: number;
  hemiIntensity: number;
  /**
   * A weak second sun, aimed opposite the real one and slightly above the horizon.
   *
   * A hemisphere light cannot serve both a wall and a pavement: it gives a horizontal surface the
   * full sky term and a vertical one about half, so any value bright enough to keep a shaded facade
   * off black blows out every pavement under a high sun. This project has hit that trap twice.
   *
   * A fill light aimed low and opposite does what the hemisphere cannot: it lands almost entirely on
   * the vertical faces that the sun is not reaching, and contributes very little to the ground,
   * because the ground is nearly edge-on to it. That is the standard cinematographer's answer and it
   * costs one more light.
   */
  fillDirection: [number, number, number];
  fillColour: number;
  fillIntensity: number;
  /** Renderer exposure, which has to fall as the sun does or dusk reads as noon. */
  exposure: number;
  /** 0 at night, 1 in full sun. Useful for deciding whether to draw window glow. */
  daylight: number;
  altitudeDeg: number;
  azimuthDeg: number;
}

function mix(a: [number, number, number], b: [number, number, number], t: number): number {
  const clamped = Math.max(0, Math.min(1, t));
  const r = Math.round(a[0] + (b[0] - a[0]) * clamped);
  const g = Math.round(a[1] + (b[1] - a[1]) * clamped);
  const bl = Math.round(a[2] + (b[2] - a[2]) * clamped);
  return (r << 16) | (g << 8) | bl;
}

// Clear-sky reference points. A rendered sky has to be brighter than intuition suggests: a real
// clear zenith is around 60 per cent luminance and the horizon much paler still, because you are
// looking through far more atmosphere. Authoring these at "photograph of a blue sky" values produces
// something that reads as dusk, which is most of what made the old rig look permanently overcast.
const ZENITH_NOON: [number, number, number] = [116, 168, 232];
const ZENITH_GOLDEN: [number, number, number] = [150, 172, 214];
const ZENITH_DUSK: [number, number, number] = [64, 80, 122];

const HORIZON_NOON: [number, number, number] = [214, 232, 248];
const HORIZON_GOLDEN: [number, number, number] = [252, 214, 172];
const HORIZON_DUSK: [number, number, number] = [140, 116, 128];

const SUN_HIGH: [number, number, number] = [255, 250, 240];
const SUN_GOLDEN: [number, number, number] = [255, 194, 128];
const SUN_HORIZON: [number, number, number] = [255, 148, 92];

/**
 * Turn a sun position into a light rig.
 *
 * The shape of the curves matters more than any single constant. Sun intensity rises quickly out of
 * twilight and then flattens, because the eye's response does; sky colour swings warm below about
 * 12° of altitude, which is what "golden hour" physically is; and exposure falls as the sun sets, so
 * that dusk is dimmer than noon rather than merely oranger. Without that last part every hour of the
 * day renders at the same apparent brightness, which is exactly what made the old rig read as
 * permanently overcast.
 */
export function skyLighting(sun: SunPosition): SkyLighting {
  const { altitudeDeg, azimuthDeg } = sun;
  const rad = Math.PI / 180;

  // Scene north is -Z in render space; azimuth is clockwise from north.
  const altitude = altitudeDeg * rad;
  const azimuth = azimuthDeg * rad;
  const horizontal = Math.cos(altitude);
  const sunDirection: [number, number, number] = [
    horizontal * Math.sin(azimuth),
    Math.sin(altitude),
    -horizontal * Math.cos(azimuth),
  ];

  // Daylight ramps in across civil twilight rather than switching on at the horizon.
  const daylight = Math.max(0, Math.min(1, (altitudeDeg + 6) / 12));
  // How "golden" the light is: 1 at the horizon, 0 above about 12 degrees.
  const golden = Math.max(0, Math.min(1, 1 - altitudeDeg / 12));
  // How far into dusk: 1 below the horizon, 0 above about 2 degrees.
  const dusk = Math.max(0, Math.min(1, 1 - (altitudeDeg + 2) / 4));

  const sunColour =
    altitudeDeg <= 0
      ? mix(SUN_GOLDEN, SUN_HORIZON, 1)
      : mix(SUN_HIGH, altitudeDeg < 6 ? SUN_HORIZON : SUN_GOLDEN, golden);

  // A clear midday sun is strong. This peaks well above the old rig's 2.3 because the old value was
  // chosen to stop an untone-mapped scene clipping, a constraint ACES removed.
  const sunIntensity = daylight * (1.8 + 3.4 * Math.sin(Math.max(0, altitude)));

  const skyTop = dusk > 0 ? mix(ZENITH_GOLDEN, ZENITH_DUSK, dusk) : mix(ZENITH_NOON, ZENITH_GOLDEN, golden);
  const skyHorizon =
    dusk > 0 ? mix(HORIZON_GOLDEN, HORIZON_DUSK, dusk) : mix(HORIZON_NOON, HORIZON_GOLDEN, golden);

  // How far overhead the sun is. Used in three places below, so it is computed once here.
  const overhead = Math.max(0, Math.sin(Math.max(0, altitude)));

  // Sky fill, kept modest. A hemisphere light gives a horizontal surface the full sky term and a
  // vertical one about half, so tuning it bright enough to rescue a shaded facade blows out every
  // pavement under a high sun. The fill light below does that job instead.
  const hemiIntensity = 0.35 + daylight * 0.55;

  // The fill: opposite the sun in plan and low, so it grazes the ground and squarely hits the wall
  // faces the sun has left dark. Tinted with the sky, because that is physically what is lighting
  // them.
  //
  // Kept low even when the sun is high. Raising it with the sun seems intuitive and is wrong: tilting
  // the fill upward reduces its dot product with the vertical faces it exists to serve, so the shaded
  // walls get *darker* exactly when they most need help. It is the intensity that has to rise.
  const fillAzimuth = (azimuthDeg + 180) * rad;
  const fillAltitude = 12 * rad;
  const fillHorizontal = Math.cos(fillAltitude);
  const fillDirection: [number, number, number] = [
    fillHorizontal * Math.sin(fillAzimuth),
    Math.sin(fillAltitude),
    -fillHorizontal * Math.cos(fillAzimuth),
  ];
  // Fill tint. Deliberately close to white rather than sky blue: three.js multiplies light colour by
  // surface colour in LINEAR space, where a mid-blue sky is about 0.18, and brick is about 0.18 too.
  // Multiplying the two leaves roughly 3 per cent of the energy the numbers suggest, which is why a
  // "sky-tinted" fill kept leaving shaded walls black no matter how far its intensity was pushed.
  // A pale tint carries the colour without eating the light.
  const fillColour = mix([214, 226, 244], [236, 214, 198], golden);
  // Rises with the sun, and by more than the sun does. Exposure is being pulled down at noon to keep
  // the pavement off white, and that pull applies to the shaded walls too; without compensation the
  // shaded side of every building gets darker as the day gets brighter, which is backwards.
  const fillIntensity = daylight * (1.0 + 1.6 * overhead);

  // Exposure falls with the sun, which is what makes evening look like evening rather than like noon
  // with orange lights. It also eases off when the sun is high, because a horizontal surface takes
  // the full beam at noon while a facade takes a glancing fraction of it: without this the pavement
  // is white by the time the walls are right. This is the same trap tone mapping was introduced to
  // fix, reappearing once the sun was allowed to get properly high.
  const exposure = (0.55 + daylight * 0.72) * (1 - 0.38 * overhead);

  return {
    sunDirection,
    sunColour,
    sunIntensity,
    skyTop,
    skyHorizon,
    hemiSky: mix([220, 232, 246], [246, 224, 204], golden),
    hemiGround: 0x8a8074,
    hemiIntensity,
    fillDirection,
    fillColour,
    fillIntensity,
    exposure,
    daylight,
    altitudeDeg,
    azimuthDeg,
  };
}

/** Named presets, for when a specific look is wanted rather than a specific moment. */
export const LIGHTING_PRESETS = {
  'golden-morning': { altitudeDeg: 8, azimuthDeg: 85 },
  'mid-morning': { altitudeDeg: 34, azimuthDeg: 118 },
  noon: { altitudeDeg: 66, azimuthDeg: 180 },
  afternoon: { altitudeDeg: 40, azimuthDeg: 245 },
  'golden-evening': { altitudeDeg: 7, azimuthDeg: 282 },
  dusk: { altitudeDeg: -3, azimuthDeg: 292 },
} as const satisfies Record<string, SunPosition>;

export type LightingPreset = keyof typeof LIGHTING_PRESETS;
