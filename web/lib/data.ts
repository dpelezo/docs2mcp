import raw from "@/data/demo.json";

export type Condition = "A" | "B" | "C" | "D";

export interface Property {
  type?: string | string[];
  description?: string;
  enum?: unknown[];
  const?: unknown;
  default?: unknown;
  items?: Property;
  anyOf?: Property[];
}

export interface Tool {
  name: string;
  description: string;
  input_schema: {
    type?: string;
    properties?: Record<string, Property>;
    required?: string[];
  };
  tokens: number;
}

export interface Summary {
  runs: number;
  passed: number;
  grounded: number;
  tool_calls: number;
  tokens: number;
  answers?: string[];
}

export interface Task {
  id: string;
  api: string;
  level: "single" | "multi" | "complex";
  question: string;
  rule: string;
  expected: string | number | string[];
  grounding: "literal" | "derived";
  outcomes: Partial<Record<Condition, Summary>>;
}

export interface Api {
  name: string;
  base_url: string;
  tools: Record<Condition, Tool[]> | null;
}

export interface Demo {
  generator: string;
  run: { model: string; repeats: number; condition_repeats: Partial<Record<Condition, number>>; max_steps: number };
  conditions: Record<Condition, string>;
  shared_apis: string[];
  apis: Record<string, Api>;
  results: { by_api: Record<string, Partial<Record<Condition, Summary>>>; tasks: Task[] };
}

export const demo = raw as unknown as Demo;
export const CONDITIONS: Condition[] = ["A", "B", "C", "D"];

export const CONDITION_NOTE: Record<Condition, string> = {
  A: "The API's own OpenAPI file, one tool per operation (docs2mcp's converter)",
  B: "docs2mcp from the docs, one tool per endpoint",
  C: "docs2mcp from the docs, task-shaped tools",
  D: "FastMCP's from_openapi on the same OpenAPI file",
};

export function rate(summary: Summary | undefined, key: "passed" | "grounded" = "passed"): string {
  if (!summary || summary.runs === 0) return "–";
  return `${((100 * summary[key]) / summary.runs).toFixed(1)}%`;
}

export function apiName(id: string): string {
  return demo.apis[id]?.name ?? id;
}
