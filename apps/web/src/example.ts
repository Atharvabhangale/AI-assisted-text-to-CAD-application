/**
 * The one preloaded document: Section D of the CAD specification.
 *
 * A copy of the specification's worked example, here only so the page has
 * something to show. It is **input data**, not CAD logic: nothing in the
 * browser interprets it, and the backend validates and builds it.
 */
export const SECTION_D_DOCUMENT = {
  schema_version: "1.0.0",
  units: "mm",
  name: "plate-100x60x10-4holes",
  description:
    "100 x 60 x 10 mm plate with four 8 mm through-holes, 10 mm from each corner",
  features: [
    {
      id: "plate",
      type: "box",
      size: { x: 100, y: 60, z: 10 },
      position: { x: 0, y: 0, z: 0 },
    },
    {
      id: "hole_front_left",
      type: "through_hole",
      target: "plate",
      diameter: 8,
      position: { x: 10, y: 10, z: 0 },
      axis: "+Z",
    },
    {
      id: "hole_front_right",
      type: "through_hole",
      target: "plate",
      diameter: 8,
      position: { x: 90, y: 10, z: 0 },
      axis: "+Z",
    },
    {
      id: "hole_back_left",
      type: "through_hole",
      target: "plate",
      diameter: 8,
      position: { x: 10, y: 50, z: 0 },
      axis: "+Z",
    },
    {
      id: "hole_back_right",
      type: "through_hole",
      target: "plate",
      diameter: 8,
      position: { x: 90, y: 50, z: 0 },
      axis: "+Z",
    },
  ],
} as const;

/** The example, formatted for the textarea. */
export const SECTION_D_JSON = JSON.stringify(SECTION_D_DOCUMENT, null, 2);
