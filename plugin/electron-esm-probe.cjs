// electron-esm-probe.cjs — run as a REAL Electron main process to test how the
// electron API is reachable in DSH Desktop 2.0.9's runtime (Electron in the
// packaged app). Critical distinction: `ELECTRON_RUN_AS_NODE=1` has no browser
// process, so `import("electron")` legitimately yields an empty namespace there.
// This probe therefore runs as the app itself with an isolated --user-data-dir.
//
//   "DSH Desktop.exe" electron-esm-probe.cjs <logFile>
//
// Writes plain-text results; no dependency on the DSH logger.

const fs = require("node:fs");
const path = require("node:path");

const outFile = process.argv[2] || path.join(process.env.TEMP || ".", "electron-esm-probe.log");
const lines = [];
function rec(label, value) {
  lines.push(`[probe] ${label}: ${value}`);
  try { fs.writeFileSync(outFile, lines.join("\n") + "\n", "utf8"); } catch { /* ignore */ }
}
function describeModule(mod) {
  const own = mod && typeof mod === "object" ? Object.getOwnPropertyNames(mod) : [];
  return `type=${typeof mod} keys=[${own.slice(0, 12).join(",")}] app=${typeof (mod && mod.app)} BrowserWindow=${typeof (mod && mod.BrowserWindow)} screen=${typeof (mod && mod.screen)} globalShortcut=${typeof (mod && mod.globalShortcut)}`;
}

(async () => {
  rec("versions", `electron=${process.versions.electron} node=${process.versions.node} chrome=${process.versions.chrome}`);
  rec("mainModule", typeof require.main);
  rec("argv1", String(process.argv[1]));

  // 1. dynamic ESM import (what the plugin uses)
  try {
    const esm = await import("electron");
    rec("esm-import", describeModule(esm));
    rec("esm-import.default", describeModule(esm.default));
    rec("esm-import['module.exports']", describeModule(esm["module.exports"]));
  } catch (cause) {
    rec("esm-import", "THREW " + String(cause && cause.message));
  }

  // 2. CommonJS require
  try {
    const cjs = require("electron");
    rec("cjs-require", describeModule(cjs));
  } catch (cause) {
    rec("cjs-require", "THREW " + String(cause && cause.message));
  }

  // 3. what the electron-wrapped module actually exposes
  try {
    const { createRequire } = require("node:module");
    const req = createRequire(__filename);
    const cjs2 = req("electron");
    rec("createRequire", describeModule(cjs2));
  } catch (cause) {
    rec("createRequire", "THREW " + String(cause && cause.message));
  }

  // 4. linked bindings (always available in an Electron process)
  for (const binding of ["electron_browser_app", "electron_browser_browser_window", "electron_browser_screen", "electron_browser_global_shortcut"]) {
    try {
      const b = process._linkedBinding(binding);
      rec("linkedBinding:" + binding, `type=${typeof b} keys=[${Object.getOwnPropertyNames(b || {}).slice(0, 6).join(",")}]`);
    } catch (cause) {
      rec("linkedBinding:" + binding, "THREW " + String(cause && cause.message));
    }
  }

  // 5. end-to-end: can a panel window actually be created and a hotkey registered?
  try {
    const electron = require("electron");
    const { app, BrowserWindow, screen, globalShortcut } = electron;
    if (!app) throw new Error("no app export from require('electron')");
    rec("app.isReady", String(app.isReady()));
    await app.whenReady();
    rec("whenReady", "resolved");
    const area = screen.getPrimaryDisplay().workArea;
    rec("primaryWorkArea", JSON.stringify(area));
    const win = new BrowserWindow({ x: area.x + area.width - 60, y: area.y, width: 60, height: area.height, frame: false, show: false, skipTaskbar: true });
    rec("BrowserWindow", "created id=" + win.id);
    win.show();
    win.hide();
    rec("window show/hide", "ok, isVisible=" + win.isVisible());
    const reg = globalShortcut.register("Control+Alt+F9", () => {});
    rec("globalShortcut.register", "returned " + reg + " isRegistered=" + globalShortcut.isRegistered("Control+Alt+F9"));
    const regTaken = globalShortcut.register("Control+Shift+E", () => {});
    rec("globalShortcut Control+Shift+E", "returned " + regTaken + " isRegistered=" + globalShortcut.isRegistered("Control+Shift+E"));
    globalShortcut.unregisterAll();
    win.destroy();
  } catch (cause) {
    rec("panel-e2e", "THREW " + String(cause && cause.stack ? cause.stack.split("\n").slice(0, 4).join(" | ") : cause));
  }

  rec("done", "exiting");
  try { require("electron").app.exit(0); } catch { process.exit(0); }
})();
