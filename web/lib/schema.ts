import type { Property } from "@/lib/data";

export type Kind = "string" | "integer" | "number" | "boolean" | "choice" | "list" | "other";

export interface Field {
  kind: Kind;
  /** The values a choice allows. */
  options: string[];
  /** A short label such as "integer" or "week | month". */
  label: string;
}

/** What kind of input a JSON Schema property needs, looking through `anyOf [X, null]`. */
export function fieldOf(property: Property): Field {
  const variants = (property.anyOf ?? [property]).filter((v) => v.type !== "null");
  const one = variants.length === 1 ? variants[0] : undefined;
  if (!one) return { kind: "other", options: [], label: "any" };
  if (one.enum || one.const !== undefined) {
    const options = (one.enum ?? [one.const]).map(String);
    return { kind: "choice", options, label: options.join(" | ") };
  }
  const type = Array.isArray(one.type) ? one.type.find((t) => t !== "null") : one.type;
  switch (type) {
    case "string":
    case "integer":
    case "number":
    case "boolean":
      return { kind: type, options: [], label: type };
    case "array":
      return one.items?.type === "string"
        ? { kind: "list", options: [], label: "list of strings" }
        : { kind: "other", options: [], label: "list" };
    default:
      return { kind: "other", options: [], label: type ?? "any" };
  }
}

/** Turn form text into the argument value, or undefined to leave it out. */
export function valueOf(field: Field, text: string): unknown {
  const trimmed = text.trim();
  if (trimmed === "") return undefined;
  switch (field.kind) {
    case "integer":
    case "number": {
      const n = Number(trimmed);
      return Number.isFinite(n) ? n : trimmed;
    }
    case "boolean":
      return trimmed === "true";
    case "list":
      return trimmed.split(",").map((s) => s.trim()).filter(Boolean);
    default:
      return trimmed;
  }
}
