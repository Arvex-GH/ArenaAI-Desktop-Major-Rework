// The start-up screen and the saved-account list in Settings are source-level
// contracts: the app has to connect its local MCP servers by itself while saying
// so, and the account switcher that lived in the removed left rail has to be
// reachable in Settings.
//
// Usage: node tests/js/startup_screen.js
const fs = require("fs");
const path = require("path");

const root = path.resolve(__dirname, "..", "..");
const src = (file) => fs.readFileSync(path.join(root, file), "utf8").replace(/\r\n/g, "\n");

const boot = src("arena/web/static/js/14-boot.js");
const settings = src("arena/web/static/js/12-settings.js");
const api = src("arena/web/static/js/02-api.js");
const css = src("arena/web/static/style.css");

const checks = [];
const check = (name, ok) => checks.push([name, !!ok]);

/* ---- start-up screen --------------------------------------------------- */

check("the screen exists", /function startStartupScreen\(\)/.test(boot));
check("it is mounted while the app opens", /restoreWorkspacePanelPreference\(\);\n    startStartupScreen\(\);/.test(boot));
check("it polls the connector start-up route", /API\.getConnectorStartup\(\)/.test(boot));
check("it shows a progress bar", /startup-progress-fill/.test(boot) && /aria-valuenow/.test(boot));
check("it names each connector and its state", /status === 'connecting'/.test(boot)
  && /status === 'connected'/.test(boot) && /status === 'error'/.test(boot));
check("it reports tool counts", /item\.tools \? `\$\{item\.tools\} tool/.test(boot));
// The app's own presets (Filesystem, Memory, Sequential Thinking) are installed
// into mcp.json, so they ride the same run — and keep their display names
// instead of the raw "sequential_thinking".
check("rows use the connector's display name", /entry = row\(String\(item\.label \|\| item\.name \|\| id\)\)/.test(boot));
check("a switched-off connector reads Off", /status === 'disabled'\).*entry\.set\('off', 'Off'\)/.test(boot)
  || /else if \(status === 'disabled'\) entry\.set\('off', 'Off'\)/.test(boot));
check("the summary counts the switched-off ones", /mcpOff = Number\(snap\.disabled\)/.test(boot)
  && /switched off/.test(boot));
check("it closes by itself when done", /window\.setTimeout\(\(\) => \{ if \(!closed\) close\(\); \}, 900\)/.test(boot));
check("it offers a retry when a connector failed", /API\.retryConnectorStartup\(\)/.test(boot));
check("it can be dismissed early", /Continue in the background/.test(boot));
check("escape closes it", /event\.key === 'Escape'/.test(boot));
check("demo mode skips it", /if \(state\.demo \|\| startupScreen\) return startupScreen;/.test(boot));
check("the one-time setup takes the window instead", /document\.body\.appendChild\(overlay\);\n    dismissStartupScreen\(\);/.test(boot));
check("the connectors panel refreshes after the run", /void loadConnectors\(\{ silent: true \}\)/.test(boot));
check("it is styled", /\.startup-overlay \{/.test(css) && /\.startup-progress-fill \{/.test(css)
  && /\.startup-step\.is-error/.test(css) && /\.startup-step\.is-off/.test(css));

/* ---- the routes it talks to -------------------------------------------- */

check("the api client has the start-up route", /getConnectorStartup\(\) \{ return this\.request\('\/api\/connectors\/startup'\); \}/.test(api));
check("the api client can retry", /retryConnectorStartup\(\) \{ return this\.request\('\/api\/connectors\/startup\/retry'/.test(api));

/* ---- saved accounts in Settings ---------------------------------------- */

check("settings lists the saved accounts", /const saved = \(Array\.isArray\(state\.accounts\)/.test(settings));
check("the current account is marked", /settings-account-tag/.test(settings) && /Current account/.test(settings));
check("switching reuses the account switch", /const ok = await switchToAccount\(item\);/.test(settings));
check("an expired account offers a new sign-in", /item\.expired \? 'Sign in again' : 'Switch'/.test(settings));
check("forgetting an account reuses the store", /await removeSavedAccount\(item\);/.test(settings));
check("adding a second account stays in Settings", /Add another account/.test(settings)
  && /startLogin\(\{ addAccount: true \}\)/.test(settings));
check("the list is hydrated from the server", /API\.getAccounts\(\)\.then\(\(result\) => \{/.test(settings));
check("the list is styled", /\.settings-account-row \{/.test(css) && /\.settings-account-switch \{/.test(css));

/* ---- the panel must not claim more than it does ------------------------ */

check("no logout happens just to switch", !/switchToAccount\([^)]*\)\s*;[^}]*logoutAccount/.test(settings));

let failed = 0;
for (const [name, ok] of checks) {
  if (!ok) failed += 1;
  console.log(`${ok ? "ok  " : "FAIL"}  ${name}`);
}
console.log(failed ? `${failed} failed` : `PASS (${checks.length} checks)`);
process.exit(failed ? 1 : 0);
