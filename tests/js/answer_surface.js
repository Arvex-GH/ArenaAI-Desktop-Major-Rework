// The answer is not a box, and it is not a person.
//
// Reported from the app: every reply sat inside an outlined card (most visible
// while it streamed, when the border darkened), with a square avatar in front of
// the model name. Only the user's own prompt is outlined now, and the avatar in
// the answer head is gone — it stays in the model picker, where choosing needs
// something to aim at.
//
// Usage: node tests/js/answer_surface.js
const fs = require("fs");
const path = require("path");

const root = path.resolve(__dirname, "..", "..");
const src = (file) => fs.readFileSync(path.join(root, file), "utf8").replace(/\r\n/g, "\n");

const css = src("arena/web/static/style.css");
const render = src("arena/web/static/js/07-render.js");
const picker = src("arena/web/static/js/11-ui.js");

// Every declaration block whose selector list is exactly `selector` (media
// queries included, so a mobile override shows up as its own entry).
function rulesOf(selector) {
  const escaped = selector.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const pattern = new RegExp(`(^|\\n)\\s*${escaped}\\s*\\{([^}]*)\\}`, "g");
  const out = [];
  let match;
  while ((match = pattern.exec(css))) out.push(match[2]);
  return out;
}

const rule = (selector) => rulesOf(selector).join("\n");

const checks = [];
const check = (name, ok, extra = "") => checks.push([name, !!ok, extra]);

const lane = rule(".assistant-lane");
check("the answer card has no border", /border:\s*0\b/.test(lane), lane.trim().slice(0, 120));
check("the answer card has no border colour to come back", !/border-color/.test(lane));
check("the answer card sits on the page", /background:\s*transparent/.test(lane));

check("streaming no longer paints a border",
  !/\.assistant-lane\.is-streaming\s*\{[^}]*border/.test(css));
check("no later rule re-adds a border to the lane",
  !/\.assistant-lane\s*\{[^}]*border:\s*1px/.test(css));

const userBubble = rulesOf(".user-bubble");
check("the user's prompt keeps its outline",
  userBubble.some((block) => /border:\s*1px solid/.test(block)),
  userBubble.join(" | ").slice(0, 120));

const battle = rule(".lanes-2 .assistant-lane");
check("a battle keeps a surface, not a border", /background:/.test(battle) && !/border/.test(battle), battle.trim());

/* ---- the head of an answer --------------------------------------------- */

function functionSource(name) {
  const start = render.indexOf(`function ${name}(`);
  if (start < 0) return "";
  let depth = 0, i = render.indexOf("{", start);
  for (; i < render.length; i += 1) {
    if (render[i] === "{") depth += 1;
    else if (render[i] === "}") { depth -= 1; if (!depth) break; }
  }
  return render.slice(start, i + 1);
}

const laneFn = functionSource("renderAssistantLane");
check("the answer head has no avatar", !/model-avatar/.test(laneFn));
check("the model name is still there", /class: 'model-name'/.test(laneFn));
check("the generating label still shows", /renderGeneratingLabel\(lane\)/.test(laneFn));
check("the copy button still shows", /answer-copy-button/.test(laneFn));

// The picker keeps its icons: taking them away from the answer must not strip
// the place where a model is chosen by sight.
check("the model picker keeps its icons", /model-avatar/.test(picker));

/* ---- computed style, when jsdom is around ------------------------------ */
/* Text checks can be fooled by a later rule; the cascade cannot. */
let JSDOM = null;
try {
  ({ JSDOM } = require("jsdom"));
} catch {
  JSDOM = null;
}
if (JSDOM) {
  const dom = new JSDOM('<!doctype html><html><head></head><body>'
    + '<article class="message-assistant assistant-lane is-streaming" id="lane">'
    + '<div class="assistant-head"><div class="assistant-id"><span class="model-name">Agent</span></div></div>'
    + '</article>'
    + '<div class="message-user"><div class="user-bubble" id="bubble">how much space</div></div>'
    + '<div class="lane-grid lanes-2"><article class="message-assistant assistant-lane" id="battle"></article></div>'
    + '</body></html>');
  const { document } = dom.window;
  const style = document.createElement("style");
  style.textContent = css;
  document.head.appendChild(style);
  const computed = (id, prop) => dom.window.getComputedStyle(document.getElementById(id))[prop];
  check("computed: a streaming answer has no border",
    computed("lane", "borderTopWidth") === "0px" && computed("lane", "borderTopStyle") === "none",
    `${computed("lane", "borderTopWidth")} ${computed("lane", "borderTopStyle")}`);
  check("computed: a battle column has no border",
    computed("battle", "borderTopWidth") === "0px", computed("battle", "borderTopWidth"));
  check("computed: the prompt still has its 1px border",
    computed("bubble", "borderTopWidth") === "1px" && computed("bubble", "borderTopStyle") === "solid",
    `${computed("bubble", "borderTopWidth")} ${computed("bubble", "borderTopStyle")}`);
} else {
  console.log("note  jsdom is not installed — computed-style checks skipped");
}

let failed = 0;
for (const [name, ok, extra] of checks) {
  if (!ok) failed += 1;
  console.log(`${ok ? "ok  " : "FAIL"}  ${name}${extra && !ok ? `  — ${extra}` : ""}`);
}
console.log(failed ? `${failed} failed` : `PASS (${checks.length} checks)`);
process.exit(failed ? 1 : 0);
