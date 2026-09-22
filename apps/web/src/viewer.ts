/**
 * The Three.js viewport.
 *
 * It draws the mesh the backend sent and nothing else: no geometry is
 * constructed from the CAD document, from bounds, from STEP or from STL, and
 * no measurement is taken here. The camera, the light and the material are
 * this file's only opinions.
 *
 * Two conventions the backend documents are honoured rather than converted:
 *
 * * **Z up.** The render model is right-handed Z-up; Three.js defaults to
 *   Y-up. The *camera's* up vector is set to Z instead of rotating the
 *   geometry, so the vertices drawn are the vertices sent.
 * * **Counter-clockwise outward.** That is already Three.js's front-face
 *   convention, so the material draws front faces and no index is reversed.
 */

import {
  AmbientLight,
  BufferAttribute,
  BufferGeometry,
  Color,
  DirectionalLight,
  FrontSide,
  Mesh,
  MeshLambertMaterial,
  PerspectiveCamera,
  Scene,
  Vector3 as ThreeVector3,
  WebGLRenderer,
} from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";

import {
  fitCameraToBounds,
  toGeometryArrays,
  type RenderBounds,
  type RenderModel,
} from "./render-model";

/** A neutral material: one colour, no textures, no PBR, no face colouring. */
const SURFACE_COLOUR = 0xb8c2cc;
const BACKGROUND_COLOUR = 0xe9edf2;
const FIELD_OF_VIEW_DEGREES = 45;

/**
 * Build a `BufferGeometry` from a render model.
 *
 * Indexed exactly as the model lists its triangles, with the model's own
 * normals and its per-face vertices intact. Nothing is merged and nothing is
 * recomputed -- a test asserts the attribute counts equal the model's.
 */
export function buildGeometry(model: RenderModel): BufferGeometry {
  const arrays = toGeometryArrays(model);
  const geometry = new BufferGeometry();
  geometry.setAttribute("position", new BufferAttribute(arrays.positions, 3));
  geometry.setAttribute("normal", new BufferAttribute(arrays.normals, 3));
  geometry.setIndex(new BufferAttribute(arrays.indices, 1));
  return geometry;
}

/**
 * The smallest box containing every body's bounds.
 *
 * Pure, and deliberately here rather than in the render model: a RenderModel
 * describes ONE body, and inventing a combined one would be a second, merged
 * representation of a part the plan never merged. This is a camera question,
 * so it is answered where the camera is.
 */
function combinedBounds(models: readonly RenderModel[]): RenderBounds | null {
  if (models.length === 0) {
    return null;
  }
  const minimum: [number, number, number] = [Infinity, Infinity, Infinity];
  const maximum: [number, number, number] = [-Infinity, -Infinity, -Infinity];
  for (const model of models) {
    for (let axis = 0; axis < 3; axis += 1) {
      minimum[axis] = Math.min(minimum[axis], model.bounds.minimum[axis]);
      maximum[axis] = Math.max(maximum[axis], model.bounds.maximum[axis]);
    }
  }
  const size: [number, number, number] = [
    maximum[0] - minimum[0],
    maximum[1] - minimum[1],
    maximum[2] - minimum[2],
  ];
  return { minimum, maximum, size };
}

export interface Viewer {
  /** Draw a render model, replacing whatever was drawn before. */
  show(model: RenderModel): void;
  /**
   * Draw several bodies at once, replacing whatever was drawn before.
   *
   * One mesh per body, never a merged one: merging would be a fuse the plan
   * did not ask for, and the plan is the only thing in this system that
   * joins solids. The camera frames the bodies TOGETHER, from their combined
   * bounds, so a two-body part arrives in view as a two-body part rather
   * than with one body framed and the other off screen.
   */
  showBodies(models: readonly RenderModel[]): void;
  /** Re-frame the current model from its own bounds. */
  fit(): void;
  /** Match the canvas to its container. */
  resize(): void;
  /** Release the GPU resources. */
  dispose(): void;
}

/**
 * Create the viewport on a canvas.
 *
 * Requires a real WebGL context, so it is called only in a browser. The parts
 * that can be tested without one -- the geometry conversion and the camera
 * fit -- are pure functions in `render-model.ts` and `buildGeometry` above.
 */
export function createViewer(canvas: HTMLCanvasElement): Viewer {
  const renderer = new WebGLRenderer({ canvas, antialias: true });
  renderer.setPixelRatio(Math.min(globalThis.devicePixelRatio ?? 1, 2));

  const scene = new Scene();
  scene.background = new Color(BACKGROUND_COLOUR);

  const camera = new PerspectiveCamera(FIELD_OF_VIEW_DEGREES, 1, 0.1, 1000);
  // The model is Z-up; the camera is told so rather than the geometry rotated.
  camera.up.set(0, 0, 1);

  const headlight = new DirectionalLight(0xffffff, 2.2);
  const fill = new AmbientLight(0xffffff, 0.55);
  scene.add(fill);
  camera.add(headlight);
  headlight.position.set(0.5, -1, 1);
  scene.add(camera);

  const controls = new OrbitControls(camera, canvas);
  controls.enableDamping = true;

  const material = new MeshLambertMaterial({
    color: SURFACE_COLOUR,
    side: FrontSide,
  });

  let meshes: Mesh[] = [];
  let bounds: RenderBounds | null = null;
  let running = true;

  function resize(): void {
    const width = canvas.clientWidth || 1;
    const height = canvas.clientHeight || 1;
    renderer.setSize(width, height, false);
    camera.aspect = width / height;
    camera.updateProjectionMatrix();
  }

  function fit(): void {
    if (bounds === null) {
      return;
    }
    const view = fitCameraToBounds(bounds, FIELD_OF_VIEW_DEGREES);
    camera.near = view.near;
    camera.far = view.far;
    camera.position.set(...view.position);
    camera.updateProjectionMatrix();
    controls.target.set(...(view.target as unknown as [number, number, number]));
    controls.update();
  }

  function show(model: RenderModel): void {
    showBodies([model]);
  }

  function showBodies(models: readonly RenderModel[]): void {
    for (const drawn of meshes) {
      scene.remove(drawn);
      drawn.geometry.dispose();
    }
    meshes = [];
    bounds = combinedBounds(models);
    for (const model of models) {
      const drawn = new Mesh(buildGeometry(model), material);
      scene.add(drawn);
      meshes.push(drawn);
    }
    resize();
    fit();
  }

  function frame(): void {
    if (!running) {
      return;
    }
    controls.update();
    renderer.render(scene, camera);
    globalThis.requestAnimationFrame(frame);
  }

  resize();
  camera.position.set(1, -1, 1).multiplyScalar(10);
  controls.target.copy(new ThreeVector3(0, 0, 0));
  frame();

  return {
    show,
    fit,
    resize,
    showBodies,
    dispose(): void {
      running = false;
      controls.dispose();
      for (const drawn of meshes) {
        drawn.geometry.dispose();
      }
      material.dispose();
      renderer.dispose();
    },
  };
}
