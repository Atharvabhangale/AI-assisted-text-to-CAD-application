/**
 * The RenderModel -> WebGL conversion, on the real Section D render model.
 *
 * The fixture is the backend's own `GET /builds/{key}/render` body, so these
 * are assertions about real tessellated geometry, not about a hand-made mesh.
 */

import { describe, expect, it } from "vitest";
import { BufferGeometry } from "three";

import {
  EXPECTED_COORDINATE_SYSTEM,
  EXPECTED_NORMAL_BINDING,
  EXPECTED_WINDING,
  RenderModelError,
  SUPPORTED_FORMAT_VERSION,
  assertRenderModel,
  describeMesh,
  fitCameraToBounds,
  toGeometryArrays,
  type RenderModel,
} from "../src/render-model";
import { buildGeometry } from "../src/viewer";
import { sectionDRender } from "./harness";

const TOLERANCE_MM = 1e-6;

function model(): RenderModel {
  return assertRenderModel(sectionDRender());
}

describe("the render model the backend sent", () => {
  it("declares the conventions this viewer was written against", () => {
    const payload = sectionDRender();
    expect(payload.format_version).toBe(SUPPORTED_FORMAT_VERSION);
    expect(payload.coordinate_system).toBe(EXPECTED_COORDINATE_SYSTEM);
    expect(payload.winding).toBe(EXPECTED_WINDING);
    expect(payload.normal_binding).toBe(EXPECTED_NORMAL_BINDING);
    expect(payload.units).toBe("mm");
  });

  it("is accepted, with its own vertex and triangle counts", () => {
    const accepted = model();
    expect(accepted.vertices).toHaveLength(2048);
    expect(accepted.triangles).toHaveLength(2044);
    expect(accepted.normals).toHaveLength(2048);
  });

  it("is refused when a convention differs, rather than drawn wrongly", () => {
    const cases: ReadonlyArray<readonly [string, unknown]> = [
      ["format_version", "2.0.0"],
      ["coordinate_system", "right_handed_y_up"],
      ["winding", "clockwise_outward"],
      ["normal_binding", "per_face"],
    ];
    for (const [field, value] of cases) {
      expect(() =>
        assertRenderModel({ ...sectionDRender(), [field]: value }),
      ).toThrow(RenderModelError);
    }
  });

  it("is refused when the mesh contradicts itself", () => {
    const real = sectionDRender();
    expect(() =>
      assertRenderModel({ ...real, normals: real.normals.slice(0, 10) }),
    ).toThrow(/normals are per vertex/);
    expect(() =>
      assertRenderModel({ ...real, triangles: [[0, 1, real.vertices.length]] }),
    ).toThrow(/out of range/);
    expect(() =>
      assertRenderModel({ ...real, vertices: [], triangles: [] }),
    ).toThrow(/holds no geometry/);
    expect(() => assertRenderModel({ ...real, triangles: [[0, 1]] })).toThrow(
      /three indices/,
    );
    expect(() => assertRenderModel(null)).toThrow(/not an object/);
    expect(() => assertRenderModel("{}")).toThrow(/not an object/);
  });
});

describe("the render model converts to WebGL geometry", () => {
  it("produces one position and one normal per vertex, and the model's indices", () => {
    const source = model();
    const arrays = toGeometryArrays(source);

    expect(arrays.vertexCount).toBe(source.vertices.length);
    expect(arrays.triangleCount).toBe(source.triangles.length);
    expect(arrays.positions).toHaveLength(source.vertices.length * 3);
    expect(arrays.normals).toHaveLength(source.vertices.length * 3);
    expect(arrays.indices).toHaveLength(source.triangles.length * 3);
    expect(arrays.positions).toBeInstanceOf(Float32Array);
    expect(arrays.indices).toBeInstanceOf(Uint32Array);
  });

  it("copies every coordinate through unchanged", () => {
    const source = model();
    const arrays = toGeometryArrays(source);
    for (let index = 0; index < source.vertices.length; index += 1) {
      const vertex = source.vertices[index];
      expect(arrays.positions[index * 3]).toBeCloseTo(vertex[0], 4);
      expect(arrays.positions[index * 3 + 1]).toBeCloseTo(vertex[1], 4);
      expect(arrays.positions[index * 3 + 2]).toBeCloseTo(vertex[2], 4);
    }
  });

  it("copies every triangle index verbatim, in order", () => {
    const source = model();
    const arrays = toGeometryArrays(source);
    for (let index = 0; index < source.triangles.length; index += 1) {
      const triangle = source.triangles[index];
      expect(arrays.indices[index * 3]).toBe(triangle[0]);
      expect(arrays.indices[index * 3 + 1]).toBe(triangle[1]);
      expect(arrays.indices[index * 3 + 2]).toBe(triangle[2]);
    }
  });

  it("draws only vertices the model listed", () => {
    const source = model();
    const arrays = toGeometryArrays(source);
    for (const index of arrays.indices) {
      expect(index).toBeLessThan(arrays.vertexCount);
    }
  });

  it("fills a Three.js BufferGeometry with exactly those attributes", () => {
    const source = model();
    const geometry = buildGeometry(source);

    expect(geometry).toBeInstanceOf(BufferGeometry);
    expect(geometry.getAttribute("position").count).toBe(source.vertices.length);
    expect(geometry.getAttribute("normal").count).toBe(source.normals.length);
    expect(geometry.getIndex()?.count).toBe(source.triangles.length * 3);
    // No colours, no UVs, no tangents: this is a neutral solid.
    expect(Object.keys(geometry.attributes).sort()).toEqual([
      "normal",
      "position",
    ]);
  });
});

describe("the normals are the backend's", () => {
  it("copies each normal through unchanged", () => {
    const source = model();
    const arrays = toGeometryArrays(source);
    for (let index = 0; index < source.normals.length; index += 1) {
      const normal = source.normals[index];
      expect(arrays.normals[index * 3]).toBeCloseTo(normal[0], 4);
      expect(arrays.normals[index * 3 + 1]).toBeCloseTo(normal[1], 4);
      expect(arrays.normals[index * 3 + 2]).toBeCloseTo(normal[2], 4);
    }
  });

  it("does not recompute them: they still match what the backend sent", () => {
    const source = model();
    const geometry = buildGeometry(source);
    const attribute = geometry.getAttribute("normal");
    for (let index = 0; index < source.normals.length; index += 1) {
      expect(attribute.getX(index)).toBeCloseTo(source.normals[index][0], 4);
      expect(attribute.getY(index)).toBeCloseTo(source.normals[index][1], 4);
      expect(attribute.getZ(index)).toBeCloseTo(source.normals[index][2], 4);
    }
  });

  it("receives unit normals from the backend, and keeps them unit", () => {
    const arrays = toGeometryArrays(model());
    for (let index = 0; index < arrays.vertexCount; index += 1) {
      const x = arrays.normals[index * 3];
      const y = arrays.normals[index * 3 + 1];
      const z = arrays.normals[index * 3 + 2];
      expect(Math.sqrt(x * x + y * y + z * z)).toBeCloseTo(1, 3);
    }
  });

  it("keeps the plate's flat faces axis-aligned in Z", () => {
    // The plate's top and bottom faces are +Z/-Z; the holes' walls are radial.
    const source = model();
    const axial = source.normals.filter(
      (normal) => Math.abs(Math.abs(normal[2]) - 1) < 1e-6,
    );
    expect(axial.length).toBeGreaterThan(0);
    for (const normal of axial) {
      expect(Math.abs(normal[0])).toBeLessThan(1e-6);
      expect(Math.abs(normal[1])).toBeLessThan(1e-6);
    }
  });
});

describe("per-face vertices are preserved", () => {
  it("keeps the duplicates the backend deliberately emitted", () => {
    const source = model();
    const distinct = new Set(
      source.vertices.map((vertex) => vertex.join(",")),
    );
    // Stage 8 emits per-face vertices so a box keeps sharp edges: there are
    // strictly fewer distinct positions than vertices.
    expect(distinct.size).toBeLessThan(source.vertices.length);

    const arrays = toGeometryArrays(source);
    expect(arrays.vertexCount).toBe(source.vertices.length);
    expect(arrays.vertexCount).toBeGreaterThan(distinct.size);
  });

  it("keeps a shared corner position carrying more than one normal", () => {
    const source = model();
    const byPosition = new Map<string, Set<string>>();
    source.vertices.forEach((vertex, index) => {
      const key = vertex.join(",");
      const normals = byPosition.get(key) ?? new Set<string>();
      normals.add(source.normals[index].map((n) => n.toFixed(6)).join(","));
      byPosition.set(key, normals);
    });
    const sharp = [...byPosition.values()].filter((set) => set.size > 1);
    // A merged mesh would have exactly one normal per position everywhere.
    expect(sharp.length).toBeGreaterThan(0);
  });

  it("does not merge or reorder vertices in the buffer geometry", () => {
    const source = model();
    const geometry = buildGeometry(source);
    const position = geometry.getAttribute("position");
    expect(position.count).toBe(source.vertices.length);
    expect(position.getX(0)).toBeCloseTo(source.vertices[0][0], 4);
    const last = source.vertices.length - 1;
    expect(position.getZ(last)).toBeCloseTo(source.vertices[last][2], 4);
  });
});

describe("the bounds drive fit-to-view", () => {
  it("uses the model's own bounds, with no hard-coded coordinates", () => {
    const bounds = model().bounds;
    expect(bounds.minimum).toEqual([0, 0, 0]);
    expect(bounds.maximum).toEqual([100, 60, 10]);
    expect(bounds.size).toEqual([100, 60, 10]);

    const view = fitCameraToBounds(bounds);
    expect(view.target[0]).toBeCloseTo(50, 6);
    expect(view.target[1]).toBeCloseTo(30, 6);
    expect(view.target[2]).toBeCloseTo(5, 6);
  });

  it("places the camera far enough away to contain the bounding sphere", () => {
    const bounds = model().bounds;
    const view = fitCameraToBounds(bounds, 45, 1.25);
    const radius = Math.sqrt(100 ** 2 + 60 ** 2 + 10 ** 2) / 2;
    const expected = (radius / Math.sin((22.5 * Math.PI) / 180)) * 1.25;
    expect(view.distance).toBeCloseTo(expected, 6);
    expect(view.distance).toBeGreaterThan(radius);

    const offset = Math.sqrt(
      (view.position[0] - view.target[0]) ** 2 +
        (view.position[1] - view.target[1]) ** 2 +
        (view.position[2] - view.target[2]) ** 2,
    );
    expect(offset).toBeCloseTo(view.distance, 4);
  });

  it("looks down at the model, honouring the Z-up frame", () => {
    const view = fitCameraToBounds(model().bounds);
    expect(view.position[2]).toBeGreaterThan(view.target[2]);
  });

  it("keeps the whole model between the near and far planes", () => {
    const bounds = model().bounds;
    const view = fitCameraToBounds(bounds);
    const radius = Math.sqrt(100 ** 2 + 60 ** 2 + 10 ** 2) / 2;
    expect(view.near).toBeGreaterThan(0);
    expect(view.near).toBeLessThan(view.distance - radius);
    expect(view.far).toBeGreaterThan(view.distance + radius);
  });

  it("scales with the model instead of assuming a size", () => {
    const small = fitCameraToBounds({
      minimum: [0, 0, 0],
      maximum: [1, 1, 1],
      size: [1, 1, 1],
    });
    const large = fitCameraToBounds({
      minimum: [0, 0, 0],
      maximum: [1000, 1000, 1000],
      size: [1000, 1000, 1000],
    });
    expect(large.distance / small.distance).toBeCloseTo(1000, 3);
  });

  it("survives a degenerate bounding box without dividing by zero", () => {
    const view = fitCameraToBounds({
      minimum: [5, 5, 5],
      maximum: [5, 5, 5],
      size: [0, 0, 0],
    });
    expect(Number.isFinite(view.distance)).toBe(true);
    expect(view.distance).toBeGreaterThan(0);
    expect(view.target).toEqual([5, 5, 5]);
  });

  it("agrees with the bounds' own size field, within tolerance", () => {
    const bounds = model().bounds;
    for (const axis of [0, 1, 2] as const) {
      expect(
        Math.abs(bounds.maximum[axis] - bounds.minimum[axis] - bounds.size[axis]),
      ).toBeLessThan(TOLERANCE_MM);
    }
  });
});

describe("the mesh description", () => {
  it("reports the backend's counts and units, and nothing computed", () => {
    const summary = describeMesh(model());
    expect(summary).toContain("plate-100x60x10-4holes");
    expect(summary).toContain("2044 triangles");
    expect(summary).toContain("2048 vertices");
    expect(summary).toContain("100 x 60 x 10 mm");
  });
});
