"use client";

import { useMemo, useState } from "react";
import type { Condition, Summary, Task } from "@/lib/data";

const CONDITIONS: Condition[] = ["A", "B", "C", "D"];

function Cell({ summary }: { summary: Summary | undefined }) {
  if (!summary) return <td className="num muted">–</td>;
  const all = summary.passed === summary.runs;
  const className = all ? "pass" : summary.passed === 0 ? "fail" : "";
  return (
    <td className="num" title={(summary.answers ?? []).join("\n") || "no answer"}>
      <span className={className}>
        {summary.passed}/{summary.runs}
      </span>
      {summary.grounded < summary.passed ? <div className="muted small">not grounded</div> : null}
    </td>
  );
}

export function TaskTable({ tasks, apiNames }: { tasks: Task[]; apiNames: Record<string, string> }) {
  const [api, setApi] = useState("all");
  const [level, setLevel] = useState("all");
  const [onlyMisses, setOnlyMisses] = useState(false);
  const [query, setQuery] = useState("");
  const shown = useMemo(
    () =>
      tasks.filter((t) => {
        if (api !== "all" && t.api !== api) return false;
        if (level !== "all" && t.level !== level) return false;
        if (query && !`${t.id} ${t.question}`.toLowerCase().includes(query.toLowerCase())) return false;
        if (onlyMisses) {
          return CONDITIONS.some((k) => {
            const o = t.outcomes[k];
            return o !== undefined && o.grounded < o.runs;
          });
        }
        return true;
      }),
    [tasks, api, level, onlyMisses, query],
  );
  return (
    <>
      <div className="filters">
        <select value={api} onChange={(e) => setApi(e.target.value)} aria-label="API">
          <option value="all">All APIs</option>
          {Object.entries(apiNames).map(([id, name]) => (
            <option key={id} value={id}>{name}</option>
          ))}
        </select>
        <select value={level} onChange={(e) => setLevel(e.target.value)} aria-label="Level">
          <option value="all">All levels</option>
          <option value="single">single</option>
          <option value="multi">multi</option>
          <option value="complex">complex</option>
        </select>
        <label className="small">
          <input type="checkbox" checked={onlyMisses} onChange={(e) => setOnlyMisses(e.target.checked)} /> only tasks
          with a miss
        </label>
        <input placeholder="Search" value={query} onChange={(e) => setQuery(e.target.value)} aria-label="Search" />
        <span className="muted small">{shown.length} tasks</span>
      </div>
      <div className="scroll">
        <table>
          <thead>
            <tr>
              <th>Task</th>
              <th>Question</th>
              <th>Expected</th>
              {CONDITIONS.map((k) => (
                <th className="num" key={k}>{k}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {shown.map((t) => (
              <tr key={t.id}>
                <td className="mono small">
                  {t.id}
                  <div><span className="tag">{t.level}</span></div>
                </td>
                <td>{t.question}</td>
                <td className="small">
                  {Array.isArray(t.expected) ? t.expected.join(", ") : String(t.expected)}
                  <div className="muted">{t.rule}{t.grounding === "derived" ? ", worked out" : ""}</div>
                </td>
                {CONDITIONS.map((k) => (
                  <Cell key={k} summary={t.outcomes[k]} />
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="muted small">Hover a cell to see the answers. A blank or “–” means the condition doesn&apos;t cover that API.</p>
    </>
  );
}
