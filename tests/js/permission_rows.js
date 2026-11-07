// Tool rows must not repeat an approval that was granted: the command is right
// there with its exit code. A refusal is the only verdict worth a line, and it
// belongs on the command's own row, in red.
//
// Usage: node tests/js/permission_rows.js
const fs = require("fs");
const path = require("path");

const root = path.resolve(__dirname, "..", "..");
const render = fs.readFileSync(path.join(root, "arena/web/static/js/07-render.js"), "utf8");
const css = fs.readFileSync(path.join(root, "arena/web/static/style.css"), "utf8");

function grab(name) {
  const start = render.indexOf(`function ${name}(`);
  if (start < 0) throw new Error(`missing function ${name}`);
  let depth = 0, i = render.indexOf("{", start);
  const from = i;
  for (; i < render.length; i += 1) {
    if (render[i] === "{") depth += 1;
    else if (render[i] === "}") { depth -= 1; if (!depth) break; }
  }
  return render.slice(start, i + 1);
}

const sandbox = {};
new Function("exports", `${grab("rejectionText")}\n${grab("permissionAllowed")}
  exports.rejectionText = rejectionText;
  exports.permissionAllowed = permissionAllowed;`)(sandbox);

const { rejectionText, permissionAllowed } = sandbox;

const checks = [];
const check = (name, ok) => checks.push([name, !!ok]);

check("refused command says so", rejectionText({ status: "denied by the user" }) === "rejected by user");
check("refusal from raw status", rejectionText({ raw: { status: "denied by the user" } }) === "rejected by user");
check("a command that ran is silent", rejectionText({ status: "ok", raw: { status: "ok" } }) === "");
check("errors are not refusals", rejectionText({ status: "error", raw: { status: "error" } }) === "");
check("plan blocks are not refusals", rejectionText({ status: "blocked: plan mode is read-only" }) === "");
check("granted approvals are allowed", ["allow", "always", "allowed"].every(permissionAllowed));
check("refusals are not allowed", !permissionAllowed("declined") && !permissionAllowed("denied"));

// Source-level: the receipt card is gone for granted approvals, and the refused
// row carries the red tag instead of the old silver "Allowed/Declined" line.
check("receipt returns nothing when allowed",
      /function renderPermissionReceipt\(permission\) \{[\s\S]*?if \(permissionAllowed\(status\)\) return null;/.test(render));
check("card loop skips allowed receipts",
      /if \(permissionAllowed\(permission\.status\)\) return;/.test(render));
check("refusal is rendered on the row",
      /rejected \? el\('span', \{ class: 'tool-reject', text: rejected \}\)/.test(render));
check("no Allowed/Declined receipts left", !/text: allowed \? 'Allowed' : 'Declined'/.test(render));
check("red tag in the stylesheet",
      /\.tool-reject \{[^}]*color: var\(--red\)/.test(css));

let failed = 0;
for (const [name, ok] of checks) {
  if (!ok) failed += 1;
  console.log(`${ok ? "ok  " : "FAIL"}  ${name}`);
}
console.log(failed ? `${failed} failed` : `PASS (${checks.length} checks)`);
process.exit(failed ? 1 : 0);
