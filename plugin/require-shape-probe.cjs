// require-shape-probe.cjs — how is the electron module reachable?
// Run TWO ways to compare:
//   1) ELECTRON_RUN_AS_NODE=1 (no browser process)
//   2) as the real app:  "DSH Desktop.exe" require-shape-probe.cjs <logFile> --user-data-dir=...
// Writes a plain log file so the result survives both modes.

const fs = require("node:fs");
const path = require("node:path");

const outFile = process.argv[2] || path.join(process.env.TEMP || ".", "require-shape-probe.log");
const lines = [];
function rec(label, value) {
  lines.push(`[probe] ${label}: ${value}`);
  try { fs.writeFileSync(outFile, lines.join("\n") + "\n", "utf8"); } catch { /* ignore */ }
}
function shape(mod) {
  if (mod === undefined) return "undefined";
  if (mod === null) return "null";
  let own = [];
  try { own = Object.getOwnPropertyNames(mod); } catch { /* ignore */ }
  const types = ["app", "BrowserWindow", "screen", "globalShortcut", "ipcMain", "shell"].map((k) => `${k}=${typeof (mod && mod[k])}`);
  return `type=${typeof mod} own=[${own.slice(0, 14).join(",")}] ${types.join(" ")}`;
}

rec("mode", process.env.ELECTRON_RUN_AS_NODE ? "ELECTRON_RUN_AS_NODE" : "app");
rec("versions", `electron=${process.versions.electron} node=${process.versions.node}`);
rec("typeof global require", String(typeof require));
rec("require.main", String(require.main && require.main.filename));

// 1. require("electron") from this very file
try { rec("require('electron')", shape(require("electron"))); }
catch (cause) { rec("require('electron')", "THREW " + String(cause && cause.message)); }

// 2. createRequire anchored at the file itself
try {
  const { createRequire } = require("node:module");
  rec("createRequire(__filename)", shape(createRequire(__filename)("electron")));
} catch (cause) { rec("createRequire(__filename)", "THREW " + String(cause && cause.message)); }

// 3. createRequire anchored inside the app package (app.asar)
try {
  const { createRequire } = require("node:module");
  const anchor = path.join(path.dirname(process.execPath), "resources", "app.asar", "package.json");
  rec("createRequire(app.asar/package.json)", shape(createRequire(anchor)("electron")));
} catch (cause) { rec("createRequire(app.asar/package.json)", "THREW " + String(cause && cause.message)); }

// 4. the legacy process.electronBinding / process._linkedBinding names used by real code
for (const name of ["electron_browser_app", "electron_browser_screen"]) {
  try {
    const b = process._linkedBinding(name);
    rec("_linkedBinding:" + name, `type=${typeof b} own=[${b && typeof b === "object" ? Object.getOwnPropertyNames(b).slice(0, 8).join(",") : ""}]`);
  } catch (cause) {
    rec("_linkedBinding:" + name, "THREW " + String(cause && cause.message));
  }
}
try { rec("process.electronBinding", String(typeof process.electronBinding)); } catch { /* ignore */ }

rec("done", "ok");
if (!process.env.ELECTRON_RUN_AS_NODE) {
  try { require("electron").app.exit(0); } catch { process.exit(0); }
}
