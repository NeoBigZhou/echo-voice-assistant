// electron-main-api.js — 用**静态 import** 取 Electron 主进程 API。
//
// 背景（2026-09-12 实测，DSH Desktop 2.0.9 / Electron 43.3.0）：
//   - 插件里 `import("electron")` / `createRequire('electron')` / 以 app.asar 为锚点的
//     require，都只能拿到 `[net, systemPreferences]`；探针显示 require.cache 里的键是
//     `electron` / `electron/common` / `electron/utility` —— 即解析到了**工具/渲染进程那一份**，
//     因此没有 app/BrowserWindow/screen/globalShortcut。
//   - 而 DSH 自己的 shell（app.asar 内的 ESM 代码）用静态 import 是能拿到主进程 API 的
//     （它确实在开主窗口）。
// 所以这里用**静态 import** 并在模块顶层求值，作为插件的对照探针：
// 若 `app` 存在，说明"动态 import 才会降级"，插件改成静态 import 即可恢复边条；
// 若依然为空，则说明该位置只能拿到工具进程变体，需要另辟路径。

import * as electronAll from "electron";
import * as electronMain from "electron/main";

export const staticAll = electronAll;
export const staticMain = electronMain;

export function describeStatic() {
  const shape = (label, mod) => {
    if (mod === undefined || mod === null) return `${label}: ${mod}`;
    let own = [];
    try { own = Object.getOwnPropertyNames(mod).slice(0, 14); } catch { /* ignore */ }
    return `${label}: type=${typeof mod} own=[${own.join(",")}] app=${typeof mod.app} ` +
      `BrowserWindow=${typeof mod.BrowserWindow} screen=${typeof mod.screen} ` +
      `globalShortcut=${typeof mod.globalShortcut} ipcMain=${typeof mod.ipcMain}`;
  };
  return [shape("static import * as 'electron'", electronAll), shape("static import * as 'electron/main'", electronMain)];
}

/** 静态 import 拿到的可用 API（优先 'electron'，其次 'electron/main'），取不到返回 undefined。 */
export function staticElectronApi() {
  for (const mod of [electronAll, electronMain]) {
    if (mod && mod.app && mod.BrowserWindow) return mod;
    if (mod && mod.default && mod.default.app && mod.default.BrowserWindow) return mod.default;
  }
  return undefined;
}
