// The update dialog: the bar only moves forward, and "already on the newest
// version" is an answer, not a failure.
//
// Reported from the installed app: "the progress bar also progresses and then
// regresses and then progresses until she reaches the end", and the updater
// "gives up an error saying that there already is an update installed".
//
// Two causes are covered here:
//   * the bar was driven by `received / total` from 450 ms polls that were not
//     serialised, so an older answer could land after a newer one;
//   * `/api/update/download` answers 409 "You are already on the newest
//     version." when the check cache is older than the release, and the dialog
//     printed that as a red error with a "Try again" button.
//
// Usage: node tests/js/update_dialog.js
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
const dialogSrc = fs.readFileSync(path.join(ROOT, "arena/web/static/js/15-update.js"), "utf8");

const results = [];
const check = (name, ok, extra = "") => { results.push([name, !!ok, extra]); };
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

const RELEASE = {
  current: "1.5.16", latest: "1.5.17", update: true, enabled: true,
  url: "https://github.com/x/y/releases/tag/v1.5.17",
  notes: "## Something new\n- a bullet",
  published_at: "2026-11-07T19:52:47Z",
  installer: { name: "Arena-Desktop-Setup-1.5.17.exe", size: 28653418,
               url: "https://github.com/x/y/releases/download/v1.5.17/Arena-Desktop-Setup-1.5.17.exe",
               digest: "sha256:00", kind: "installer" },
  install_mode: "helper", can_install: true, installed_version: "1.5.16",
};

// The poll answers arrive in this order — the fourth is older than the third,
// which is exactly the out-of-order answer that used to drag the bar back.
const SCRIPT = [
  { phase: "downloading", received: 1400000, total: 28653418, pct: 5 },
  { phase: "downloading", received: 11000000, total: 28653418, pct: 40 },
  { phase: "downloading", received: 5000000, total: 28653418, pct: 20 },
  { phase: "downloading", received: 21500000, total: 28653418, pct: 75 },
  { phase: "downloading", received: 28653418, total: 28653418, pct: 100 },
  { phase: "ready", received: 28653418, total: 28653418, pct: 100, path: "/tmp/Arena-Desktop-Setup-1.5.17.exe" },
];

async function boot({ downloadStatus = 200, downloadBody = null } = {}) {
  const dom = new JSDOM(html, { url: "http://localhost:5000/", pretendToBeVisual: true, runScripts: "dangerously" });
  const { window } = dom;
  window.matchMedia = window.matchMedia || (() => ({ matches: false, addEventListener() {}, removeEventListener() {}, addListener() {}, removeListener() {} }));
  window.scrollTo = () => {};
  window.HTMLElement.prototype.scrollIntoView = () => {};

  function json(data, status = 200) {
    return {
      ok: status < 400, status,
      headers: { get: (name) => (String(name).toLowerCase() === "content-type" ? "application/json" : null) },
      json: async () => data, text: async () => JSON.stringify(data), blob: async () => null,
    };
  }

  let polls = 0;
  const calls = [];
  const payloads = {
    "/api/status": { authenticated: true, email: "a@b.c", mode: "direct", modality: "text", engine: { ready: true }, usage: {} },
    "/api/chats": { chats: [] },
    "/api/models": { models: [], groups: [] },
    "/api/settings": { setup_done: true, theme: "dark", workspace_dir: "" },
    "/api/permissions": { pending: [], mode: "default", allowed: [], denied: [] },
    "/api/connectors": { ok: true, servers: [], presets: [], transports: {} },
    "/api/connectors/startup": { ok: true, phase: "done", total: 0, done: 0, connected: 0, failed: 0, disabled: 0, items: [] },
    "/api/workspace": { path: "", root: "" },
    "/api/update-check": RELEASE,
  };

  window.fetch = async (input, init) => {
    const url = new URL(typeof input === "string" ? input : input.url, "http://localhost:5000");
    const route = url.pathname;
    calls.push(`${route}${url.search}`);
    if (route === "/api/update/download") {
      return json(downloadBody || { ok: true, job: { phase: "downloading" }, mode: "helper" }, downloadStatus);
    }
    if (route === "/api/update/install") {
      return json({ ok: true, restarting: true, version: "1.5.17" });
    }
    if (route === "/api/update/progress") {
      const job = SCRIPT[Math.min(polls, SCRIPT.length - 1)];
      polls += 1;
      return json({ ok: true, job, mode: "helper" });
    }
    if (payloads[route]) return json(url.search.includes("force=1") ? RELEASE : payloads[route]);
    return json({ ok: true });
  };

  const errors = [];
  window.addEventListener("error", (event) => errors.push(String(event.error || event.message)));
  window.eval(appjs);
  await sleep(600);
  const doc = window.document;
  const overlay = doc.querySelector(".startup-overlay");
  if (overlay) {
    const cont = Array.from(overlay.querySelectorAll("button")).find((b) => /continue|ready/i.test(b.textContent || ""));
    if (cont) cont.click();
    await sleep(250);
  }
  return { window, doc, calls, errors, polls: () => polls };
}

function pctOf(doc) {
  const node = doc.querySelector(".update-progress-pct");
  return node ? Number(String(node.textContent).replace("%", "")) || 0 : 0;
}
function primaryLabel(doc) {
  return (doc.querySelector(".modal-update .modal-foot .button:last-child")?.textContent || "").trim();
}

async function main() {
  /* ---- 1. the bar never goes backwards --------------------------------- */
  const a = await boot();
  await sleep(300);
  const dialog = a.doc.querySelector(".modal-update");
  check("the update dialog opens on its own", Boolean(dialog));
  check("it names the new version", /Version 1\.5\.17 is available/.test(a.doc.querySelector("#update-title")?.textContent || ""),
    a.doc.querySelector("#update-title")?.textContent);
  check("it offers to download and install",
    /Download and install/.test(primaryLabel(a.doc)), primaryLabel(a.doc));

  const seen = [];
  const sampler = setInterval(() => { seen.push(pctOf(a.doc)); }, 60);

  const startButton = a.doc.querySelector(".modal-update .modal-foot .button:last-child");
  startButton.click();
  await sleep(3200);           // ~7 poll ticks at 450 ms
  clearInterval(sampler);

  const steps = seen.filter((v, i) => i === 0 || v !== seen[i - 1]);
  const monotonic = steps.every((value, i) => i === 0 || value >= steps[i - 1]);
  check("the bar only ever moves forward", monotonic, steps.join(" → "));
  check("it reaches the end", steps[steps.length - 1] === 100, String(steps[steps.length - 1]));
  check("a regressing answer is not drawn",
    !steps.some((value, i) => i > 0 && value < steps[i - 1]), steps.join(" → "));
  const downloads = a.calls.filter((call) => call.startsWith("/api/update/download"));
  check("one click starts one download", downloads.length === 1, `${downloads.length} POST(s)`);
  check("…and it asks the feed again instead of trusting the cache",
    dialogSrc.includes("API.updateDownload(true)"), "source");
  check("the finished download is ready to install",
    /Restart and update/.test(primaryLabel(a.doc)), primaryLabel(a.doc));

  // "Restart and update" must install — not start a second download, which is
  // what made the bar drop back to 1% and left two jobs writing one file.
  const beforeInstall = a.calls.filter((call) => call.startsWith("/api/update/download")).length;
  a.doc.querySelector(".modal-update .modal-foot .button:last-child").click();
  await sleep(400);
  const afterInstall = a.calls.filter((call) => call.startsWith("/api/update/download")).length;
  check("installing does not start the download again", afterInstall === beforeInstall,
    `${beforeInstall} → ${afterInstall}`);
  check("installing asks once for the install",
    a.calls.filter((call) => call.startsWith("/api/update/install")).length === 1,
    a.calls.filter((call) => call.startsWith("/api/update/install")).join(" "));
  check("the bar stays at the end while it installs", pctOf(a.doc) === 100, String(pctOf(a.doc)));
  check("no uncaught errors while the bar moved", a.errors.length === 0, a.errors.join(" | "));

  /* ---- 2. "already on the newest version" is not an error -------------- */
  const b = await boot({ downloadStatus: 409, downloadBody: { ok: false, error: "You are already on the newest version." } });
  await sleep(300);
  const btn = b.doc.querySelector(".modal-update .modal-foot .button:last-child");
  check("the second dialog opens too", Boolean(btn));
  btn.click();
  await sleep(700);
  check("no red error is shown", b.doc.querySelector(".update-error")?.hidden !== false,
    b.doc.querySelector(".update-error")?.textContent);
  const note = (b.doc.querySelector(".update-note")?.textContent || "").trim();
  check("it says the build is current", /newest release/i.test(note), note);
  check("the button stops offering a retry", /Close/.test(primaryLabel(b.doc)), primaryLabel(b.doc));
  check("the stale update was re-checked",
    b.calls.filter((call) => call.startsWith("/api/update-check")).length >= 2, b.calls.join(" "));
  check("no uncaught errors in the 409 case", b.errors.length === 0, b.errors.join(" | "));

  let failed = 0;
  for (const [name, ok, extra] of results) {
    if (!ok) failed += 1;
    console.log(`${ok ? "ok  " : "FAIL"}  ${name}${extra ? `  — ${extra}` : ""}`);
  }
  console.log(failed ? `${failed} failed` : `PASS (${results.length} checks)`);
  process.exit(failed ? 1 : 0);
}

main().catch((error) => { console.error(error); process.exit(1); });
