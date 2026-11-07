// Regression harness for the setup window's bridge hand-off.
//
// pywebview injects `window.pywebview.api` about 1.5-2 s after the page loads and
// announces it with a `pywebviewready` event on **window** (webview/js/finish.js:
// `window.dispatchEvent(new CustomEvent('pywebviewready'))`). 1.5.3 and 1.5.4
// listened on `document` and checked `window.pywebview` once at 900 ms, so the
// window never left its static "Starting…" text.
//
// Usage: node tests/js/setup_page_bridge.js arena/setup/web/index.html
// Needs jsdom (`npm i jsdom`); exits 0 on success, 1 on failure.
const fs = require("fs");
const { JSDOM } = require("jsdom");

const html = fs.readFileSync(process.argv[2], "utf8");

function run({ bridgeAt, onWindow, wait = 2600 }) {
  return new Promise((resolve) => {
    const dom = new JSDOM(html, { runScripts: "dangerously", pretendToBeVisual: true,
                                  url: "file:///setup/index.html" });
    const { window } = dom;
    const calls = [];

    // WebView2 host: this is what the page uses to tell a real setup window
    // apart from a plain browser preview.
    window.chrome = { webview: {} };

    const api = {
      info: async () => { calls.push("info"); return { mode: "install", version: "1.5.5", silent: false, install_dir: "C:\\Arena", installed_version: null }; },
      start: async () => { calls.push("start"); return { phase: "working", label: "Preparing", detail: "", progress: 0.1 }; },
      state: async () => { calls.push("state"); return { phase: "working", label: "Copying the application", detail: "5 of 28 MB", progress: 0.4 }; },
      close: async () => { calls.push("close"); return true; },
    };

    if (bridgeAt === 0) window.pywebview = { api };
    if (bridgeAt > 0) {
      setTimeout(() => {
        window.pywebview = { api };
        (onWindow ? window : window.document)
          .dispatchEvent(new window.Event("pywebviewready"));
      }, bridgeAt);
    }

    setTimeout(() => {
      const status = window.document.getElementById("statusText").textContent;
      const version = window.document.getElementById("chromeVersion").textContent;
      window.close();
      resolve({ calls, status, version });
    }, wait);
  });
}

(async () => {
  const atLoad = await run({ bridgeAt: 0 });
  const lateWindow = await run({ bridgeAt: 1200, onWindow: true });
  const lateDocument = await run({ bridgeAt: 1200, onWindow: false });
  const never = await run({ bridgeAt: -1, wait: 7000 });

  console.log("bridge at load:          ", JSON.stringify(atLoad));
  console.log("late, event on window:   ", JSON.stringify(lateWindow));
  console.log("late, event on document: ", JSON.stringify(lateDocument));
  console.log("never arrives:           ", JSON.stringify(never));

  const worked = (r) => r.calls.includes("info") && r.calls.includes("start");
  const ok =
    worked(atLoad) &&
    worked(lateWindow) &&
    worked(lateDocument) &&
    never.calls.length === 0 &&
    /background/i.test(never.status) &&
    atLoad.version.includes("1.5.5");

  console.log(ok ? "PASS" : "FAIL");
  process.exit(ok ? 0 : 1);
})();
