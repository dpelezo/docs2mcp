import { TaskTable } from "@/components/task-table";
import { demo } from "@/lib/data";

export const metadata = { title: "Tasks · docs2mcp playground" };

export default function Tasks() {
  return (
    <>
      <h1>Every task</h1>
      <p className="lead">
        {demo.results.tasks.length} hand-written questions with answers checked against the live APIs
        and scored by fixed rules, never by a model. Each cell shows how many runs passed, and the
        answers given.
      </p>
      <TaskTable tasks={demo.results.tasks} apiNames={Object.fromEntries(Object.entries(demo.apis).map(([k, v]) => [k, v.name]))} />
    </>
  );
}
