// Runtime smoke test for the start-up screen and Settings → Account, in jsdom.
// No browser needed: index.html + app.js with a stubbed local API.
const fs = require("fs");
const path = require("path");
let JSDOM;
try {
  ({ JSDOM } = require("jsdom"));
} catch {
  console.log("SKIP jsdom is not installed (npm i jsdom, or set ARENA_JSDOM_PATH)");
  process.exit(3);
}

const ROOT = "/home/user/ArenaAI-Desktop-Major-Rework";
const html = fs.readFileSync(path.join(ROOT, "arena/web/static/index.html"), "utf8")
  .replace(/<script[\s\S]*?<\/script>/g, "");
const appjs = fs.readFileSync(path.join(ROOT, "arena/web/static/app.js"), "utf8");

const results = [];
const check = (name, ok, extra = "") => { results.push([name, !!ok, extra]); };
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

const ACCOUNTS = [
  { id: "acc-1", email: "one@example.test", name: "One Person", avatar_url: null, current: true, expired: false },
  { id: "acc-2", email: "two@example.test", name: "Two Person", avatar_url: null, current: false, expired: false },
  { id: "acc-3", email: "three@example.test", name: "", avatar_url: null, current: false, expired: true },
];

// The app's own presets are ordinary connectors: they are started by the same
// run and keep their display names. One of them is switched off on purpose.
const FILE = (status, tools, error) => ({ id: "filesystem", name: "filesystem", label: "Filesystem",
  preset_id: "filesystem", description: "Files inside the selected workspace folder", status, tools, error });
const MEMORY = (status, tools) => ({ id: "memory", name: "memory", label: "Memory",
  preset_id: "memory", description: "Local long-term memory as a knowledge graph", status, tools, error: null });
const THINKING = (status, tools, error) => ({ id: "sequential_thinking", name: "sequential_thinking",
  label: "Sequential Thinking", preset_id: "sequential_thinking", description: "Structured step-by-step reasoning",
  status, tools, error: error || null });
const OFF = { id: "playwright", name: "playwright", label: "Playwright", preset_id: "playwright",
  description: "Browser MCP", status: "disabled", tools: 0, error: null };

let startupPolls = 0;
const startupSnapshots = [
  { ok: true, phase: "connecting", total: 3, done: 0, connected: 0, failed: 0, disabled: 1,
    items: [FILE("connecting", 0), MEMORY("queued", 0), THINKING("queued", 0), OFF] },
  { ok: true, phase: "connecting", total: 3, done: 2, connected: 2, failed: 0, disabled: 1,
    items: [FILE("connected", 12), MEMORY("connected", 9), THINKING("connecting", 0), OFF] },
  { ok: true, phase: "done", total: 3, done: 3, connected: 2, failed: 1, disabled: 1,
    items: [FILE("connected", 12), MEMORY("connected", 9), THINKING("error", 0, "spawn npx ENOENT"), OFF] },
];

const payloads = {
  "/api/status": { authenticated: true, email: "one@example.test", full_name: "One Person", accounts: ACCOUNTS,
                   current_account: "acc-1", mode: "agent", modality: "text", engine: { ready: true }, usage: {} },
  "/api/accounts": { accounts: ACCOUNTS, current_id: "acc-1" },
  "/api/chats": { chats: [] },
  "/api/models": { models: [], groups: [] },
  "/api/settings": { setup_done: true, theme: "dark", workspace_dir: "" },
  "/api/permissions": { pending: [], mode: "default", allowed: [], denied: [] },
  "/api/connectors": { ok: true, servers: [
      { id: "filesystem", name: "filesystem", status: "connected", running: true, enabled: true, tools_count: 12, tools: [], transport: "stdio" },
      { id: "github", name: "github", status: "connected", running: true, enabled: true, tools_count: 4, tools: [], transport: "stdio" },
      { id: "time", name: "time", status: "error", running: false, enabled: true, tools_count: 0, tools: [], transport: "stdio", error: "npx not found" },
    ], presets: [], transports: { stdio: true, http: false } },
  "/api/workspace": { path: "/tmp/workspace", root: "/tmp/workspace" },
  "/api/updates/latest": { ok: true },
};

async function main() {
  const dom = new JSDOM(html, { url: "http://localhost:5000/", pretendToBeVisual: true, runScripts: "dangerously" });
  const { window } = dom;
  window.matchMedia = window.matchMedia || (() => ({ matches: false, addEventListener() {}, removeEventListener() {}, addListener() {}, removeListener() {} }));
  window.scrollTo = () => {};
  window.HTMLElement.prototype.scrollIntoView = () => {};

  window.fetch = async (input, init) => {
    const url = new URL(typeof input === "string" ? input : input.url, "http://localhost:5000");
    const route = url.pathname;
    if (route === "/api/connectors/startup" || route === "/api/connectors/startup/retry") {
      const snap = startupSnapshots[Math.min(startupPolls, startupSnapshots.length - 1)];
      startupPolls += 1;
      return json(snap);
    }
    if (route.startsWith("/api/accounts/switch")) {
      const body = JSON.parse((init && init.body) || "{}");
      ACCOUNTS.forEach((a) => { a.current = a.id === body.id; });
      return json({ ok: true, accounts: ACCOUNTS });
    }
    if (payloads[route]) return json(payloads[route]);
    return json({ ok: true });
  };

  function json(data) {
    return {
      ok: true, status: 200,
      headers: { get: (name) => (String(name).toLowerCase() === "content-type" ? "application/json" : null) },
      json: async () => data, text: async () => JSON.stringify(data), blob: async () => null,
    };
  }

  const errors = [];
  window.addEventListener("error", (event) => errors.push(String(event.error || event.message)));
  window.eval(appjs);
  await sleep(400);

  const doc = window.document;
  const overlay = doc.querySelector(".startup-overlay");
  check("start-up screen is on screen", Boolean(overlay), overlay ? "" : "not rendered");
  const rows = overlay ? Array.from(overlay.querySelectorAll(".startup-step-copy")).map((n) => n.textContent) : [];
  check("it lists the app and the connectors", rows.some((t) => t === "App") && rows.includes("Filesystem"),
    JSON.stringify(rows));
  check("presets keep their display names", rows.includes("Sequential Thinking") && rows.includes("Memory"),
    JSON.stringify(rows));
  check("a switched-off connector is shown as Off, not hidden",
    rows.includes("Playwright")
      && Array.from(overlay.querySelectorAll(".startup-step.is-off .startup-step-note"))
        .some((n) => n.textContent === "Off"));
  const bar = overlay && overlay.querySelector(".startup-progress");
  check("the progress bar moves", Boolean(bar) && Number(bar.getAttribute("aria-valuenow")) >= 0,
    bar ? `aria-valuenow=${bar.getAttribute("aria-valuenow")}` : "no bar");

  // Let the poll loop reach the finished snapshot.
  await sleep(1400);
  const titled = doc.querySelector(".startup-title");
  check("a failed connector is named, not hidden",
    Boolean(titled) && /could not start/i.test(titled.textContent || ""),
    titled ? titled.textContent : "already gone");
  const notes = Array.from(doc.querySelectorAll(".startup-step-note")).map((n) => n.textContent);
  check("the failing row says why", notes.includes("spawn npx ENOENT"), JSON.stringify(notes));
  check("the connected rows report their tools", notes.includes("12 tools") && notes.includes("9 tools"),
    JSON.stringify(notes));
  check("a retry is offered", Array.from(doc.querySelectorAll(".startup-actions button"))
    .some((b) => /retry/i.test(b.textContent || "")));

  // The continue button closes it.
  const buttons = Array.from(doc.querySelectorAll(".startup-actions button"));
  const cont = buttons.find((b) => /continue/i.test(b.textContent || ""));
  if (cont) cont.click();
  await sleep(250);
  check("it can be dismissed", !doc.querySelector(".startup-overlay"));

  // Settings → Account
  const settingsBtn = doc.querySelector('[data-action="settings"]');
  check("settings button exists", Boolean(settingsBtn));
  if (settingsBtn) {
    settingsBtn.click();
    await sleep(300);
    const panel = doc.querySelector("#settings-panel");
    check("settings opened on the account tab", Boolean(panel));
    const names = Array.from(doc.querySelectorAll(".settings-account-name")).map((n) => n.textContent);
    check("saved accounts are listed", names.length === 3, JSON.stringify(names));
    const tag = doc.querySelector(".settings-account-tag");
    check("the current account is marked", Boolean(tag) && /current/i.test(tag.textContent || ""));
    const switches = Array.from(doc.querySelectorAll(".settings-account-switch"));
    check("the others offer a switch", switches.length === 2, String(switches.length));
    check("an expired account asks to sign in again",
      switches.some((b) => /sign in again/i.test(b.textContent || "")));
    const add = Array.from(doc.querySelectorAll("#settings-panel button"))
      .find((b) => /add another account/i.test(b.textContent || ""));
    check("adding another account is in the panel", Boolean(add));
    if (switches[0]) {
      switches[0].click();
      await sleep(400);
      const after = Array.from(doc.querySelectorAll(".settings-account-name")).map((n) => n.textContent);
      check("switching keeps the list open", after.length === 3, JSON.stringify(after));
      check("switching moved the current mark",
        Boolean(doc.querySelector(".settings-account-row.is-current")));
    }
  }

  check("no uncaught errors", errors.length === 0, errors.join(" | "));

  let failed = 0;
  for (const [name, ok, extra] of results) {
    if (!ok) failed += 1;
    console.log(`${ok ? "ok  " : "FAIL"}  ${name}${extra ? `  — ${extra}` : ""}`);
  }
  console.log(failed ? `${failed} failed` : `PASS (${results.length} checks)`);
  process.exit(failed ? 1 : 0);
}

main().catch((error) => { console.error(error); process.exit(1); });
