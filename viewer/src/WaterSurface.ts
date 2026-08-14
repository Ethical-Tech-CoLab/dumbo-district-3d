/**
 * The East River surface: planar reflection, procedural ripple, and sun glitter.
 *
 * Why this is written rather than borrowed
 * ----------------------------------------
 * `owenyuwono/poseidon` was suggested as a reference and it is genuinely good work -- MIT licensed,
 * a full Tessendorf/Horvath FFT ocean with three wave cascades. Two things rule it out here, and
 * neither is a criticism of it:
 *
 *   1. It is WebGPU only, and says so in its own README. The FFT runs as `renderer.compute()`
 *      dispatches, which have no WebGL2 equivalent; the project aborts deliberately if it detects a
 *      WebGL2 fallback. This viewer is a WebGL2 `WebGLRenderer`.
 *   2. It does not reflect anything. Its water samples an *analytic sky colour* along the reflected
 *      ray -- no planar reflection, no screen-space reflection, no cubemap. That is the correct
 *      choice for open ocean with nothing to reflect, and exactly wrong for a river 500 m wide with
 *      the Brooklyn Bridge over it and the Manhattan skyline behind it.
 *
 * The thing that makes this stretch of water read as this stretch of water is that the bridges and
 * the skyline are in it. So: a real planar reflection, at reduced resolution because a reflection in
 * moving water is never sharp anyway, distorted by the same procedural normals that carry the
 * ripple. Ocean-grade spectral waves would be a poor trade for a tidal strait viewed from a
 * promenade, and would cost more than everything else in the district put together.
 *
 * No textures, so nothing needs vendoring and nothing needs a licence record.
 */
import * as THREE from 'three';

/** Reflection resolution. Deliberately low: it is about to be distorted by ripple and Fresnel. */
const REFLECTION_SIZE = 512;

const VERTEX = /* glsl */ `
  uniform mat4 uTextureMatrix;
  varying vec4 vReflectUv;
  varying vec3 vWorld;

  void main() {
    vec4 world = modelMatrix * vec4(position, 1.0);
    vWorld = world.xyz;
    vReflectUv = uTextureMatrix * world;
    gl_Position = projectionMatrix * viewMatrix * world;
  }
`;

const FRAGMENT = /* glsl */ `
  uniform sampler2D uReflection;
  uniform float uTime;
  uniform vec3 uSunDirection;
  uniform vec3 uSunColour;
  uniform vec3 uDeepColour;
  uniform vec3 uShallowColour;
  uniform float uDaylight;
  varying vec4 vReflectUv;
  varying vec3 vWorld;

  /**
   * Ripple normal from summed directional waves.
   *
   * Four scales rather than one: a single frequency reads as corrugated iron, and it is the beat
   * between scales that makes water look like water. Cheap enough to run per fragment, and free of
   * the texture a scrolling normal map would need.
   */
  vec3 rippleNormal(vec2 p, float t) {
    vec2 n = vec2(0.0);
    // Direction, wavelength, amplitude, speed.
    n += vec2(0.9, 0.35) * sin(dot(p, vec2(0.9, 0.35)) * 0.42 + t * 1.10) * 0.055;
    n += vec2(-0.6, 0.8) * sin(dot(p, vec2(-0.6, 0.8)) * 0.73 + t * 1.55) * 0.038;
    n += vec2(0.25, -0.97) * sin(dot(p, vec2(0.25, -0.97)) * 1.60 + t * 2.20) * 0.020;
    n += vec2(-0.95, -0.3) * sin(dot(p, vec2(-0.95, -0.3)) * 3.10 + t * 3.10) * 0.011;
    return normalize(vec3(n.x, 1.0, n.y));
  }

  void main() {
    vec3 normal = rippleNormal(vWorld.xz, uTime);
    vec3 viewDir = normalize(cameraPosition - vWorld);

    // Fresnel: a river is a mirror at grazing angles and dark water straight down. This is most of
    // why water reads as wet, and it is why the far bank reflects and your own feet do not.
    float fresnel = 0.02 + 0.98 * pow(1.0 - clamp(dot(normal, viewDir), 0.0, 1.0), 4.0);

    // Reflection, offset by the ripple so the skyline shivers rather than sitting there like a
    // photograph pasted under the bank.
    vec2 uv = vReflectUv.xy / max(vReflectUv.w, 0.0001);
    uv += normal.xz * 0.06;
    vec3 reflected = texture2D(uReflection, clamp(uv, 0.001, 0.999)).rgb;

    // Body colour: deeper looking down, paler toward the far bank where the channel shallows.
    float depthMix = pow(clamp(dot(normal, viewDir), 0.0, 1.0), 0.6);
    vec3 body = mix(uShallowColour, uDeepColour, depthMix);

    vec3 colour = mix(body, reflected, clamp(fresnel, 0.0, 0.86));

    // Sun glitter. A specular lobe on the ripple normal, which is the moving sparkle that says the
    // surface is alive; without it still water looks like painted glass.
    vec3 halfway = normalize(uSunDirection + viewDir);
    float glitter = pow(max(dot(normal, halfway), 0.0), 220.0);
    colour += uSunColour * glitter * 0.9 * uDaylight;

    gl_FragColor = vec4(colour, 1.0);
    #include <colorspace_fragment>
  }
`;

export class WaterSurface {
  readonly mesh: THREE.Mesh;

  private readonly target: THREE.WebGLRenderTarget;
  private readonly reflectionCamera = new THREE.PerspectiveCamera();
  private readonly material: THREE.ShaderMaterial;
  private readonly surfaceY: number;
  private readonly textureMatrix = new THREE.Matrix4();
  /**
   * What the water does not bother reflecting.
   *
   * Measured, not assumed: reflecting the whole scene cost 22 ms a frame, and the street props were
   * almost all of it -- 26,000 instanced trees, railings, bollards and bins, none of which you can
   * see in a river from any viewpoint in this district. Dropping props and paving takes the pass
   * under a millisecond and removes nothing a person would look for. Buildings, the bridges, the
   * Manhattan skyline and the sky all still reflect, because those are what the water is *for*.
   */
  private excluded: () => THREE.Object3D[] = () => [];

  setExcluded(provider: () => THREE.Object3D[]): void {
    this.excluded = provider;
  }

  constructor(surfaceY: number, extent = 6000) {
    this.surfaceY = surfaceY;
    this.target = new THREE.WebGLRenderTarget(REFLECTION_SIZE, REFLECTION_SIZE, {
      minFilter: THREE.LinearFilter,
      magFilter: THREE.LinearFilter,
    });

    this.material = new THREE.ShaderMaterial({
      uniforms: {
        uReflection: { value: this.target.texture },
        uTextureMatrix: { value: this.textureMatrix },
        uTime: { value: 0 },
        uSunDirection: { value: new THREE.Vector3(0.4, 0.6, 0.4) },
        uSunColour: { value: new THREE.Color(0xfff2df) },
        uDeepColour: { value: new THREE.Color(0x1d3446) },
        uShallowColour: { value: new THREE.Color(0x40657f) },
        uDaylight: { value: 1 },
      },
      vertexShader: VERTEX,
      fragmentShader: FRAGMENT,
    });

    const geometry = new THREE.PlaneGeometry(extent, extent, 1, 1);
    this.mesh = new THREE.Mesh(geometry, this.material);
    this.mesh.rotation.x = -Math.PI / 2;
    this.mesh.position.y = surfaceY;
    this.mesh.renderOrder = -1;
    this.mesh.name = 'water_surface';
    // Water must not receive the sun's shadow map: it is its own lighting model, and a shadow
    // stripe across a reflective surface reads as a slick rather than as shade.
    this.mesh.receiveShadow = false;
    this.mesh.castShadow = false;
  }

  /** Follow the lighting rig, so the glitter tracks the sun and dusk does not sparkle. */
  setLighting(sunDirection: [number, number, number], sunColour: number, daylight: number): void {
    const [x, y, z] = sunDirection;
    // Scene (Z-up) to render (Y-up).
    this.material.uniforms.uSunDirection.value.set(x, z, -y).normalize();
    this.material.uniforms.uSunColour.value.setHex(sunColour);
    this.material.uniforms.uDaylight.value = daylight;
  }

  /**
   * Render what the water can see, then hand it to the shader.
   *
   * The reflection camera is the viewer's camera mirrored through the water plane. Its projection
   * is left alone but the near plane is *not* clipped to the surface: at this reflectivity and
   * resolution the artefacts that obliquely-clipped planes exist to prevent are smaller than the
   * ripple distortion already applied.
   */
  update(
    renderer: THREE.WebGLRenderer,
    scene: THREE.Scene,
    camera: THREE.PerspectiveCamera,
    timeSeconds: number,
  ): void {
    this.material.uniforms.uTime.value = timeSeconds;

    // Nothing to reflect if the eye is under the surface.
    if (camera.position.y <= this.surfaceY) return;

    this.reflectionCamera.copy(camera);
    this.reflectionCamera.position.y = 2 * this.surfaceY - camera.position.y;

    // Mirror the look direction through the plane.
    const forward = new THREE.Vector3();
    camera.getWorldDirection(forward);
    const target = new THREE.Vector3()
      .copy(camera.position)
      .addScaledVector(forward, 100);
    target.y = 2 * this.surfaceY - target.y;
    this.reflectionCamera.up.set(0, 1, 0);
    this.reflectionCamera.lookAt(target);
    this.reflectionCamera.updateMatrixWorld();
    this.reflectionCamera.updateProjectionMatrix();

    // Projective texture matrix: clip space to [0,1] texture space.
    this.textureMatrix.set(
      0.5, 0, 0, 0.5,
      0, 0.5, 0, 0.5,
      0, 0, 0.5, 0.5,
      0, 0, 0, 1,
    );
    this.textureMatrix.multiply(this.reflectionCamera.projectionMatrix);
    this.textureMatrix.multiply(this.reflectionCamera.matrixWorldInverse);

    // Hide the water itself, or it reflects its own back face and the river turns to tar. Street
    // props go with it; see `excluded` for the measurement that put them there.
    const wasVisible = this.mesh.visible;
    this.mesh.visible = false;
    const skipped = this.excluded();
    const previousVisibility = skipped.map((o) => o.visible);
    for (const object of skipped) object.visible = false;

    const previousTarget = renderer.getRenderTarget();
    // Note what is NOT done here: `renderer.shadowMap.enabled` is left alone. Toggling it per frame
    // invalidates three.js's program cache and recompiles every material in the scene. The shadow
    // map is not rebuilt for this pass anyway, because autoUpdate is off and nothing invalidated it.
    renderer.setRenderTarget(this.target);
    renderer.clear();
    renderer.render(scene, this.reflectionCamera);
    renderer.setRenderTarget(previousTarget);

    skipped.forEach((object, i) => (object.visible = previousVisibility[i]));
    this.mesh.visible = wasVisible;
  }

  dispose(): void {
    this.target.dispose();
    this.material.dispose();
    this.mesh.geometry.dispose();
  }
}
