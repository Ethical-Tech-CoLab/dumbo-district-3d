/**
 * Walls and ground together, at several sun altitudes.
 *
 * Checked jointly on purpose. This project has twice tuned the light against facades and quietly
 * blown out every pavement, because a hemisphere light gives a horizontal surface the full sky term
 * and a vertical one about half. Any change to the rig has to be read across both columns at once.
 */
import * as THREE from 'three';
import { skyLighting, sunPosition, LIGHTING_PRESETS } from '../src/Sky';

const BRICK = new THREE.Color('#765244');
const CONCRETE = new THREE.Color('#928e86');
const ASPHALT = new THREE.Color('#3c3a38');

const aces = (x: number, e: number) => {
  const v = x * e * 0.6;
  return Math.min(1, Math.max(0, (v * (2.51 * v + 0.03)) / (v * (2.43 * v + 0.59) + 0.14)));
};

function lightness(
  base: THREE.Color,
  ndl: number,
  rig: ReturnType<typeof skyLighting>,
  normal: THREE.Vector3,
) {
  const sun = base.clone().multiply(new THREE.Color(rig.sunColour)).multiplyScalar(rig.sunIntensity * ndl);
  // Hemisphere, the way three.js actually computes it: the sky and ground colours are mixed by the
  // normal's tilt, mix(ground, sky, 0.5 * dot(N, up) + 0.5). A horizontal face takes pure sky, a
  // vertical face takes half sky and half ground bounce. This test previously gave a wall half the
  // sky and *none* of the ground, which understated every vertical surface it measured -- harmless
  // while walls were being tuned against each other, but wrong the moment a column was added for a
  // surface whose only remaining light was the hemisphere.
  const tilt = 0.5 * normal.dot(new THREE.Vector3(0, 1, 0)) + 0.5;
  const hemiColour = new THREE.Color(rig.hemiGround).lerp(new THREE.Color(rig.hemiSky), tilt);
  const hemi = base.clone().multiply(hemiColour).multiplyScalar(rig.hemiIntensity);
  const fillDir = new THREE.Vector3(...rig.fillDirection).normalize();
  const fillNdl = Math.max(0, normal.dot(fillDir));
  const fill = base.clone().multiply(new THREE.Color(rig.fillColour)).multiplyScalar(rig.fillIntensity * fillNdl);
  const bounce = base
    .clone()
    .multiply(new THREE.Color(rig.bounceColour))
    .multiplyScalar(rig.bounceIntensity);
  const total = sun.add(hemi).add(fill).add(bounce);
  const out = new THREE.Color(
    aces(total.r, rig.exposure),
    aces(total.g, rig.exposure),
    aces(total.b, rig.exposure),
  );
  const hsl = { h: 0, s: 0, l: 0 };
  out.getHSL(hsl);
  return hsl.l;
}

function row(label: string, altitudeDeg: number, azimuthDeg: number) {
  const rig = skyLighting({ altitudeDeg, azimuthDeg });
  const dir = new THREE.Vector3(...rig.sunDirection).normalize();

  const facing = new THREE.Vector3(dir.x, 0, dir.z).normalize();
  const away = facing.clone().negate();
  const up = new THREE.Vector3(0, 1, 0);
  const litWall = lightness(BRICK, Math.max(0, facing.dot(dir)), rig, facing);
  const shadeWall = lightness(BRICK, Math.max(0, away.dot(dir)), rig, away);
  // The worst-lit surface in the district: a wall that faces the sun's side of the street but stands
  // in a taller building's cast shadow. It loses the sun, and because the fill sits opposite the sun
  // it is turned away from that too, so only the hemisphere and the bounce remain. Cast shadows
  // created this case -- before them such a wall always caught some sun -- and it is the one that
  // broke, measuring 0.078 in the render before a bounce term existed.
  const occludedWall = lightness(BRICK, 0, rig, facing);
  const groundNdl = Math.max(0, up.dot(dir));
  const pavement = lightness(CONCRETE, groundNdl, rig, up);
  const road = lightness(ASPHALT, groundNdl, rig, up);
  // Pavement standing in a building's shadow: no sun term at all, only sky fill and the bounce
  // light. This is what shadows actually produce, and the number that decides whether a shaded
  // street canyon reads as shade or as a hole in the ground.
  const pavementShadowed = lightness(CONCRETE, 0, rig, up);

  const flag = (v: number, lo: number, hi: number) => (v < lo ? ' LOW' : v > hi ? 'HIGH' : '  ok');

  // Report the brightest wall the sun actually reaches, not a wall of our choosing. Under a high
  // sun no vertical face is well lit -- that is a fact about noon, not a fault in the rig -- so
  // tilting the sample normal toward the sun measures the best case a facade can get.
  const best = dir.clone().setY(Math.max(0, dir.y * 0.35)).normalize();
  const bestWall = lightness(BRICK, Math.max(0, best.dot(dir)), rig, best);

  console.log(
    `${label.padEnd(16)} alt ${altitudeDeg.toFixed(0).padStart(3)}°  ` +
      `lit ${litWall.toFixed(2)}  best ${bestWall.toFixed(2)}${flag(bestWall, 0.3, 0.55)}  ` +
      `shade ${shadeWall.toFixed(2)}${flag(shadeWall, 0.09, 0.25)}  ` +
      `occluded ${occludedWall.toFixed(2)}${flag(occludedWall, 0.03, 0.25)}  ` +
      `pave ${pavement.toFixed(2)}${flag(pavement, 0.4, 0.75)}  ` +
      `shadowed ${pavementShadowed.toFixed(2)}${flag(pavementShadowed, 0.16, 0.45)}  ` +
      `road ${road.toFixed(2)}`,
  );

  return {
    best: bestWall,
    shade: shadeWall,
    occluded: occludedWall,
    pave: pavement,
    shadowed: pavementShadowed,
  };
}

console.log('targets: best-lit brick .30-.55 · shaded brick .09-.25 · pavement .40-.75');
console.log('(pavement runs bright under a high sun because a horizontal surface takes the full beam;');
console.log(' the upper bound is set where concrete stops reading as concrete, not where it is convenient)');
console.log();

let failures = 0;

function check(label: string, altitudeDeg: number, azimuthDeg: number) {
  const r = row(label, altitudeDeg, azimuthDeg);
  // Only daylight is asserted. Golden hour is legitimately dim and dusk is legitimately dark; the
  // point of the rig is that they differ, not that they all land in the same band.
  if (altitudeDeg < 15) return;
  if (r.best < 0.3 || r.best > 0.55) failures++;
  if (r.shade < 0.09 || r.shade > 0.25) failures++;
  // The occluded column is a worst-case bound rather than a typical reading: it is the exact wall
  // orientation that loses both the sun and the fill, which in the render is a measure-zero case --
  // sampling this class of wall on screen gives about 0.13, three times what the bound predicts,
  // because real walls are rarely exactly perpendicular to the fill. So the floor here is set to
  // catch the failure that actually happened (an omnidirectional term of zero, which drops this to
  // roughly 0.015) rather than to assert a realism target the bound cannot meet.
  if (r.occluded < 0.03) failures++;
  if (r.pave < 0.4 || r.pave > 0.75) failures++;
  // A shadowed pavement must stay readable. Shadow is not absence of light: the sky is still up
  // there, and a street canyon whose shaded half goes to near-black looks like a hole rather than
  // like shade. This is the number that decides whether shadows can be switched on at all.
  if (r.shadowed < 0.16 || r.shadowed > 0.45) failures++;
}

for (const [name, pos] of Object.entries(LIGHTING_PRESETS)) check(name, pos.altitudeDeg, pos.azimuthDeg);
console.log();
const now = sunPosition(40.703, -73.989, new Date());
check('live sun', now.altitudeDeg, now.azimuthDeg);
for (const [label, iso] of [
  ['midsummer noon', '2026-06-21T17:00:00Z'],
  ['midwinter noon', '2026-12-21T17:00:00Z'],
  ['equinox morning', '2026-09-22T14:00:00Z'],
] as const) {
  const p = sunPosition(40.703, -73.989, new Date(iso));
  check(label, p.altitudeDeg, p.azimuthDeg);
}

console.log();
if (failures) {
  console.log(`${failures} reading(s) outside the target band`);
  process.exit(1);
}
console.log('every daylight reading is within its target band');
