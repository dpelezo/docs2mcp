"use client";

import { useMemo, useState } from "react";
import type { Tool } from "@/lib/data";
import { fieldOf, valueOf } from "@/lib/schema";

type Live = "B" | "C";

const EXAMPLES: Record<string, Record<Live, { tool: string; args: Record<string, string> }>> = {
  pokeapi: {
    B: { tool: "get_pokemon", args: { id_or_name: "pikachu" } },
    C: { tool: "pokemon", args: { id_or_name: "pikachu" } },
  },
  frankfurter: {
    B: { tool: "get_rate", args: { base: "EUR", quote: "USD", date: "2024-01-15" } },
    C: { tool: "exchange_rates", args: { base: "EUR", quote: "USD", date: "2024-01-15" } },
  },
  postcodes_io: {
    B: { tool: "lookup_postcode", args: { postcode: "SW1A 1AA" } },
    C: { tool: "postcode_lookup", args: { postcode: "SW1A 1AA" } },
  },
};

const LABEL: Record<Live, string> = { B: "B. docs2mcp direct", C: "C. docs2mcp task-shaped" };

interface Result {
  ok: boolean;
  is_error?: boolean;
  text?: string;
  ms?: number;
  error?: string;
}

function pretty(text: string): string {
  try {
    return JSON.stringify(JSON.parse(text), null, 2);
  } catch {
    return text;
  }
}

export function TryTool({
  api,
  tools,
  callable,
}: {
  api: string;
  tools: Record<Live, Tool[]>;
  callable: Record<Live, string[]>;
}) {
  const [condition, setCondition] = useState<Live>("C");
  const example = EXAMPLES[api]?.[condition];
  const choices = useMemo(
    () => tools[condition].filter((t) => callable[condition].includes(t.name)),
    [tools, callable, condition],
  );
  const [name, setName] = useState(example?.tool ?? choices[0]?.name ?? "");
  const [values, setValues] = useState<Record<string, string>>(example?.args ?? {});
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<Result | null>(null);
  const tool = choices.find((t) => t.name === name) ?? choices[0];

  function pickCondition(next: Live) {
    setCondition(next);
    const ex = EXAMPLES[api]?.[next];
    setName(ex?.tool ?? tools[next][0]?.name ?? "");
    setValues(ex?.args ?? {});
    setResult(null);
  }

  function pickTool(next: string) {
    setName(next);
    setValues({});
    setResult(null);
  }

  async function run() {
    if (!tool) return;
    const properties = tool.input_schema.properties ?? {};
    const args: Record<string, unknown> = {};
    for (const [key, property] of Object.entries(properties)) {
      const value = valueOf(fieldOf(property), values[key] ?? "");
      if (value !== undefined) args[key] = value;
    }
    setBusy(true);
    setResult(null);
    try {
      const response = await fetch("/api/call", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ api, condition, tool: tool.name, arguments: args }),
      });
      const isJson = response.headers.get("content-type")?.includes("application/json");
      setResult(
        isJson
          ? ((await response.json()) as Result)
          : { ok: false, error: `The live endpoint is not available here (HTTP ${response.status}).` },
      );
    } catch {
      setResult({ ok: false, error: "Could not reach the live endpoint." });
    } finally {
      setBusy(false);
    }
  }

  if (!tool) return null;
  const properties = Object.entries(tool.input_schema.properties ?? {});
  const required = new Set(tool.input_schema.required ?? []);
  return (
    <section className="try">
      <h2>Try a tool, live</h2>
      <p className="muted small">
        Runs the generated server&apos;s tool over MCP against the real API, exactly as the
        eval&apos;s agent called it. No model is involved. Only read-only (GET) tools, and a few
        calls a minute.
      </p>
      <div className="filters">
        <select value={condition} onChange={(e) => pickCondition(e.target.value as Live)} aria-label="Condition">
          {(["C", "B"] as const).map((c) => (
            <option key={c} value={c}>{LABEL[c]}</option>
          ))}
        </select>
        <select value={tool.name} onChange={(e) => pickTool(e.target.value)} aria-label="Tool">
          {choices.map((t) => (
            <option key={t.name} value={t.name}>{t.name}</option>
          ))}
        </select>
      </div>
      <p className="small">{tool.description}</p>
      <form
        className="args"
        onSubmit={(e) => {
          e.preventDefault();
          void run();
        }}
      >
        {properties.map(([key, property]) => {
          const field = fieldOf(property);
          if (field.kind === "other") return null;
          const id = `arg-${key}`;
          const value = values[key] ?? "";
          const set = (v: string) => setValues((old) => ({ ...old, [key]: v }));
          return (
            <label key={key} htmlFor={id}>
              <span className="mono small">
                {key}
                {required.has(key) ? " *" : ""} <span className="muted">{field.label}</span>
              </span>
              {field.kind === "choice" || field.kind === "boolean" ? (
                <select id={id} value={value} onChange={(e) => set(e.target.value)}>
                  <option value="">(not set)</option>
                  {(field.kind === "boolean" ? ["true", "false"] : field.options).map((o) => (
                    <option key={o} value={o}>{o}</option>
                  ))}
                </select>
              ) : (
                <input
                  id={id}
                  value={value}
                  maxLength={200}
                  inputMode={field.kind === "integer" || field.kind === "number" ? "decimal" : undefined}
                  placeholder={property.description ?? ""}
                  required={required.has(key)}
                  onChange={(e) => set(e.target.value)}
                />
              )}
            </label>
          );
        })}
        <button type="submit" disabled={busy}>
          {busy ? "Calling…" : `Call ${tool.name}`}
        </button>
      </form>
      {result ? (
        result.ok ? (
          <div className="result">
            <p className="small">
              <span className={result.is_error ? "fail" : "pass"}>
                {result.is_error ? "Tool error" : "OK"}
              </span>{" "}
              <span className="muted">in {result.ms} ms · {(result.text ?? "").length.toLocaleString("en-US")} characters, the most an agent gets is 20,000</span>
            </p>
            <pre>{pretty(result.text ?? "")}</pre>
          </div>
        ) : (
          <p className="fail small">{result.error}</p>
        )
      ) : null}
    </section>
  );
}
