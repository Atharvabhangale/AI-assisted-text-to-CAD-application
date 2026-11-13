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
 * Schema 1.2.0's optional parameters (a cylinder's `top_diameter` and
 * `sides`, a subtract's or union's `pattern_*`) get a line only when present
 * and different from the value that changes nothing, so a 1.1-style document
 * reads exactly as it always did. Naming 6 sides "hexagonal" is a lookup of
 * the number given; no position, angle or size is ever derived.
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
  union: "Union",
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
    push(rows, "Shape", cylinderShape(feature));
    push(rows, "Diameter", length(feature, "diameter", units));
    push(rows, "Taper", taper(feature, units));
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
    pushPattern(rows, feature, units);
    return rows;
  }

  if (type === "union") {
    // Reads as a sentence: join the tools onto the target, which keeps its id.
    push(rows, "Join", list(feature["tools"]));
    push(rows, "Onto", scalar(feature["target"]));
    pushPattern(rows, feature, units);
    return rows;
  }

  return rows;
}

/**
 * Whether a field is present and differs from the value that, in schema
 * 1.2.0, means "this parameter changes nothing".
 *
 * This decides only *whether to mention* a field. It never fills one in: an
 * absent field stays absent from the text, and a field equal to its neutral
 * value is not worth a line -- which is what keeps every 1.1-style document
 * described exactly as before.
 */
function differs(feature: Json, field: string, neutral: unknown): boolean {
  const value = feature[field];
  return value !== undefined && value !== null && value !== neutral;
}

/**
 * The adjective for a polygon's number of sides, by name.
 *
 * A lookup of the number the document gave, not a calculation: a side count
 * this table does not name is shown as the number itself ("13-sided").
 */
const POLYGON_NAMES: Readonly<Record<string, string>> = {
  "3": "triangular",
  "4": "square",
  "5": "pentagonal",
  "6": "hexagonal",
  "7": "heptagonal",
  "8": "octagonal",
  "9": "nonagonal",
  "10": "decagonal",
  "11": "hendecagonal",
  "12": "dodecagonal",
};

/** A cylinder's `sides` in words, or null when it is round (`0`) or absent. */
function polygon(feature: Json): string | null {
  if (!differs(feature, "sides", 0)) {
    return null;
  }
  const sides = scalar(feature["sides"]);
  if (sides === null) {
    return null;
  }
  return POLYGON_NAMES[sides] ?? `${sides}-sided`;
}

/** Whether the document gives a `top_diameter` other than `diameter`. */
function tapers(feature: Json): boolean {
  return differs(feature, "top_diameter", feature["diameter"]);
}

/** Whether the document's `top_diameter` is exactly zero: a point. */
function pointed(feature: Json): boolean {
  return tapers(feature) && feature["top_diameter"] === 0;
}

/**
 * What a cylinder feature actually is, when it is not a plain cylinder:
 * "a hexagonal prism", "a tapered hexagonal prism", "a cone". A round taper
 * that keeps a flat top needs no shape line -- its taper line says it all.
 */
function cylinderShape(feature: Json): string | null {
  const sides = polygon(feature);
  if (sides !== null) {
    if (pointed(feature)) {
      return article(`${sides} pyramid`);
    }
    return tapers(feature) ? `a tapered ${sides} prism` : article(`${sides} prism`);
  }
  return pointed(feature) ? "a cone" : null;
}

/** "a hexagonal prism", "an octagonal prism". */
function article(phrase: string): string {
  return /^[aeiou]/.test(phrase) ? `an ${phrase}` : `a ${phrase}`;
}

/** "tapering from 40 mm to 10 mm", or "... to a point" for a zero top. */
function taper(feature: Json, units: string): string | null {
  if (!tapers(feature)) {
    return null;
  }
  const top = pointed(feature)
    ? "a point"
    : length(feature, "top_diameter", units);
  if (top === null) {
    return null;
  }
  const base = length(feature, "diameter", units);
  return base === null ? `tapering to ${top}` : `tapering from ${base} to ${top}`;
}

/**
 * The copy-a-tool pattern of a `subtract` or `union`, in words.
 *
 * Every number is the document's own: `pattern_angle` is already the angle
 * between neighbouring copies and `pattern_step` the distance between them,
 * so "6 copies, 60° apart about Z" is a reading of three fields, not a
 * result. A count of 1 is a single move of the tools: "turned 30° about Z".
 * An absent `pattern_axis` is left unsaid rather than guessed.
 */
function pushPattern(rows: IntentRow[], feature: Json, units: string): void {
  const count = scalar(feature["pattern_count"]);
  const many = count !== null && differs(feature, "pattern_count", 1);
  const turns = differs(feature, "pattern_angle", 0);
  const moves = differs(feature, "pattern_step", 0);
  const axisName = scalar(feature["pattern_axis"]);
  const about = axisName === null ? "" : ` about ${axisName}`;
  const along = axisName === null ? "" : ` along ${axisName}`;
  const angle = scalar(feature["pattern_angle"]);
  const step = length(feature, "pattern_step", units);

  if (many) {
    const spacing: string[] = [];
    if (turns && angle !== null) {
      spacing.push(`${angle}° apart${about}`);
    }
    if (moves && step !== null) {
      spacing.push(`${step} apart${along}`);
    }
    const copies = `${count} copies`;
    push(
      rows,
      "Pattern",
      spacing.length === 0 ? copies : `${copies}, ${spacing.join(" and ")}`,
    );
    return;
  }

  const motion: string[] = [];
  if (turns && angle !== null) {
    motion.push(`turned ${angle}°${about}`);
  }
  if (moves && step !== null) {
    motion.push(`moved ${step}${along}`);
  }
  if (motion.length > 0) {
    push(rows, "Placement", motion.join(" and "));
  }
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
