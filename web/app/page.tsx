import Link from "next/link";
import { CONDITION_NOTE, CONDITIONS, apiName, demo, rate, type Condition, type Summary } from "@/lib/data";

function levelRate(condition: Condition, level: string): string {
  const tasks = demo.results.tasks.filter((t) => demo.shared_apis.includes(t.api) && t.level === level);
  let runs = 0;
  let passed = 0;
  for (const task of tasks) {
    const o = task.outcomes[condition];
    if (o) {
      runs += o.runs;
      passed += o.passed;
    }
  }
  return runs ? `${Math.round((100 * passed) / runs)}%` : "–";
}

export default function Home() {
  const shared = demo.results.by_api.shared ?? {};
  const c = shared.C as Summary;
  const a = shared.A as Summary;
  const tokenCut = Math.round(100 * (1 - c.tokens / a.tokens));
  const apis = Object.keys(demo.results.by_api).filter((k) => k !== "shared");
  return (
    <>
      <h1>Docs in, task-shaped MCP server out.</h1>
      <p className="lead">
        docs2mcp reads an API&apos;s documentation, extracts a typed spec, groups endpoints into tools
        an agent can use, and generates a checked MCP server. This page shows how an agent did with
        those tools against two baselines built from the API&apos;s own OpenAPI file.
      </p>
      <div className="cards">
        <div className="card">
          <div className="big">{rate(c)}</div>
          <div className="muted small">tasks solved with docs2mcp&apos;s task-shaped tools</div>
        </div>
        <div className="card">
          <div className="big">{rate(a)}</div>
          <div className="muted small">with tools converted from the OpenAPI file</div>
        </div>
        <div className="card">
          <div className="big">{rate(shared.D)}</div>
          <div className="muted small">with FastMCP&apos;s from_openapi</div>
        </div>
        <div className="card">
          <div className="big">−{tokenCut}%</div>
          <div className="muted small">tokens per task, task-shaped against the OpenAPI conversion</div>
        </div>
      </div>
      <p className="muted small">
        {demo.run.model}, {demo.results.tasks.length} hand-written tasks with checked answers, each
        condition run {demo.run.repeats} times (FastMCP once). Headline on the three APIs every
        condition covers: {demo.shared_apis.map(apiName).join(", ")}.
      </p>

      <h2>Every condition, on the shared APIs</h2>
      <div className="scroll">
        <table>
          <thead>
            <tr>
              <th>Condition</th>
              <th className="num">Runs</th>
              <th className="num">Success</th>
              <th className="num">Grounded</th>
              <th className="num">Tool calls</th>
              <th className="num">Tokens / task</th>
              <th className="num">Complex tasks</th>
            </tr>
          </thead>
          <tbody>
            {CONDITIONS.map((k) => {
              const s = shared[k];
              return (
                <tr key={k}>
                  <td>
                    <strong>{k}</strong> {demo.conditions[k]}
                    <div className="muted small">{CONDITION_NOTE[k]}</div>
                  </td>
                  <td className="num">{s?.runs}</td>
                  <td className="num">{rate(s)}</td>
                  <td className="num">{rate(s, "grounded")}</td>
                  <td className="num">{s?.tool_calls.toFixed(2)}</td>
                  <td className="num">{s?.tokens.toLocaleString("en-US")}</td>
                  <td className="num">{levelRate(k, "complex")}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      <p className="muted small">
        Grounded: the answer appeared in a tool result the agent saw, so it did not come from the
        model&apos;s memory.
      </p>

      <h2>Every API</h2>
      <div className="scroll">
        <table>
          <thead>
            <tr>
              <th>API</th>
              {CONDITIONS.map((k) => (
                <th className="num" key={k}>{k}</th>
              ))}
              <th />
            </tr>
          </thead>
          <tbody>
            {apis.map((api) => (
              <tr key={api}>
                <td>{apiName(api)}</td>
                {CONDITIONS.map((k) => (
                  <td className="num" key={k}>{rate(demo.results.by_api[api]?.[k])}</td>
                ))}
                <td>
                  {demo.apis[api]?.tools ? <Link href={`/apis/${api}`}>tools</Link> : <span className="muted small">results only</span>}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="muted small">
        A and D exist only where the API publishes an OpenAPI file. Open Library and Open-Meteo show
        results only: their docs are AGPL, so their designs are not published.
      </p>

      <h2>The honest part</h2>
      <p>
        Task-shaped tools did best on complex tasks and used the fewest tokens. Across all six APIs
        they were level with docs2mcp&apos;s own one-tool-per-endpoint design, and behind it on Open
        Library. Most tasks were solved by every condition, so the gaps rest on a few
        tasks. The eval also found four real bugs before any result was claimed. Read the{" "}
        <a href="https://github.com/dpelezo/docs2mcp/blob/main/evals/agent/README.md">full results and limits</a>, or{" "}
        <Link href="/tasks">browse every task</Link>.
      </p>
      <footer>
        {demo.generator}. Try it: <code>uvx docs2mcp-cli --help</code>
      </footer>
    </>
  );
}
