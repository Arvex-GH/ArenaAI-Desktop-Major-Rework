// The model's out-loud thinking goes in <thinking> blocks, and its calls are
// fenced with "~~~" markers. Neither may leak into the answer: a thought gets a
// collapsed block where it happened, a call gets a row, and the prose in between
// survives — every word of it, on the right side of the row.
//
// Usage: node tests/js/thinking_blocks.js
const fs = require("fs");
const path = require("path");

const root = path.resolve(__dirname, "..", "..");
// Windows checkouts can carry CRLF; the anchors below are line-based.
const src = (file) => fs.readFileSync(path.join(root, file), "utf8").replace(/\r\n/g, "\n");

const render = src("arena/web/static/js/07-render.js");

function grab(name) {
  const start = render.indexOf(`function ${name}(`);
  if (start < 0) throw new Error(`missing function ${name}`);
  let depth = 0, i = render.indexOf("{", start);
  for (; i < render.length; i += 1) {
    if (render[i] === "{") depth += 1;
    else if (render[i] === "}") { depth -= 1; if (!depth) break; }
  }
  return render.slice(start, i + 1);
}

function constant(name) {
  const m = render.match(new RegExp(`const ${name} = (.*?);\\n`));
  if (!m) throw new Error(`missing constant ${name}`);
  return `const ${name} = ${m[1]};`;
}

// A const whose value spans lines (an object literal): from "const X = {" to the
// matching close brace.
function block(name) {
  const start = render.indexOf(`const ${name} = {`);
  if (start < 0) throw new Error(`missing object ${name}`);
  let depth = 0, i = render.indexOf("{", start);
  for (; i < render.length; i += 1) {
    if (render[i] === "{") depth += 1;
    else if (render[i] === "}") { depth -= 1; if (!depth) break; }
  }
  return render.slice(start, i + 2);
}

const body = [
  block("MARKER_LABELS"),
  constant("MARKER_RE"),
  constant("TOOL_TAG_CLOSE"),
  constant("THINKING_OPEN"),
  constant("THINKING_CLOSE"),
  "function markerLabel(kind) { return kind ? MARKER_LABELS[kind] || kind : 'Working'; }",
  "function markerDetail() { return ''; }",
  grab("readMarker"),
  grab("readThinking"),
  grab("stripThinkingTail"),
  grab("offsetMapper"),
  grab("readToolStream"),
  "exports.readToolStream = readToolStream;",
  "exports.offsetMapper = offsetMapper;",
].join("\n");

const api = {};
new Function("exports", body)(api);
const { readToolStream, offsetMapper } = api;

const checks = [];
const check = (name, ok) => checks.push([name, !!ok]);

/* ---- thoughts ---------------------------------------------------------- */

const closed = readToolStream("<thinking>The disk is 1 TB.\nPlan: report it.</thinking>The C: drive has 660 GB.", false);
check("thinking leaves the answer", closed.text.trim() === "The C: drive has 660 GB.");
check("thinking is captured", closed.thinking.length === 1 && /1 TB/.test(closed.thinking[0].body));
check("a closed block is not live", closed.thinking[0].open === false);

const live = readToolStream("Let me look.<thinking>Scanning the disks", true);
check("text before a live block stays", live.text.trim() === "Let me look.");
check("an unclosed block is hidden", live.thinking.length === 1 && live.thinking[0].open === true);
check("live body streams", live.thinking[0].body === "Scanning the disks");

const partial = readToolStream("Answer so far.<thinki", true);
check("a half-arrived tag never shows", partial.text === "Answer so far.");

const two = readToolStream("<thinking>a</thinking>one<thinking>b</thinking>two", false);
check("two blocks keep their order", two.thinking.length === 2 && two.text === "onetwo");
check("positions are recorded", two.thinking[0].at === 0 && two.thinking[1].at > 0);

const plain = readToolStream("just an answer", false);
check("a reply without thinking is untouched", plain.text === "just an answer" && plain.thinking.length === 0);

/* ---- markers ----------------------------------------------------------- */

const TOOL = (name) => `<tool name="run_cmd"><cmd>${name}</cmd></tool>`;

// The screenshot: the closing marker glued to the answer, which printed its own
// tildes and the sentence on one line.
const glued = readToolStream(
  `I'll check the disk space on your drive.\n~~~command\n${TOOL("wmic logicaldisk")}\n~~~Hi Urja, your PC has one drive.\n\n**Used:** about 207 GB`,
  false);
check("a glued closing marker is hidden", !glued.text.includes("~~~"));
check("the words after a glued marker are the answer",
  glued.text.includes("Hi Urja, your PC has one drive."));
check("the answer keeps its paragraphs", /Used:\*\* about 207 GB/.test(glued.text) || glued.text.includes("about 207 GB"));
check("a finished call is not a pending row", glued.pending === null);

// The screenshot, other lane: three calls, none of them fenced off, then the
// answer. The next opening marker used to be eaten as the previous closer, and
// the last block then hid the answer to the end of the text.
const unfenced = readToolStream([
  "Let me check your disk space.",
  "~~~command", TOOL("wmic logicaldisk get caption"), "",
  "~~~command", TOOL("powershell Get-CimInstance Win32_LogicalDisk"), "",
  "~~~command", TOOL("powershell Get-CimInstance Win32_Log"), "",
  "Your PC has one drive, C:, with about 453 GB free.",
].join("\n"), false);
check("an unfenced call does not eat the next one", unfenced.text.includes("Let me check your disk space."));
check("the last answer survives an unfenced call",
  unfenced.text.includes("Your PC has one drive, C:, with about 453 GB free."));
check("no tool markup reaches the answer", !/<tool|<\/tool>/.test(unfenced.text));
check("an unfenced turn adds no phantom row", unfenced.pending === null);

// While streaming, an unfenced call is still pending: the row has to open.
const streaming = readToolStream("~~~command\n" + TOOL("dir").slice(0, 20), true);
check("a call in flight is pending while it streams", streaming.pending !== null && streaming.pending.kind === "command");
check("an in-flight call hides to the end", !streaming.text.includes("<tool"));

// A marker with nothing after it never ran.
const empty = readToolStream("~~~command\n", false);
check("an empty call is marked unfinished", empty.pending !== null && empty.pending.unfinished === true);

// A "~~~" fence inside a file body must not end the call.
const fencedFile = readToolStream(
  "~~~write\n<tool name=\"write_file\"><path>a.md</path><content>~~~\ntext\n~~~</content></tool>\n~~~\nDone.",
  false);
check("a fence inside a file body does not end the call", fencedFile.text.trim() === "Done.");

// Ordinary prose keeps its tildes only when they are not a marker.
const stray = readToolStream("About 660 GB total.\n~~~\nDone.", false);
check("a stray marker line is hidden", stray.text.includes("About 660 GB total.")
  && stray.text.trim().endsWith("Done.") && !stray.text.includes("~~~"));

/* ---- placement --------------------------------------------------------- */

const raw = [
  "First words.",
  "~~~command", TOOL("dir"), "~~~",
  "After the call: about 207 GB free.",
  "<thinking>A note to self.</thinking>",
  "Last words.",
].join("\n");
const placed = readToolStream(raw, false);
const hiddenAt = offsetMapper(placed.hidden);
const markerAt = raw.indexOf("~~~command");
check("a call's place is measured in visible characters",
  placed.text.slice(0, hiddenAt(markerAt)).endsWith("First words.\n"));
check("the cut never lands inside a word", placed.text.slice(hiddenAt(markerAt)).trimStart()
  .startsWith("After the call: about 207 GB free."));
check("hidden ranges never overlap the visible text", hiddenAt(raw.length) === placed.text.length);

/* ---- wiring ------------------------------------------------------------ */

check("the card passes its thoughts to the flow", /lane\.thinking = view\.thinking;/.test(render));
check("a live thought suppresses the placeholder", /const thinkingLive = view\.thinking\.some/.test(render)
  && /const showTypewriter = lane\.streaming && \(!visibleText \|\| inTypewriterWindow\) && !thinkingLive;/.test(render));
check("rows and thoughts are sorted together", /const bySource = /.test(render)
  && /placed\.slice\(\)\.sort\(bySource\)/.test(render));
// A thought is a call in the interface (1.5.15): one quiet line with a chevron,
// opened while it is written, folded to "Thought for 3 seconds" when it ends —
// exactly like "Ran commands" on arena.ai.
check("a thought renders as a tool row", /class: `tool-row is-thought/.test(render));
check("a thought row opens like a call", /data-tool-toggle/.test(grab("renderThoughtRow")));
check("a live thought says Thinking…", /running \? 'Thinking\\u2026' : thoughtLabel\(seconds\)/.test(render));
check("a thought is timed, not guessed", /Thought for \$\{total\} second/.test(render)
  && /if \(!block\.open && !lane\.streaming\) return null;/.test(render));
check("no thought is hoisted above the text", !/thinking\.forEach\(\(block\) => nodes\.unshift/.test(render));
check("rows are placed with the same hiding as the text", /offsetMapper\(readToolStream\(raw, Boolean\(lane\.streaming\)\)\.hidden\)/.test(render));

// While the placeholder stands in for the prose, the prose itself must not be
// poured into the card as well — that printed the first sentence twice.
const typewriterBranch = render.slice(render.indexOf("if (showTypewriter) {"),
  render.indexOf("placed.sort((a, b) => {"));
check("the placeholder hides the prose it stands for", /return nodes;/.test(typewriterBranch)
  && !/markdownNode/.test(typewriterBranch));

const css = src("arena/web/static/style.css");
check("thought rows are styled like calls", /\.tool-row\.is-thought \{/.test(css)
  && /\.thought-text \{/.test(css) && /\.tool-chevron \{/.test(css) && /\.tool-row\.is-open > \.tool-line \.tool-chevron/.test(css));

// The instructions the model is given must ask for the blocks, or none of this
// ever happens.
const guide = src("arena/agent.py");
check("the prompt asks for <thinking>", /Think in a `<thinking>` block/.test(guide));
check("the prompt asks for the closing marker", /close the call with `~~~`|closing `~~~`/.test(guide) || /`~~~` after it/.test(guide));

/* ---- the clock --------------------------------------------------------- */

// The same block, twice: while it is open it stamps a start, when it closes it
// stamps an end, and a thought that was never seen live keeps no duration.
const clockApi = {};
let fakeNow = 1_700_000_000_000;
const FakeDate = { now: () => fakeNow };
new Function("exports", "Date", [
  "const _thoughtClocks = new WeakMap();",
  grab("thoughtSeconds"),
  grab("thoughtLabel"),
  "exports.thoughtSeconds = thoughtSeconds;",
  "exports.thoughtLabel = thoughtLabel;",
].join("\n"))(clockApi, FakeDate);

const lane = { streaming: true };
const liveThought = { at: 10, open: true, body: "Scanning the disks" };
check("an open thought has no duration yet", clockApi.thoughtSeconds(lane, liveThought) === 0);
fakeNow += 3000;
const closedThought = { at: 10, open: false, body: "Scanning the disks" };
const threeSeconds = clockApi.thoughtSeconds(lane, closedThought);
check("a closed thought is timed from its first sighting", threeSeconds === 3000);
check("…and reads like a call", clockApi.thoughtLabel(threeSeconds) === "Thought for 3 seconds");
check("one second is singular", clockApi.thoughtLabel(1000) === "Thought for 1 second");
check("never zero seconds", clockApi.thoughtLabel(120) === "Thought for 1 second");
check("minutes read naturally", clockApi.thoughtLabel(75_000) === "Thought for 1 minute 15s");
check("whole minutes drop the seconds", clockApi.thoughtLabel(120_000) === "Thought for 2 minutes");
check("a saved thought is not given a made-up duration",
  clockApi.thoughtSeconds({ streaming: false }, { at: 99, open: false, body: "old" }) === null
  && clockApi.thoughtLabel(null) === "Thought");

let failed = 0;
for (const [name, ok] of checks) {
  if (!ok) failed += 1;
  console.log(`${ok ? "ok  " : "FAIL"}  ${name}`);
}
console.log(failed ? `${failed} failed` : `PASS (${checks.length} checks)`);
process.exit(failed ? 1 : 0);
