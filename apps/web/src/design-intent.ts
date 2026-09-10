/**
 * Design intent: the generated CAD document, read back in English.
 *
 * This module **formats and nothing else.** Every value it shows is a field
 * the backend put in the document it returned; nothing here parses the user's
 * sentence, infers a dimension, supplies a default, converts a unit or does
 * any arithmetic. If a field is absent it is omitted rather than guessed --
 * an omitted `position` means the specification's default applied, and saying
 * so is the backend's job, not this page's.
 *
 * It is deliberately separate from `app.ts` so that "what the model decided"
 * has one place to live, and so a test can read it without a DOM.
 */

/** One labelled line of a feature's description. */
export interface IntentRow {
  readonly label: string;
  readonly value: string;
}

/** One feature, as the panel shows it. */
export interface IntentFeature {
  readonly heading: string;
  readonly rows: readonly IntentRow[];
}

/** A whole document, as the panel shows it. */
export interface DesignIntent {
  readonly name: string | null;
  readonly description: string | null;
  readonly units: string;
  readonly features: readonly IntentFeature[];
}

/**
 * The human-readable name of each feature type in the V1 vocabulary.
 *
 * A type this table does not know is shown by its own name rather than
 * hidden: the page must never silently drop a feature the backend generated.
 */
const FEATURE_HEADINGS: Readonly<Record<string, string>> = {
  box: "Box",
  cylinder: "Cylinder",
  through_hole: "Through hole",
  subtract: "Subtract",
  fillet: "Fillet",
  chamfer: "Chamfer",
};

type Json = Record<string, unknown>;

function isObject(value: unknown): value is Json {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

/** A scalar as text. Never reformatted, never rounded, never scaled. */
function scalar(value: unknown): string | null {
  if (typeof value === "number" || typeof value === "string") {
    return String(value);
  }
  return null;
}

/** `{x, y, z}` as `x, y, z`, with the document's own units appended. */
function triple(value: unknown, units: string): string | null {
  if (!isObject(value)) {
    return null;
  }
  const parts = ["x", "y", "z"].map((axis) => scalar(value[axis]));
  if (parts.some((part) => part === null)) {
    return null;
  }
  return `${parts.join(", ")} ${units}`;
}

function push(
  rows: IntentRow[],
  label: string,
  value: string | null,
): void {
  if (value !== null) {
    rows.push({ label, value });
  }
}

/** A length field, shown with the document's units. */
function length(feature: Json, field: string, units: string): string | null {
  const value = scalar(feature[field]);
  return value === null ? null : `${value} ${units}`;
}

/** How each feature type describes itself. Driven by the V1 vocabulary. */
function rowsFor(feature: Json, units: string): IntentRow[] {
  const rows: IntentRow[] = [];
  const type = scalar(feature["type"]);
  const size = feature["size"];

  if (type === "box" && isObject(size)) {
    // The specification's own axis meanings: +X width, +Y depth, +Z height.
    push(rows, "Length (X)", length(size, "x", units));
    push(rows, "Width (Y)", length(size, "y", units));
    push(rows, "Height (Z)", length(size, "z", units));
    push(rows, "Position", triple(feature["position"], units));
    return rows;
  }

  if (type === "cylinder") {
    push(rows, "Diameter", length(feature, "diameter", units));
    push(rows, "Height", length(feature, "height", units));
    push(rows, "Axis", scalar(feature["axis"]));
    push(rows, "Base position", triple(feature["position"], units));
    return rows;
  }

  if (type === "through_hole") {
    push(rows, "Diameter", length(feature, "diameter", units));
    push(rows, "Axis", scalar(feature["axis"]));
    push(rows, "Position", triple(feature["position"], units));
    push(rows, "In", scalar(feature["target"]));
    return rows;
  }

  if (type === "fillet") {
    push(rows, "Radius", length(feature, "radius", units));
    push(rows, "On", scalar(feature["target"]));
    push(rows, "Edges", edges(feature["edges"]));
    return rows;
  }

  if (type === "chamfer") {
    push(rows, "Distance", length(feature, "distance", units));
    push(rows, "On", scalar(feature["target"]));
    push(rows, "Edges", edges(feature["edges"]));
    return rows;
  }

  if (type === "subtract") {
    push(rows, "From", scalar(feature["target"]));
    push(rows, "Remove", list(feature["tools"]));
    return rows;
  }

  return rows;
}

/** An edge selector, as the document states it. */
function edges(value: unknown): string | null {
  if (!isObject(value)) {
    return null;
  }
  const select = scalar(value["select"]);
  if (select === null) {
    return null;
  }
  const axis = scalar(value["axis"]);
  return axis === null ? select : `${select} (${axis})`;
}

/** A list of solid ids, as the document states them. */
function list(value: unknown): string | null {
  if (!Array.isArray(value)) {
    return null;
  }
  const items = value.map(scalar).filter((item): item is string => item !== null);
  return items.length === 0 ? null : items.join(", ");
}

/**
 * Read a generated CAD document into the panel's shape.
 *
 * Features are reported **in document order**, because the order is
 * semantic: the V1 specification evaluates the list in sequence.
 */
export function describeIntent(document: unknown): DesignIntent | null {
  if (!isObject(document)) {
    return null;
  }
  const units = scalar(document["units"]) ?? "";
  const raw = document["features"];
  const features: IntentFeature[] = [];
  if (Array.isArray(raw)) {
    for (const entry of raw) {
      if (!isObject(entry)) {
        continue;
      }
      const type = scalar(entry["type"]) ?? "feature";
      const named = FEATURE_HEADINGS[type] ?? type;
      const id = scalar(entry["id"]);
      features.push({
        heading: id === null ? named : `${named} — ${id}`,
        rows: rowsFor(entry, units),
      });
    }
  }
  return {
    name: scalar(document["name"]),
    description: scalar(document["description"]),
    units,
    features,
  };
}
