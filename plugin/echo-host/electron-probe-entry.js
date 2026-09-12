// electron-probe-entry.js — 一次性诊断入口（由 echo-host 插件动态 import）。
//
// 目的：查清 DSH Desktop 2.0.9 下 "插件拿不到 electron 主进程 API" 的确切成因。
// 已知事实：同一段插件代码在 2.0.5 有全局热键与边条窗口（日志 `ECHO 仪表盘: 展开`），
// 2.0.9 里 `import("electron")` 只给出 net/systemPreferences（渲染/工具进程子集）。
//
// 本模块与 echo-host 同目录、由插件动态 import —— 即“与插件完全相同的加载路径”。
// 它对比若干种取法，并把结果写进文件日志，然后返回结果给插件一起记录。

import { appendFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { pathToFileURL } from "node:url";

// 诊断日志写到系统临时目录（可用 ECHO_ELECTRON_PROBE_LOG 覆盖），不依赖仓库位置
const LOG = process.env.ECHO_ELECTRON_PROBE_LOG || join(tmpdir(), "echo-electron-probe.log");

export function probeLog(line) {
  try { appendFileSync(LOG, `[${new Date().toISOString()}] ${line}\n`, "utf8"); } catch { /* ignore */ }
}

function shape(label, mod) {
  if (mod instanceof Error) return `${label}: THREW ${mod.message}`;
  if (mod === null || mod === undefined) return `${label}: ${mod}`;
  let own = [];
  try { own = Object.getOwnPropertyNames(mod).slice(0, 12); } catch { /* ignore */ }
  return `${label}: type=${typeof mod} own=[${own.join(",")}] app=${typeof mod.app} ` +
    `BrowserWindow=${typeof mod.BrowserWindow} screen=${typeof mod.screen} ` +
    `globalShortcut=${typeof mod.globalShortcut}`;
}

export async function runElectronProbe() {
  const results = [];

  // 1. 本文件内动态 import（与插件里那条完全一致）
  try { results.push(shape("probe: dynamic import('electron')", await import("electron"))); }
  catch (e) { results.push(shape("probe: dynamic import('electron')", e)); }

  // 2. Node 的 CJS 通道
  try {
    const { createRequire } = await import("node:module");
    results.push(shape("probe: createRequire('electron')", createRequire(import.meta.url)("electron")));
  } catch (e) { results.push(shape("probe: createRequire('electron')", e)); }

  // 3. 从 DSH 安装目录的 package.json 作为锚点 require（加载器内部用的就是这种锚点）
  //    DSH 安装目录优先取环境变量 DSH_INSTALL_DIR，否则按默认安装位置推断。
  try {
    const { createRequire } = await import("node:module");
    const dshDir = process.env.DSH_INSTALL_DIR
      || join(process.env.LOCALAPPDATA || "", "Programs", "DSH Desktop");
    const anchor = pathToFileURL(join(dshDir, "resources", "app.asar", "package.json")).href;
    results.push(shape("probe: anchor=app.asar/package.json", createRequire(anchor)("electron")));
  } catch (e) { results.push(shape("probe: anchor=app.asar/package.json", e)); }

  // 4. 走 Electron 自己的模块注册表（主进程内可见）
  try {
    const keys = Object.keys(process).filter((k) => /electron/i.test(k));
    results.push(`probe: process keys matching /electron/i = [${keys.join(",")}]`);
  } catch (e) { results.push("probe: process key scan THREW " + e.message); }

  // 5. 只读地看一眼模块缓存里有没有 electron 的键（有则说明进程内已加载过主进程那份）
  try {
    const { createRequire } = await import("node:module");
    const req = createRequire(import.meta.url);
    const cached = Object.keys(req.cache || {}).filter((k) => /electron/i.test(k)).slice(0, 10);
    results.push(`probe: require.cache electron keys = [${cached.map((c) => c.split(/[\\/]/).slice(-2).join("/")).join(",")}]`);
  } catch (e) { results.push("probe: require.cache scan THREW " + e.message); }

  // 6. 关键对照：静态 import（DSH 自己 shell 的写法）能否拿到主进程 API
  try {
    const helper = await import("./electron-main-api.js");
    for (const line of helper.describeStatic()) results.push(line);
    const api = helper.staticElectronApi();
    results.push(`probe: staticElectronApi() -> ${api ? "AVAILABLE (" + Object.keys(api).length + " keys)" : "undefined"}`);
  } catch (e) {
    results.push("probe: static import helper THREW " + (e && e.message ? e.message : String(e)));
  }

  for (const line of results) probeLog(line);
  return results;
}
