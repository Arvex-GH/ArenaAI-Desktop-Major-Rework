// The model's out-loud thinking goes in <thinking> blocks. They must never reach
// the answer text: the content belongs in a collapsed block at the position it
// happened, and an unclosed block must stay hidden while it streams (1.5.8 and
// earlier showed all of it as reply prose).
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

const body = [
  constant("THINKING_OPEN"),
  constant("THINKING_CLOSE"),
  constant("STEP_MARKER_LINE"),
  constant("TOOL_TAG_CLOSE"),
  "function markerLabel() { return 'tool'; }",
  "function markerDetail() { return ''; }",
  grab("readThinking"),
  grab("stripThinkingTail"),
  grab("readToolStream"),
  "exports.readToolStream = readToolStream;",
].join("\n");

const api = {};
new Function("exports", body)(api);
const { readToolStream } = api;

const checks = [];
const check = (name, ok) => checks.push([name, !!ok]);

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

const withTool = readToolStream("~~~command\n<tool name=\"run_cmd\"><cmd>dir</cmd></tool>\n~~~\n<thinking>x</thinking>done", false);
check("tool markers and thinking coexist", withTool.text.trim() === "done" && withTool.thinking.length === 1);

// Wiring: the card hands the blocks to the flow, keeps them in place, and does
// not type out a placeholder on top of a thought that is still arriving.
check("the card passes its thoughts to the flow", /lane\.thinking = view\.thinking;/.test(render));
check("a live thought suppresses the placeholder", /const thinkingLive = view\.thinking\.some/.test(render)
  && /const showTypewriter = lane\.streaming && \(!visibleText \|\| inTypewriterWindow\) && !thinkingLive;/.test(render));
check("rows and thoughts are sorted together", /const bySource = /.test(render)
  && /placed\.slice\(\)\.sort\(bySource\)/.test(render));
// While the placeholder stands in for the prose, the prose itself must not be
// poured into the card as well — that printed the first sentence twice.
const typewriterBranch = render.slice(render.indexOf("if (showTypewriter) {"),
  render.indexOf("placed.sort((a, b) => {"));
check("the placeholder hides the prose it stands for", /return nodes;/.test(typewriterBranch)
  && !/markdownNode/.test(typewriterBranch));
check("a thought renders as a collapsed block", /class: `reasoning thinking-block/.test(render));
check("no thought is hoisted above the text", !/thinking\.forEach\(\(block\) => nodes\.unshift/.test(render));

const css = src("arena/web/static/style.css");
check("thought blocks are styled", /\.thinking-block \{/.test(css) && /\.thinking-block\.is-live/.test(css));

// The instructions the model is given must ask for the blocks, or none of this
// ever happens.
const guide = src("arena/agent.py");
check("the prompt asks for <thinking>", /Think in a `<thinking>` block/.test(guide));

let failed = 0;
for (const [name, ok] of checks) {
  if (!ok) failed += 1;
  console.log(`${ok ? "ok  " : "FAIL"}  ${name}`);
}
console.log(failed ? `${failed} failed` : `PASS (${checks.length} checks)`);
process.exit(failed ? 1 : 0);
