// The composer as a two-button toolbar, in jsdom (no browser needed).
//
// 1.5.15 moved everything the composer used to spell out — mode, work type,
// permissions, folder — into one "Chat options" panel behind an icon, and put
// the two Agent backends in it as well, so choosing Local or Cloud no longer
// means three clicks into Settings.
//
// Usage: node tests/js/composer_menu_dom.js
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

const ROOT = path.resolve(__dirname, "..", "..");
const html = fs.readFileSync(path.join(ROOT, "arena/web/static/index.html"), "utf8")
  .replace(/<script[\s\S]*?<\/script>/g, "");
const appjs = fs.readFileSync(path.join(ROOT, "arena/web/static/app.js"), "utf8");
const css = fs.readFileSync(path.join(ROOT, "arena/web/static/style.css"), "utf8");

const results = [];
const check = (name, ok, extra = "") => { results.push([name, !!ok, extra]); };
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

const payloads = {
  "/api/status": { authenticated: true, email: "one@example.test", full_name: "One Person",
                   mode: "direct", modality: "text", engine: { ready: true }, usage: {} },
  "/api/chats": { chats: [] },
  "/api/models": { models: [{ id: "m1", name: "Model One", org: "Org", modalities: ["text"] }], groups: [] },
  "/api/settings": { setup_done: true, theme: "dark", workspace_dir: "/home/one/project" },
  "/api/permissions": { pending: [], mode: "default", allowed: [], denied: [] },
  "/api/connectors": { ok: true, servers: [], presets: [], transports: { stdio: true, http: false } },
  "/api/connectors/startup": { ok: true, phase: "done", total: 0, done: 0, connected: 0,
                               failed: 0, disabled: 0, items: [] },
  "/api/workspace": { path: "/home/one/project", root: "/home/one/project", chosen: true },
  "/api/updates/latest": { ok: true },
};

async function main() {
  const dom = new JSDOM(html, { url: "http://localhost:5000/", pretendToBeVisual: true, runScripts: "dangerously" });
  const { window } = dom;
  window.matchMedia = window.matchMedia || (() => ({ matches: false, addEventListener() {}, removeEventListener() {}, addListener() {}, removeListener() {} }));
  window.scrollTo = () => {};
  window.HTMLElement.prototype.scrollIntoView = () => {};

  function json(data) {
    return {
      ok: true, status: 200,
      headers: { get: (name) => (String(name).toLowerCase() === "content-type" ? "application/json" : null) },
      json: async () => data, text: async () => JSON.stringify(data), blob: async () => null,
    };
  }
  const posted = [];
  window.fetch = async (input, init) => {
    const url = new URL(typeof input === "string" ? input : input.url, "http://localhost:5000");
    const route = url.pathname;
    if (init && init.method && String(init.method).toUpperCase() === "POST") {
      let body = null;
      try { body = init.body ? JSON.parse(init.body) : null; } catch { body = init.body; }
      posted.push([route, body]);
    }
    if (payloads[route]) return json(payloads[route]);
    return json({ ok: true });
  };

  const errors = [];
  window.addEventListener("error", (event) => errors.push(String(event.error || event.message)));
  window.eval(appjs);
  await sleep(500);

  const doc = window.document;
  const overlay = doc.querySelector(".startup-overlay");
  if (overlay) {
    const cont = Array.from(overlay.querySelectorAll("button")).find((b) => /continue|ready/i.test(b.textContent || ""));
    if (cont) cont.click();
    await sleep(250);
  }
  if (!doc.querySelector(".startup-overlay")) {
    const skip = doc.querySelector(".startup-skip");
    if (skip) skip.click();
    await sleep(150);
  }

  /* ---- the toolbar is two icons ---------------------------------------- */

  const menuButton = doc.querySelector("#composerMenuButton");
  const attachButton = doc.querySelector("#attachButton");
  check("there is a Chat options button", Boolean(menuButton));
  check("Attach is still there", Boolean(attachButton));
  check("both controls are icons, not text",
    Boolean(menuButton && !menuButton.textContent.trim() && !attachButton.textContent.trim()),
    `${menuButton?.textContent} / ${attachButton?.textContent}`);
  check("each icon has a tooltip", Boolean(menuButton?.getAttribute("title")) && Boolean(attachButton?.getAttribute("title")),
    `${menuButton?.getAttribute("title")} | ${attachButton?.getAttribute("title")}`);
  check("each icon has an accessible name", Boolean(menuButton?.getAttribute("aria-label")) && Boolean(attachButton?.getAttribute("aria-label")));
  check("the menu icon is actually drawn",
    (menuButton?.querySelectorAll("svg circle").length || 0) === 3,
    String(menuButton?.innerHTML || "").slice(0, 80));
  check("Attach still shows a plus", Boolean(attachButton?.querySelector("svg path, svg line")));
  check("no text chip is left in the toolbar",
    !doc.querySelector(".composer-toolbar > .composer-chip")
    && !doc.querySelector(".composer-toolbar > .send-cluster .permission-chip"));
  const stash = doc.querySelector(".toolbar-stash");
  check("the old chips are stashed, not drawn", Boolean(stash) && stash.hasAttribute("hidden")
    && /\.toolbar-stash \{ display: none !important; \}/.test(css));
  check("the bypass dot still sits by Send",
    Boolean(doc.querySelector('.send-cluster #bypassIndicator')),
    "bypass indicator not inside .send-cluster");

  /* ---- the panel -------------------------------------------------------- */

  menuButton.click();
  await sleep(120);
  let popover = doc.querySelector(".popover");
  check("the menu opens", Boolean(popover));
  const titles = Array.from(doc.querySelectorAll(".cm-title")).map((n) => n.textContent);
  check("it holds every setting the chips used to",
    ["Chat", "Agent", "Work type", "Permissions", "Folder", "Options"].every((t) => titles.includes(t)),
    JSON.stringify(titles));

  const pillLabels = () => Array.from(doc.querySelectorAll(".cm-pill")).map((b) => (b.textContent || "").trim());
  check("the two Agent backends are a choice in here",
    pillLabels().includes("Local agent") && pillLabels().includes("Cloud agent"), JSON.stringify(pillLabels()));
  const agentPillTitle = Array.from(doc.querySelectorAll(".cm-pill"))
    .find((b) => /local agent/i.test(b.textContent || ""))?.getAttribute("title") || "";
  check("a pill explains itself on hover", /local agent/i.test(agentPillTitle), agentPillTitle);

  /* ---- switching to the Local agent, from here -------------------------- */

  window.localStorage.removeItem("ap-agent-kind");
  const localPill = () => Array.from(doc.querySelectorAll(".cm-pill")).find((b) => /local agent/i.test(b.textContent || ""));
  localPill().click();
  await sleep(200);
  check("Local agent is remembered for next launch",
    window.localStorage.getItem("ap-agent-kind") === "local",
    String(window.localStorage.getItem("ap-agent-kind")));
  check("the chat is now Agent Mode", (doc.querySelector("#modeLabel")?.textContent || "").trim() === "Agent",
    doc.querySelector("#modeLabel")?.textContent);
  check("the choice is marked in the panel",
    localPill()?.getAttribute("aria-pressed") === "true");
  check("the panel stays open while you change more than one thing",
    doc.querySelector(".popover") === popover && Boolean(doc.querySelector(".composer-menu")));

  const cloudPill = () => Array.from(doc.querySelectorAll(".cm-pill")).find((b) => /cloud agent/i.test(b.textContent || ""));
  cloudPill().click();
  await sleep(150);
  check("Cloud agent is a click away too", window.localStorage.getItem("ap-agent-kind") === "native",
    String(window.localStorage.getItem("ap-agent-kind")));

  /* ---- permissions ------------------------------------------------------ */

  check("permissions are pills, Bypass included", pillLabels().includes("Bypass"), JSON.stringify(pillLabels()));
  const bypassPill = () => Array.from(doc.querySelectorAll(".cm-pill")).find((b) => (b.textContent || "").trim() === "Bypass");
  check("the dangerous one is marked as dangerous",
    bypassPill()?.classList.contains("is-danger"));
  bypassPill().click();
  await sleep(200);
  check("bypass is now the mode", (doc.querySelector("#permissionLabel")?.textContent || "").trim() === "Bypass",
    doc.querySelector("#permissionLabel")?.textContent);
  check("bypass shows next to Send", doc.querySelector("#bypassIndicator")?.hidden === false);
  check("the menu button carries the dot", doc.querySelector("#composerMenuDot")?.hidden === false);
  check("…and says so in its tooltip", /bypass is on/i.test(menuButton.getAttribute("title") || ""),
    menuButton.getAttribute("title"));

  /* ---- back to a chat mode --------------------------------------------- */

  const battlePill = () => Array.from(doc.querySelectorAll(".cm-pill")).find((b) => /battle/i.test(b.textContent || ""));
  battlePill().click();
  await sleep(200);
  check("a mode is one click from the same panel",
    /Battle/.test(doc.querySelector("#modeLabel")?.textContent || ""),
    doc.querySelector("#modeLabel")?.textContent);
  check("the work type came back with it", Boolean(Array.from(doc.querySelectorAll(".cm-title")).find((n) => n.textContent === "Work type")));

  // Escape closes it.
  doc.dispatchEvent(new window.KeyboardEvent("keydown", { key: "Escape", bubbles: true }));
  await sleep(120);
  check("Escape closes the panel", !doc.querySelector(".popover"));

  /* ---- Settings no longer owns permissions ------------------------------ */

  const settingsBtn = doc.querySelector('[data-action="settings"]');
  if (settingsBtn) {
    settingsBtn.click();
    await sleep(300);
    const tabs = Array.from(doc.querySelectorAll(".settings-tabs [role='tab'], .settings-tabs button"))
      .map((n) => (n.textContent || "").trim());
    check("Settings has no Permissions panel any more", !tabs.includes("Permissions"), JSON.stringify(tabs));
    check("…and no 'Agent for new chats' in it",
      !/Agent for new chats/.test(doc.querySelector("#settings-panel")?.textContent || ""));
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
