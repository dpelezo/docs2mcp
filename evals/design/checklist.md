# Design review checklist

Copy this file to `reviews/<id>.md` and answer every question for one `tools.json`. Each answer is **pass** or **fail**. A fail names the tools or endpoints concerned, with one line on why. Judge from `tools.json` and the spec only, the way an agent would see them, not from what you know about the API.

- **API:**
- **tools.json:** (path, and the run it came from)
- **Reviewer and date:**

## Each tool

1. **When to use it.** From its name and description alone, an agent can tell which requests the tool is for and which it is not for.
2. **What to pass.** Each argument's name and description say what value to give. Where the format is not obvious (an id or a name, a date format, units, a comma-separated list), the description says so.
3. **One task.** The tool's routes are variants of one task, such as getting one item or listing them, or searching by different keys. Unrelated endpoints were not merged to cut the tool count.
4. **Route choice.** For each route, it is clear which arguments select it, and an agent passing the arguments the description suggests reaches the route it means to.

## The whole design

5. **No overlap.** No request would fit two tools equally well.
6. **Exclusions.** Every exclusion has a reason that holds (not JSON, not useful to an agent, duplicate of another route), and nothing an agent needs for the API's main tasks is excluded.
7. **Faithful.** Descriptions claim nothing the spec does not support: no invented limits, values, behaviour or examples.

## Verdict

- **Fit to approve:** yes only if all seven pass.
- **Failures to fix first:**
- **Notes:** (anything worth changing that is not a fail)
