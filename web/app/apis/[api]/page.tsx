import Link from "next/link";
import { notFound } from "next/navigation";
import { TryTool } from "@/components/try-tool";
import { CONDITION_NOTE, CONDITIONS, apiName, demo, rate, type Tool } from "@/lib/data";
import { fieldOf } from "@/lib/schema";
import manifest from "@/api/_servers/manifest.json";

export function generateStaticParams() {
  return demo.shared_apis.map((api) => ({ api }));
}

export const dynamicParams = false;

function Parameters({ tool }: { tool: Tool }) {
  const properties = Object.entries(tool.input_schema.properties ?? {});
  if (!properties.length) return <p className="muted">No arguments.</p>;
  const required = new Set(tool.input_schema.required ?? []);
  return (
    <ul>
      {properties.map(([name, p]) => (
        <li key={name}>
          <code>{name}</code>
          {required.has(name) ? " (required)" : ""}
          <span className="muted"> {fieldOf(p).label}</span>
        </li>
      ))}
    </ul>
  );
}

export default async function ApiTools({ params }: { params: Promise<{ api: string }> }) {
  const { api } = await params;
  const entry = demo.apis[api];
  if (!entry?.tools) notFound();
  const tools = entry.tools;
  const results = demo.results.by_api[api] ?? {};
  return (
    <>
      <p className="small">
        {demo.shared_apis.map((id) => (
          <span key={id}>
            {id === api ? <strong>{apiName(id)}</strong> : <Link href={`/apis/${id}`}>{apiName(id)}</Link>}{" "}
            {"  "}
          </span>
        ))}
      </p>
      <h1>{entry.name}: the tools an agent sees</h1>
      <p className="lead">
        The same API, four ways. Each column is the tool list the eval&apos;s agent received over MCP,
        with estimated tokens for the whole list, and how often it solved this API&apos;s 20 tasks.
      </p>
      <TryTool
        api={api}
        tools={{ B: tools.B, C: tools.C }}
        callable={(manifest as Record<string, { B: string[]; C: string[] }>)[api] ?? { B: [], C: [] }}
      />
      <h2>All four tool lists</h2>
      <div className="cols">
        {CONDITIONS.map((k) => {
          const list = tools[k];
          const total = list.reduce((sum, t) => sum + t.tokens, 0);
          return (
            <section className="col" key={k}>
              <h3>
                {k}. {demo.conditions[k]}
              </h3>
              <div className="muted small">{CONDITION_NOTE[k]}</div>
              <p className="small">
                <strong>{list.length}</strong> tools · ~{total.toLocaleString("en-US")} tokens ·{" "}
                <span className="tag">{rate(results[k])} solved</span>
              </p>
              {list.map((tool) => (
                <details className="tool" key={tool.name}>
                  <summary>{tool.name}</summary>
                  <p>{tool.description || <span className="muted">No description.</span>}</p>
                  <Parameters tool={tool} />
                </details>
              ))}
            </section>
          );
        })}
      </div>
    </>
  );
}
