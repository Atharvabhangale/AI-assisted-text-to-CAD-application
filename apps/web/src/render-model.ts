/**
 * The RenderModel, as the backend already defines it, and the pure conversion
 * to WebGL buffers.
 *
 * This file understands the Stage 8 structure and **adds nothing to it**:
 * there is no second mesh format, no re-tessellation, no normal recomputation
 * and no measurement. Everything here is either a type mirroring the
 * backend's `to_dict()` or arithmetic on the arrays it sent.
 */

/** The render format this viewer understands, from the backend's Stage 8 contract. */
export const SUPPORTED_FORMAT_VERSION = "1.0.0";

/** The coordinate system the backend documents: right-handed, Z up. */
export const EXPECTED_COORDINATE_SYSTEM = "right_handed_z_up";

/** The winding the backend documents: counter-clockwise seen from outside. */
export const EXPECTED_WINDING = "counter_clockwise_outward";

/** How normals are attached: one per vertex. */
export const EXPECTED_NORMAL_BINDING = "per_vertex";

export type Vector3 = readonly [number, number, number];
export type Triangle = readonly [number, number, number];

export interface RenderBounds {
  readonly minimum: Vector3;
  readonly maximum: Vector3;
  readonly size: Vector3;
}

export interface TessellationSettings {
  readonly linear_deflection_mm: number;
  readonly angular_deflection_rad: number;
}

/** Exactly the object `GET /builds/{build_key}/render` returns. */
export interface RenderModel {
  readonly format_version: string;
  readonly part_name: string;
  readonly feature_id: string;
  readonly units: string;
  readonly coordinate_system: string;
  readonly winding: string;
  readonly normal_binding: string;
  readonly vertices: readonly Vector3[];
  readonly triangles: readonly Triangle[];
  readonly normals: readonly Vector3[];
  readonly bounds: RenderBounds;
  readonly tessellation: TessellationSettings;
}

/** Flat typed arrays, ready for `THREE.BufferAttribute`. */
export interface GeometryArrays {
  /** Three floats per vertex, in the model's own order. */
  readonly positions: Float32Array;
  /** Three floats per vertex: the model's normals, never recomputed. */
  readonly normals: Float32Array;
  /** Three indices per triangle, exactly as the model listed them. */
  readonly indices: Uint32Array;
  /** How many vertices the model had. Per-face vertices are preserved. */
  readonly vertexCount: number;
  readonly triangleCount: number;
}

export class RenderModelError extends Error {}

/**
 * Check that a payload is a render model this viewer can draw.
 *
 * A shape and conventions check, not a geometric one: the backend owns
 * geometry. It refuses a format version, coordinate system, winding or normal
 * binding other than the documented ones rather than guessing, and refuses
 * counts that disagree with each other or indices outside the vertex array --
 * because drawing those would be undefined behaviour, not a different picture.
 */
export function assertRenderModel(payload: unknown): RenderModel {
  if (typeof payload !== "object" || payload === null) {
    throw new RenderModelError("the render model is not an object");
  }
  const model = payload as Partial<RenderModel>;
  if (model.format_version !== SUPPORTED_FORMAT_VERSION) {
    throw new RenderModelError(
      `this viewer draws render format ${SUPPORTED_FORMAT_VERSION}, not ${String(
        model.format_version,
      )}`,
    );
  }
  if (model.coordinate_system !== EXPECTED_COORDINATE_SYSTEM) {
    throw new RenderModelError(
      `unexpected coordinate system ${String(model.coordinate_system)}`,
    );
  }
  if (model.winding !== EXPECTED_WINDING) {
    throw new RenderModelError(`unexpected winding ${String(model.winding)}`);
  }
  if (model.normal_binding !== EXPECTED_NORMAL_BINDING) {
    throw new RenderModelError(
      `unexpected normal binding ${String(model.normal_binding)}`,
    );
  }
  const { vertices, triangles, normals, bounds } = model;
  if (!Array.isArray(vertices) || !Array.isArray(triangles) || !Array.isArray(normals)) {
    throw new RenderModelError("the render model is missing its mesh arrays");
  }
  if (vertices.length === 0 || triangles.length === 0) {
    throw new RenderModelError("the render model holds no geometry");
  }
  if (normals.length !== vertices.length) {
    throw new RenderModelError(
      `${normals.length} normals for ${vertices.length} vertices, but normals are per vertex`,
    );
  }
  if (
    !bounds ||
    !Array.isArray(bounds.minimum) ||
    !Array.isArray(bounds.maximum) ||
    bounds.minimum.length !== 3 ||
    bounds.maximum.length !== 3
  ) {
    throw new RenderModelError("the render model's bounds are malformed");
  }
  for (const triangle of triangles) {
    if (!Array.isArray(triangle) || triangle.length !== 3) {
      throw new RenderModelError("a triangle is not three indices");
    }
    for (const index of triangle) {
      if (!Number.isInteger(index) || index < 0 || index >= vertices.length) {
        throw new RenderModelError(`triangle index ${String(index)} is out of range`);
      }
    }
  }
  return model as RenderModel;
}

/**
 * Flatten a render model into WebGL buffers.
 *
 * **Nothing is optimised away.** Stage 8 emits per-face vertices on purpose,
 * so a box keeps its sharp edges; merging duplicates here would smooth them.
 * The vertex count out equals the vertex count in, the indices are the
 * model's own, and the normals are the model's own -- never recomputed.
 */
export function toGeometryArrays(model: RenderModel): GeometryArrays {
  const vertexCount = model.vertices.length;
  const triangleCount = model.triangles.length;
  const positions = new Float32Array(vertexCount * 3);
  const normals = new Float32Array(vertexCount * 3);
  for (let index = 0; index < vertexCount; index += 1) {
    const vertex = model.vertices[index];
    const normal = model.normals[index];
    positions[index * 3] = vertex[0];
    positions[index * 3 + 1] = vertex[1];
    positions[index * 3 + 2] = vertex[2];
    normals[index * 3] = normal[0];
    normals[index * 3 + 1] = normal[1];
    normals[index * 3 + 2] = normal[2];
  }
  const indices = new Uint32Array(triangleCount * 3);
  for (let index = 0; index < triangleCount; index += 1) {
    const triangle = model.triangles[index];
    indices[index * 3] = triangle[0];
    indices[index * 3 + 1] = triangle[1];
    indices[index * 3 + 2] = triangle[2];
  }
  return { positions, normals, indices, vertexCount, triangleCount };
}

/** Where to put the camera so the whole model is in frame. */
export interface CameraFit {
  /** The model's centre, from its own bounds. */
  readonly target: Vector3;
  /** A position looking down at the model from a corner, Z up. */
  readonly position: Vector3;
  /** The distance from target to position. */
  readonly distance: number;
  /** A near plane comfortably inside the model. */
  readonly near: number;
  /** A far plane comfortably beyond it. */
  readonly far: number;
}

/**
 * Fit a camera to the model's **own bounds**. No hard-coded coordinates.
 *
 * The distance is the bounding sphere's radius divided by the half-angle's
 * sine, with a small margin, so the model fits whatever the aspect ratio --
 * and the direction is a fixed diagonal in the model's Z-up frame, which is
 * the one conventional choice a viewer has to make.
 */
export function fitCameraToBounds(
  bounds: RenderBounds,
  fieldOfViewDegrees = 45,
  margin = 1.25,
): CameraFit {
  const target: Vector3 = [
    (bounds.minimum[0] + bounds.maximum[0]) / 2,
    (bounds.minimum[1] + bounds.maximum[1]) / 2,
    (bounds.minimum[2] + bounds.maximum[2]) / 2,
  ];
  const extent: Vector3 = [
    Math.abs(bounds.maximum[0] - bounds.minimum[0]),
    Math.abs(bounds.maximum[1] - bounds.minimum[1]),
    Math.abs(bounds.maximum[2] - bounds.minimum[2]),
  ];
  const radius =
    Math.sqrt(extent[0] ** 2 + extent[1] ** 2 + extent[2] ** 2) / 2 || 1;
  const halfAngle = ((fieldOfViewDegrees / 2) * Math.PI) / 180;
  const distance = (radius / Math.sin(halfAngle)) * margin;
  // A fixed diagonal, normalised, in the model's own right-handed Z-up frame.
  const direction: Vector3 = [1 / Math.sqrt(3), -1 / Math.sqrt(3), 1 / Math.sqrt(3)];
  return {
    target,
    position: [
      target[0] + direction[0] * distance,
      target[1] + direction[1] * distance,
      target[2] + direction[2] * distance,
    ],
    distance,
    near: Math.max(distance / 1000, radius / 1000),
    far: distance + radius * 10,
  };
}

/** A one-line description of the mesh, for the viewport's accessible name. */
export function describeMesh(model: RenderModel): string {
  const [x, y, z] = model.bounds.size;
  return (
    `Tessellated mesh of ${model.part_name}: ` +
    `${model.triangles.length} triangles, ${model.vertices.length} vertices, ` +
    `bounds ${x} x ${y} x ${z} ${model.units}.`
  );
}
