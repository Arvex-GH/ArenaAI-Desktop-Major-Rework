// A run of calls is one line you can open.
//
// Reported from the installed app: a busy turn was a wall of rows — read, read,
// write, command — with no way to see it as one paragraph of work. Consecutive
// calls are now a group: one row that counts what it did ("2 reads, 1 write, 1
// command"), open by default, folded by a click, and never spanning a thought or
// a paragraph of the answer.
//
// Usage: node tests/js/tool_groups.js
// Needs jsdom (`npm i jsdom`); exits 0 on success, 1 on failure, 3 without jsdom.
const fs = require("fs");
const path = require("path");

let JSDOM;
try {
  ({ JSDOM } = require("jsdom"));
} catch {
  console.log("SKIP jsdom is not installed (npm i jsdom, or set ARENA_JSDOM_PATH)");
  process.exit(3);
}

const root = path.resolve(__dirname, "..", "..");
// Windows checkouts can carry CRLF; every anchor below is line-based.
const src = (file) => fs.readFileSync(path.join(root, file), "utf8").replace(/\r\n/g, "\n");
const render = src("arena/web/static/js/07-render.js");
const core = src("arena/web/static/js/01-core.js");
const css = src("arena/web/static/style.css");

function grabFrom(source, name) {
  const start = source.indexOf(`function ${name}(`);
  if (start < 0) throw new Error(`missing function ${name}`);
  // Skip the parameter list first: `function el(tag, props = {})` has a brace
  // that belongs to a default value, not to the body.
  let i = source.indexOf("(", start);
  let depth = 0;
  for (; i < source.length; i += 1) {
    if (source[i] === "(") depth += 1;
    else if (source[i] === ")") { depth -= 1; if (!depth) break; }
  }
  let d = 0;
  for (i = source.indexOf("{", i); i < source.length; i += 1) {
    if (source[i] === "{") d += 1;
    else if (source[i] === "}") { d -= 1; if (!d) break; }
  }
  return source.slice(start, i + 1);
}
const grab = (name) => grabFrom(render, name);

// A const whose value spans lines: from `const X = ...` to the matching close.
function span(name, open, close, source = render) {
  const start = source.indexOf(`const ${name} = ${open}`);
  if (start < 0) throw new Error(`missing constant ${name}`);
  let depth = 0;
  let i = source.indexOf(open, start);
  for (; i < source.length; i += 1) {
    if (source[i] === open) depth += 1;
    else if (source[i] === close) { depth -= 1; if (!depth) break; }
  }
  return source.slice(start, i + 1) + ";";
}

const setConst = (name) => {
  const start = render.indexOf(`const ${name} = new Set([`);
  if (start < 0) throw new Error(`missing set ${name}`);
  const end = render.indexOf("]);", start);
  return render.slice(start, end + 3);
};

/* ---- a document and the app's own element builder ------------------------ */

const dom = new JSDOM("<!doctype html><html><body></body></html>");
global.document = dom.window.document;
global.Node = dom.window.Node;
global.window = dom.window;

const api = {};
new Function(
  "exports",
  [
    span("ICONS", "{", "}", core),
    grabFrom(core, "icon"),
    grabFrom(core, "iconEl"),
    grabFrom(core, "append"),
    grabFrom(core, "el"),
    setConst("TOOL_READ_KEYS"),
    setConst("TOOL_WRITE_KEYS"),
    setConst("TOOL_COMMAND_KEYS"),
    "const closedGroups = new Set();",
    "const openGroups = new Set();",
    "const collapsedActivities = new Set();",
    "const expandedActivities = new Set();",
    "let renders = 0;",
    "function scheduleChatRender() { renders += 1; }",
    grab("plural"),
    grab("toolKey"),
    grab("toolKindOf"),
    grab("toolGroupSummary"),
    grab("rowHasClass"),
    grab("renderToolGroup"),
    grab("onToolToggle"),
    "exports.toolKindOf = toolKindOf;",
    "exports.toolGroupSummary = toolGroupSummary;",
    "exports.renderToolGroup = renderToolGroup;",
    "exports.onToolToggle = onToolToggle;",
    "exports.onToolToggleRow = onToolToggle;",
    "exports.closedGroups = closedGroups;",
    "exports.openGroups = openGroups;",
    "exports.collapsedActivities = collapsedActivities;",
    "exports.expandedActivities = expandedActivities;",
    "exports.renders = () => renders;",
  ].join("\n"),
)(api);

const checks = [];
const check = (name, ok, extra = "") => checks.push([name, !!ok, extra]);

/* ---- what counts as what ------------------------------------------------ */

let seq = 0;
const act = (tool, extra = {}) => ({ id: `act-${(seq += 1)}`, raw: { tool }, ...extra });
const reads = ["read_file", "read", "view", "cat", "ls", "list_dir", "list_directory"];
const writes = ["write_file", "write", "create_file", "edit_file", "edit", "str_replace", "mkdir", "delete"];
const commands = ["run_cmd", "bash", "shell", "execute_command", "terminal", "run_command"];

check("reading a file is a read", reads.every((t) => api.toolKindOf(act(t)) === "read"), reads.join(","));
check("changing a file is a write", writes.every((t) => api.toolKindOf(act(t)) === "write"), writes.join(","));
check("running a command is a command", commands.every((t) => api.toolKindOf(act(t)) === "command"), commands.join(","));
check("a sandbox edit is a write, whatever the tool is called",
  api.toolKindOf({ kind: "sandbox", raw: { verb: "Edited" } }) === "write");
check("search is not mislabelled a read", api.toolKindOf(act("web_search")) === "tool");
check("an MCP tool is counted as a tool", api.toolKindOf({ raw: { mcp: true, server: "github", mcp_tool: "issues" } }) === "tool");
check("a sub-agent is a tool", api.toolKindOf({ raw: { child_id: "abc" } }) === "tool");
check("an unknown tool is still counted", api.toolKindOf(act("brand_new_thing")) === "tool");

/* ---- the line itself ---------------------------------------------------- */

const group = (tools) => tools.map((tool) => ({ activity: act(tool), node: null }));
check("the reported run reads as reported",
  api.toolGroupSummary(group(["read_file", "read_file", "write_file", "run_cmd"])) === "2 reads, 1 write, 1 command");
check("one of everything is singular",
  api.toolGroupSummary(group(["read_file", "write_file", "run_cmd"])) === "1 read, 1 write, 1 command");
check("counts come in a fixed order, not in arrival order",
  api.toolGroupSummary(group(["run_cmd", "write_file", "read_file"])) === "1 read, 1 write, 1 command");
check("what did not happen is not mentioned",
  api.toolGroupSummary(group(["run_cmd", "bash"])) === "2 commands");
check("other tools get their own count",
  api.toolGroupSummary(group(["read_file", "web_search", "fetch"])) === "1 read, 2 tools");

/* ---- the row ------------------------------------------------------------ */

function rows(count, className = "is-ok") {
  const nodes = [];
  for (let i = 0; i < count; i += 1) {
    const node = global.document.createElement("div");
    node.className = `tool-row ${className}`;
    node.setAttribute("data-activity-id", `a${i}`);
    node.appendChild(global.document.createElement("span"));
    nodes.push(node);
  }
  return nodes;
}

function items(tools, nodes) {
  return tools.map((tool, i) => ({ activity: act(tool), node: nodes[i] }));
}

const fourNodes = rows(4);
const four = items(["read_file", "read_file", "write_file", "run_cmd"], fourNodes);
const openGroup = api.renderToolGroup(four);

check("a run is one row", openGroup.className.includes("tool-group"));
check("it starts open", openGroup.classList.contains("is-open"));
check("it says what the run did",
  (openGroup.querySelector(".tool-name") || {}).textContent === "2 reads, 1 write, 1 command",
  (openGroup.querySelector(".tool-name") || {}).textContent);
check("it counts the calls for the reader",
  (openGroup.querySelector(".tool-meta") || {}).textContent === "4 calls",
  (openGroup.querySelector(".tool-meta") || {}).textContent);
check("it opens like every other row",
  openGroup.querySelector(".tool-group-line")?.getAttribute("data-group-toggle")
  && openGroup.querySelector(".tool-group-line")?.getAttribute("aria-expanded") === "true");
check("it has a chevron and a glyph",
  Boolean(openGroup.querySelector(".tool-chevron svg")) && Boolean(openGroup.querySelector(".tool-group-glyph svg")));
check("the rows behind it are the rows themselves, not copies",
  Array.from(openGroup.querySelectorAll(".tool-group-body > .tool-row")).every((n, i) => n === fourNodes[i]));
check("it says how to fold it", /fold into one line/.test(openGroup.querySelector(".tool-group-line").getAttribute("title") || ""),
  openGroup.querySelector(".tool-group-line").getAttribute("title"));

// A click on the line folds the run to itself. The same run is drawn again, so
// the group id — the first call's — has to be the same one (it is: ids come from
// the stream, not from the render).
api.onToolToggle({ target: openGroup.querySelector(".tool-group-line") });
check("clicking it asks for a re-render", api.renders() === 1);

const folded = api.renderToolGroup(four);
check("a folded run is still one line, and still says what ran",
  (folded.querySelector(".tool-name") || {}).textContent === "2 reads, 1 write, 1 command");
check("a folded run is closed", !folded.classList.contains("is-open")
  && folded.querySelector(".tool-group-line").getAttribute("aria-expanded") === "false");
check("a folded run keeps no rows in the DOM", !folded.querySelector(".tool-group-body"));
check("a folded run says how to open it", /show every call/.test(folded.querySelector(".tool-group-line").getAttribute("title") || ""));
check("folding a group does not touch a row's own state",
  api.collapsedActivities.size === 0 && api.expandedActivities.size === 0);

// And back open, from the same line.
api.onToolToggle({ target: folded.querySelector(".tool-group-line") });
const openedAgain = api.renderToolGroup(four);
check("clicking a folded line opens the rows again",
  openedAgain.classList.contains("is-open") && Boolean(openedAgain.querySelector(".tool-group-body")));

// A live run keeps its spinner in the summary rather than a call count.
const liveGroup = api.renderToolGroup(items(["run_cmd", "read_file"], rows(2, "is-run")));
check("a live run shows it is working", Boolean(liveGroup.querySelector(".tool-spinner"))
  && liveGroup.classList.contains("is-run") && !liveGroup.querySelector(".tool-meta"));
// A pending approval may not be folded away: everything else the reader can
// hide, but not a question addressed to them.
const waitingGroup = api.renderToolGroup(items(["run_cmd", "read_file"], rows(2, "is-wait")));
check("a group with a pending approval says so",
  waitingGroup.classList.contains("is-wait")
  && (waitingGroup.querySelector(".tool-meta.is-wait") || {}).textContent === "waiting",
  (waitingGroup.querySelector(".tool-meta") || {}).textContent);
check("a waiting group shows its rows instead of a spinner",
  waitingGroup.classList.contains("is-open") && Boolean(waitingGroup.querySelector(".tool-group-body"))
  && !waitingGroup.querySelector(".tool-spinner"));
api.onToolToggle({ target: waitingGroup.querySelector(".tool-group-line") });
const waitingAfterClick = api.renderToolGroup(items(["run_cmd", "read_file"], rows(2, "is-wait")));
check("…and cannot be folded while it waits",
  waitingAfterClick.classList.contains("is-open")
  && Boolean(waitingAfterClick.querySelector(".tool-group-body")));
api.onToolToggle({ target: waitingAfterClick.querySelector(".tool-group-line") });

// A single call is not a group: "1 read" over a row that already says "Read app.js"
// would say the same thing twice. renderLaneFlow is the only caller, and it only
// groups a run of two or more.
const single = rows(1);
check("one call is not wrapped", !/tool-group/.test(single[0].className)
  && /if \(run\.length > 1\) nodes\.push\(renderToolGroup\(run\)\);/.test(render));

/* ---- the flow, executed -------------------------------------------------
   renderLaneFlow is the only place that decides where a run begins and ends, so
   it is run for real here — with the rows and the markdown stubbed, and the
   grouping left exactly as the app ships it. */

const flowApi = {};
new Function("exports", [
  grabFrom(core, "icon"),
  grabFrom(core, "iconEl"),
  grabFrom(core, "append"),
  grabFrom(core, "el"),
  grab("plural"),
  grab("toolKey"),
  setConst("TOOL_READ_KEYS"),
  setConst("TOOL_WRITE_KEYS"),
  setConst("TOOL_COMMAND_KEYS"),
  "const closedGroups = new Set();",
  "const openGroups = new Set();",
  grab("toolKindOf"),
  grab("toolGroupSummary"),
  grab("rowHasClass"),
  grab("renderToolGroup"),
  "function readToolStream() { return { hidden: [] }; }",
  "function offsetMapper() { return (at) => at; }",
  "function markdownNode(text) { return el('div', { class: 'markdown', text }); }",
  "function renderThoughtRow(lane, block) { return el('div', { class: 'tool-row is-thought', text: block.body }); }",
  "function renderActivityNode(activity) { return el('div', { class: 'tool-row is-ok', 'data-activity-id': activity.id }); }",
  "function laneAwaitsApproval() { return false; }",
  "function currentToolActivity() { return null; }",
  span("ICONS", "{", "}", core),
  grab("renderLaneFlow"),
  "exports.renderLaneFlow = renderLaneFlow;",
].join("\n"))(flowApi);

// The prose of a real reply, with the four-line gap where a call happened: calls
// that follow one another share a stretch of whitespace, which is why they form
// one run instead of being split by the nothing between them.
const text = "Reading now.\n\n\n\nWriting next.\n\n\n\nRunning last.";
const flowLane = (activities, thinking) => ({
  streaming: false, text, activities, thinking, permissions: [],
});
const call = (tool, at) => ({ id: `${tool}-at-${at}`, at, raw: { tool } });
const names = (nodes) => nodes.map((n) => (n.classList.contains("tool-group") ? "group"
  : n.classList.contains("is-thought") ? "thought" : n.classList.contains("markdown") ? "text" : "other"));
const groupsOf = (nodes) => nodes.filter((n) => n.classList.contains("tool-group"));

const flowNodes = flowApi.renderLaneFlow(flowLane(
  [call("read_file", 12), call("read_file", 14), call("write_file", 29), call("run_cmd", 31)],
  [{ at: 16, open: false, body: "one" }, { at: 33, open: false, body: "two" }],
), text, false);
const flowGroups = groupsOf(flowNodes);
check("the flow makes one line per run", flowGroups.length === 2, names(flowNodes).join(","));
check("the first run counts the reads",
  (flowGroups[0]?.querySelector(".tool-name") || {}).textContent === "2 reads",
  (flowGroups[0]?.querySelector(".tool-name") || {}).textContent);
check("the second run counts the write and the command",
  (flowGroups[1]?.querySelector(".tool-name") || {}).textContent === "1 write, 1 command",
  (flowGroups[1]?.querySelector(".tool-name") || {}).textContent);
check("a thought sits between the two runs, in place",
  names(flowNodes).filter((n) => n !== "text").join(",") === "group,thought,group,thought",
  names(flowNodes).join(","));
check("the prose around the runs is untouched",
  names(flowNodes).filter((n) => n === "text").length === 3, names(flowNodes).join(","));
check("no tool row escapes its group",
  flowNodes.filter((n) => n.classList.contains("is-ok")).length === 0);

// A lone call between two thoughts is not a group.
const loneNodes = flowApi.renderLaneFlow(flowLane(
  [call("read_file", 12)],
  [{ at: 8, open: false, body: "one" }, { at: 16, open: false, body: "two" }],
), text, false);
check("one call between two thoughts stays a plain row",
  groupsOf(loneNodes).length === 0
  && loneNodes.some((n) => n.classList.contains("is-ok")), names(loneNodes).join(","));

// A paragraph of the answer splits a run as surely as a thought does.
const splitNodes = flowApi.renderLaneFlow(flowLane(
  [call("read_file", 12), call("read_file", 14), call("write_file", 29), call("run_cmd", 31)],
  [],
), text, false);
const splitNames = names(splitNodes);
check("answer text splits a run into two lines",
  groupsOf(splitNodes).length === 2
  && splitNames.indexOf("group") < splitNames.indexOf("text", splitNames.indexOf("group"))
  && splitNames.lastIndexOf("text", splitNames.lastIndexOf("group")) > splitNames.indexOf("group"),
  splitNames.join(","));

// While the typewriter stands in for the prose, the rows still group.
const liveNodes = flowApi.renderLaneFlow({
  streaming: true, text, permissions: [],
  activities: [call("read_file", 12), call("run_cmd", 14)],
  thinking: [],
}, "", true);
check("a live turn groups too",
  groupsOf(liveNodes).length === 1 && names(liveNodes).join(",") === "group", names(liveNodes).join(","));

/* ---- wiring: where a run starts and ends -------------------------------- */

const flow = grab("renderLaneFlow");
check("a run of two or more calls becomes a group", /if \(run\.length > 1\) nodes\.push\(renderToolGroup\(run\)\);/.test(flow));
check("a single call stays a plain row", /else nodes\.push\(run\[0\]\.node\);/.test(flow));
check("a paragraph of the answer ends the run",
  /if \(piece\.trim\(\)\) \{\s*flushRun\(\);/.test(flow));
check("a thought ends the run too", /else \{ flushRun\(\); nodes\.push\(rendered\); \}/.test(flow));
check("the last run is flushed before the closing text", /flushRun\(\);\s*const rest = visibleText\.slice\(cursor\);/.test(flow));
check("the typewriter branch groups as well", /forEach\(place\);\s*flushRun\(\);/.test(flow));
check("the summary line is the row everyone else uses", /class: 'tool-line tool-group-line'/.test(grab("renderToolGroup")));
check("a group toggle is not a row toggle",
  render.indexOf("data-group-toggle") < render.indexOf("'[data-tool-toggle]'"));
check("the click handler folds and reopens", /closedGroups\.add\(groupId\)/.test(render)
  && /openGroups\.add\(groupId\)/.test(render));

/* ---- the stylesheet ---------------------------------------------------- */

check("the group line is styled like a call", /\.tool-group-line \.tool-name \{/.test(css));
check("an open group turns its chevron", /\.tool-group\.is-open > \.tool-group-line \.tool-chevron \{ transform: rotate\(90deg\); \}/.test(css));
check("the rows sit one hairline in", /\.tool-group-body \{[^}]*border-left: 1px solid/.test(css));
check("a waiting group is amber, not silent", /\.tool-group\.is-wait \.tool-group-glyph \{ color: var\(--amber\); \}/.test(css));

let failed = 0;
for (const [name, ok, extra] of checks) {
  if (!ok) failed += 1;
  console.log(`${ok ? "ok  " : "FAIL"}  ${name}${extra ? `  — ${extra}` : ""}`);
}
console.log(failed ? `${failed} failed` : `PASS (${checks.length} checks)`);
process.exit(failed ? 1 : 0);
