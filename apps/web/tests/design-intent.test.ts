/**
 * Schema 1.2.0 in the design-intent panel: tapers, polygon prisms and the
 * copy-a-tool pattern of `subtract` and `union`, read back in plain English.
 *
 * Each new parameter is mentioned only when it differs from the value that
 * changes nothing, so a 1.1-style document reads exactly as it always did.
 * Every number in the expected text is a number the document itself carries.
 */

import { describe, expect, it } from "vitest";

import { describeIntent, type IntentRow } from "../src/design-intent";

function rowsOf(feature: Record<string, unknown>): readonly IntentRow[] {
  const intent = describeIntent({
    schema_version: "1.2.0",
    units: "mm",
    features: [feature],
  });
  return intent!.features[0].rows;
}

function byLabel(rows: readonly IntentRow[]): Record<string, string> {
  return Object.fromEntries(rows.map((row) => [row.label, row.value]));
}

const ORIGIN = { x: 0, y: 0, z: 0 };

function cylinder(extra: Record<string, unknown>): Record<string, unknown> {
  return {
    id: "c",
    type: "cylinder",
    diameter: 40,
    height: 30,
    position: ORIGIN,
    axis: "+Z",
    ...extra,
  };
}

describe("a 1.1-style document reads exactly as before", () => {
  // A document using every feature type of schema 1.1.1, and none of the
  // 1.2.0 parameters. Its rows are pinned in full: any new line here would
  // be a change to how existing parts are described.
  const OLD = {
    schema_version: "1.1.1",
    units: "mm",
    name: "old-part",
    features: [
      { id: "plate", type: "box", size: { x: 100, y: 60, z: 10 }, position: ORIGIN },
      {
        id: "boss",
        type: "cylinder",
        diameter: 20,
        height: 15,
        position: { x: 50, y: 30, z: 10 },
        axis: "+Z",
      },
      { id: "body", type: "union", target: "plate", tools: ["boss"] },
      { id: "pocket", type: "box", size: { x: 10, y: 10, z: 5 }, position: ORIGIN },
      { id: "cut", type: "subtract", target: "plate", tools: ["pocket"] },
      {
        id: "hole",
        type: "through_hole",
        target: "plate",
        diameter: 6,
        position: { x: 10, y: 10, z: 0 },
        axis: "+Z",
      },
      {
        id: "round",
        type: "fillet",
        target: "plate",
        radius: 2,
        edges: { select: "axis_parallel", axis: "Z" },
      },
      { id: "bevel", type: "chamfer", target: "plate", distance: 1, edges: { select: "all" } },
    ],
  };

  it("has the same headings and rows as schema 1.1.1 always produced", () => {
    const intent = describeIntent(OLD)!;
    expect(intent.features.map((feature) => [feature.heading, feature.rows])).toEqual([
      [
        "Box — plate",
        [
          { label: "Length (X)", value: "100 mm" },
          { label: "Width (Y)", value: "60 mm" },
          { label: "Height (Z)", value: "10 mm" },
          { label: "Position", value: "0, 0, 0 mm" },
        ],
      ],
      [
        "Cylinder — boss",
        [
          { label: "Diameter", value: "20 mm" },
          { label: "Height", value: "15 mm" },
          { label: "Axis", value: "+Z" },
          { label: "Base position", value: "50, 30, 10 mm" },
        ],
      ],
      [
        "Union — body",
        [
          { label: "Join", value: "boss" },
          { label: "Onto", value: "plate" },
        ],
      ],
      [
        "Box — pocket",
        [
          { label: "Length (X)", value: "10 mm" },
          { label: "Width (Y)", value: "10 mm" },
          { label: "Height (Z)", value: "5 mm" },
          { label: "Position", value: "0, 0, 0 mm" },
        ],
      ],
      [
        "Subtract — cut",
        [
          { label: "From", value: "plate" },
          { label: "Remove", value: "pocket" },
        ],
      ],
      [
        "Through hole — hole",
        [
          { label: "Diameter", value: "6 mm" },
          { label: "Axis", value: "+Z" },
          { label: "Position", value: "10, 10, 0 mm" },
          { label: "In", value: "plate" },
        ],
      ],
      [
        "Fillet — round",
        [
          { label: "Radius", value: "2 mm" },
          { label: "On", value: "plate" },
          { label: "Edges", value: "axis_parallel (Z)" },
        ],
      ],
      [
        "Chamfer — bevel",
        [
          { label: "Distance", value: "1 mm" },
          { label: "On", value: "plate" },
          { label: "Edges", value: "all" },
        ],
      ],
    ]);
  });

  it("adds nothing when the 1.2.0 parameters hold their neutral values", () => {
    // A 1.2.0 answer may spell every parameter out. Round, untapered and
    // unpatterned must still read like the plain feature it is.
    expect(rowsOf(cylinder({ top_diameter: 40, sides: 0 }))).toEqual(
      rowsOf(cylinder({})),
    );
    const plain = { id: "cut", type: "subtract", target: "a", tools: ["b"] };
    expect(
      rowsOf({
        ...plain,
        pattern_count: 1,
        pattern_axis: "Z",
        pattern_angle: 0,
        pattern_step: 0,
      }),
    ).toEqual(rowsOf(plain));
    const join = { id: "j", type: "union", target: "a", tools: ["b"] };
    expect(
      rowsOf({
        ...join,
        pattern_count: 1,
        pattern_axis: "X",
        pattern_angle: 0,
        pattern_step: 0,
      }),
    ).toEqual(rowsOf(join));
  });
});

describe("a cylinder's sides and taper", () => {
  it("names a polygon prism by its number of sides", () => {
    const rows = rowsOf(cylinder({ sides: 6 }));
    expect(rows[0]).toEqual({ label: "Shape", value: "a hexagonal prism" });
    expect(byLabel(rows)["Diameter"]).toBe("40 mm");
    expect(byLabel(rows)["Taper"]).toBeUndefined();
  });

  it("names every side count the schema allows, and shows any other as a number", () => {
    const names: Record<number, string> = {
      3: "a triangular prism",
      4: "a square prism",
      5: "a pentagonal prism",
      6: "a hexagonal prism",
      7: "a heptagonal prism",
      8: "an octagonal prism",
      9: "a nonagonal prism",
      10: "a decagonal prism",
      11: "a hendecagonal prism",
      12: "a dodecagonal prism",
      13: "a 13-sided prism",
    };
    for (const [sides, expected] of Object.entries(names)) {
      const shape = byLabel(rowsOf(cylinder({ sides: Number(sides) })))["Shape"];
      expect(shape, sides).toBe(expected);
    }
  });

  it("says where a taper runs from and to", () => {
    const rows = rowsOf(cylinder({ top_diameter: 10 }));
    expect(byLabel(rows)["Taper"]).toBe("tapering from 40 mm to 10 mm");
    // A round taper with a flat top is still a cylinder feature; the taper
    // line says everything, so there is no shape line.
    expect(byLabel(rows)["Shape"]).toBeUndefined();
    expect(rows.map((row) => row.label)).toEqual([
      "Diameter",
      "Taper",
      "Height",
      "Axis",
      "Base position",
    ]);
  });

  it("reads a taper that widens the same way", () => {
    const rows = rowsOf(cylinder({ top_diameter: 60 }));
    expect(byLabel(rows)["Taper"]).toBe("tapering from 40 mm to 60 mm");
  });

  it("calls a round taper to zero a cone", () => {
    const rows = rowsOf(cylinder({ top_diameter: 0 }));
    expect(byLabel(rows)["Shape"]).toBe("a cone");
    expect(byLabel(rows)["Taper"]).toBe("tapering from 40 mm to a point");
  });

  it("reads a tapered polygon as a tapered prism", () => {
    const rows = rowsOf(cylinder({ sides: 6, top_diameter: 20 }));
    expect(byLabel(rows)["Shape"]).toBe("a tapered hexagonal prism");
    expect(byLabel(rows)["Taper"]).toBe("tapering from 40 mm to 20 mm");
  });

  it("does not invent a base diameter the document left out", () => {
    const rows = rowsOf({ id: "c", type: "cylinder", height: 5, top_diameter: 8 });
    expect(byLabel(rows)["Taper"]).toBe("tapering to 8 mm");
    expect(byLabel(rows)["Diameter"]).toBeUndefined();
  });
});

describe("the pattern of a subtract or union", () => {
  function cut(extra: Record<string, unknown>): Record<string, unknown> {
    return { id: "cut", type: "subtract", target: "flange", tools: ["bolt"], ...extra };
  }

  it("reads a circular pattern as copies an angle apart", () => {
    const rows = rowsOf(
      cut({ pattern_count: 6, pattern_axis: "Z", pattern_angle: 60, pattern_step: 0 }),
    );
    expect(rows).toEqual([
      { label: "From", value: "flange" },
      { label: "Remove", value: "bolt" },
      { label: "Pattern", value: "6 copies, 60° apart about Z" },
    ]);
  });

  it("reads a linear pattern as copies a distance apart", () => {
    const rows = rowsOf(
      cut({ pattern_count: 4, pattern_axis: "X", pattern_angle: 0, pattern_step: 20 }),
    );
    expect(byLabel(rows)["Pattern"]).toBe("4 copies, 20 mm apart along X");
  });

  it("reads a pattern that both turns and moves with both spacings", () => {
    const rows = rowsOf(
      cut({ pattern_count: 8, pattern_axis: "Z", pattern_angle: 45, pattern_step: 5 }),
    );
    expect(byLabel(rows)["Pattern"]).toBe(
      "8 copies, 45° apart about Z and 5 mm apart along Z",
    );
  });

  it("reads a count of one with an angle as a single turn", () => {
    const rows = rowsOf({
      id: "arm",
      type: "union",
      target: "hub",
      tools: ["spoke"],
      pattern_count: 1,
      pattern_axis: "Z",
      pattern_angle: 30,
      pattern_step: 0,
    });
    expect(rows).toEqual([
      { label: "Join", value: "spoke" },
      { label: "Onto", value: "hub" },
      { label: "Placement", value: "turned 30° about Z" },
    ]);
  });

  it("reads a count of one with a step as a single move", () => {
    const rows = rowsOf(cut({ pattern_count: 1, pattern_axis: "Y", pattern_step: -12.5 }));
    expect(byLabel(rows)["Placement"]).toBe("moved -12.5 mm along Y");
    expect(byLabel(rows)["Pattern"]).toBeUndefined();
  });

  it("shows the document's own angle, sign included, and never recomputes it", () => {
    const rows = rowsOf(cut({ pattern_count: 3, pattern_axis: "Y", pattern_angle: -120 }));
    expect(byLabel(rows)["Pattern"]).toBe("3 copies, -120° apart about Y");
  });

  it("leaves an absent axis unsaid rather than guessing one", () => {
    const rows = rowsOf(cut({ pattern_count: 5, pattern_angle: 72 }));
    expect(byLabel(rows)["Pattern"]).toBe("5 copies, 72° apart");
    expect(byLabel(rowsOf(cut({ pattern_angle: 15 })))["Placement"]).toBe("turned 15°");
  });

  it("still lists copies with no spacing, rather than hiding them", () => {
    // The backend rejects this (the copies would coincide); the panel only
    // reads what it is shown, and never drops a field silently.
    const rows = rowsOf(cut({ pattern_count: 4, pattern_axis: "Z" }));
    expect(byLabel(rows)["Pattern"]).toBe("4 copies");
  });

  it("appears in the panel with the rest of the feature", () => {
    const intent = describeIntent({
      schema_version: "1.2.0",
      units: "mm",
      features: [
        cylinder({ id: "flange", diameter: 120, height: 10 }),
        cylinder({ id: "bolt", diameter: 10, height: 10, position: { x: 50, y: 0, z: 0 } }),
        cut({ pattern_count: 6, pattern_axis: "Z", pattern_angle: 60, pattern_step: 0 }),
        cylinder({ id: "nut", sides: 6, diameter: 16, height: 8 }),
      ],
    })!;
    expect(intent.features.map((feature) => feature.heading)).toEqual([
      "Cylinder — flange",
      "Cylinder — bolt",
      "Subtract — cut",
      "Cylinder — nut",
    ]);
    expect(byLabel(intent.features[2].rows)["Pattern"]).toBe(
      "6 copies, 60° apart about Z",
    );
    expect(byLabel(intent.features[3].rows)["Shape"]).toBe("a hexagonal prism");
  });
});
