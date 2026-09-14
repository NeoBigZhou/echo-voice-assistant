// echo-host — ECHO 语音助手托管插件（Cordis plugin for DSH Desktop）
//
// 功能：
//   1. 随 DSH Desktop 启动自动拉起 ECHO Python 服务（8970，pythonw 无窗口），
//      崩溃自动守护重启。**DSH Desktop 退出时不停止 ECHO**（见文件末尾卸载段：
//      ECHO 常常是独立启动的，随 DSH 一起关掉会导致"一重启 DSH 服务就没了"）。
//   2. 仪表盘热键：由 **ECHO 服务进程**（app/hotkey.py 的 RegisterHotKey）注册
//      `panelHotkey`（默认 Ctrl+Shift+E）打开仪表盘窗口。
//      注意：插件侧无法用 electron 的 globalShortcut —— DSH Desktop 2.0.9 里
//      从 app.asar.unpacked 动态 import 拿到的 electron 只有 net/systemPreferences
//      （渲染/工具进程子集），没有 app/BrowserWindow/screen/globalShortcut。
//      因此边条窗口功能在本版 DSH 上不可用，热键落在 ECHO 自己进程里（更可靠）。
//
// 注意：本文件由 Cordis loader 经动态 import() 加载，注册行在 **活动 Profile 的补丁层**
//       `%USERPROFILE%\.dsh\profiles\<active>\cordis.patch.yml`（active 取自
//       `%APPDATA%\DSH Desktop\profile-selection\state.json`，本机是 `web`；安装脚本
//       会同时写 `web` 与 `desktop` 两份）：
//           - insert:
//               - id: echo-host
//                 name: file:///<DSH 安装目录>/resources/app.asar.unpacked/echo-host/index.js
//       必须保持 ESM 语法（目录内 package.json 已声明 "type": "module"）。
//
// 【部署】本文件是**唯一源码**，不要直接改 DSH 安装目录里的副本：
//       改完源码后运行 scripts\install-echo-host-plugin.ps1 重新部署；
//       scripts\launch-desktop.ps1 与 scripts\start.ps1 每次启动也会自愈式补装。
//
// 【为什么注册在 Profile 补丁层】两件事叠加：
//       1) DSH Desktop 2.0.9 **不再读取** `<DSH 安装目录>\resources\app.asar.unpacked\
//          cordis.patch.yml`（2.0.5 会读，2026-09-12 升级后插件就是这样"静默消失"的：
//          Loader 实时清单里一行都没有）；补丁只从 app.asar 内取。
//       2) Desktop 只组合**当前活动 Profile** 的补丁层（`profile-selection\state.json`
//          的 active），本机活动 profile 是 `web`，写进 `desktop` 目录同样不会被读。
//       组合顺序：bundle 层 → Profile 层 → 机器层（prepareDesktopProfile:
//       [...bundlePatches, ...profile.patches, ...homePatches] → boot(...)）。
//       Profile 层在 Desktop **启动时**组合，改完需重启 DSH Desktop 才生效。
import { spawn } from "node:child_process";
// existsSync 必须保留：resolvePythonw() 用它探测 pythonw 路径。
// 2026-09-12 曾在校改这段 import 时漏掉它，导致 spawnEcho 每次都抛
// "existsSync is not defined"，ECHO 再也拉不起来（日志里才查出来）。
import { appendFileSync, existsSync, mkdirSync, readFileSync, statSync, writeFileSync } from "node:fs";
import { createRequire } from "node:module";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

// 部署标记：安装脚本据此判断部署副本是否与源码同步（语义改动时同步 +1）
export const ECHO_HOST_BUILD = "2026-09-12.8";

// [probe] 模块被 import 时的标记（用于确认 loader 是否加载本模块）
console.error(`[echo-host-probe] module imported build=${ECHO_HOST_BUILD}`);

// ECHO 项目根目录（工作区）。三种来源，按优先级：
//   1) 环境变量 ECHO_ROOT
//   2) 与本插件同目录的 echo-root.txt —— scripts/install-echo-host-plugin.ps1 部署时会写入，
//      所以正常情况下不需要手工配置
//   3) 兜底占位值（只在没跑安装脚本、直接手放插件时才会用到）
function resolveEchoRoot() {
  const fromEnv = process.env.ECHO_ROOT;
  if (fromEnv && existsSync(fromEnv)) return fromEnv;
  try {
    const here = dirname(fileURLToPath(import.meta.url));
    const marker = join(here, "echo-root.txt");
    if (existsSync(marker)) {
      const p = String(readFileSync(marker, "utf8")).trim();
      if (p && existsSync(p)) return p;
    }
  } catch (e) {
    console.error("[echo-host] echo-root.txt 读取失败: " + e);
  }
  return "D:\\ECHO";
}
const ECHO_ROOT = resolveEchoRoot();

/**
 * ECHO 服务端口。优先环境变量 ECHO_PORT，其次读 ~/.dsh/settings.yaml 里由 ECHO
 * 自己维护的 `serverPort` 注释行，最后回退默认值。
 *
 * 为什么不写死：Windows 动态端口段（默认 1024-15000）会被 Hyper-V/WSL 划为保留段，
 * 且每次重启都会漂移——落在保留段里的端口 bind 会失败（Errno 13），届时 ECHO 起不来，
 * 而本插件的探活/守护地址若还盯着旧端口，就会误判"ECHO 未就绪"并反复重启。
 */
function resolveEchoPort() {
  const fromEnv = parseInt(process.env.ECHO_PORT || "", 10);
  if (Number.isInteger(fromEnv) && fromEnv > 0) return fromEnv;
  try {
    const h = process.env.USERPROFILE || process.env.HOME || "";
    const y = readFileSync(join(h, ".dsh", "settings.yaml"), "utf8");
    const m = y.match(/^\s*#\s*serverPort:\s*(\d+)\s*$/m);
    if (m) return parseInt(m[1], 10);
  } catch { /* 读不到就用默认值 */ }
  return 8970;
}
const ECHO_PORT = resolveEchoPort();

// ---------------------------------------------------------------- 文件日志通道
// 为什么需要它：DSH 的 `ctx.logger` 在插件里可能取不到（`ctx.logger?.info` 会静默
// 变成 no-op），而 `attachEchoPanel(...).catch(() => {})` 也会把异常吞掉——两者叠加
// 的结果是"插件明明加载了、界面却什么都没有、日志里也查不到原因"。
// 因此所有关键节点同时写一份文件日志（独立于 DSH 日志系统），排查时直接看它。
// 注意：必须定义在 ECHO_ROOT 之后，否则模块顶层调用会撞 TDZ
// （Cannot access 'ECHO_ROOT' before initialization）。
const ECHO_HOST_LOG = join(ECHO_ROOT, "data", "logs", "echo-host.log");
const LOG_MAX_BYTES = 1024 * 1024;

function fileLog(message) {
  try {
    mkdirSync(dirname(ECHO_HOST_LOG), { recursive: true });
    let size = 0;
    try { size = statSync(ECHO_HOST_LOG).size; } catch { size = 0; }
    const text = `[${new Date().toISOString()}] ${message}\n`;
    if (size > LOG_MAX_BYTES) writeFileSync(ECHO_HOST_LOG, text, "utf8");
    else appendFileSync(ECHO_HOST_LOG, text, "utf8");
  } catch { /* 日志失败绝不影响主流程 */ }
}

fileLog(`module imported build=${ECHO_HOST_BUILD} pid=${process.pid}`);

/**
 * 探测取回 electron API 的各种路径，返回 `{ api, results }`。
 *
 * 已知（2026-09-12 实测，DSH Desktop 2.0.9 / Electron 43.3.0）：
 *   - `import("electron")` 解析成功但只给空命名空间（keys=[default,module.exports]）；
 *   - `createRequire(...)("electron")` 同样为空；
 *   - run-as-node 下 `process._linkedBinding("electron_browser_app")` 会直接把进程打崩
 *     （0xC0000005），所以**绝不盲调**该绑定名，只搬运 `process.electronBinding`。
 * 每个候选的失败原因都写进本地日志，下一次运行就能直接定位。
 */
async function acquireElectronApi(log) {
  const results = [];
  const shapeOf = (mod) => {
    if (mod === undefined || mod === null) return `${mod}`;
    let own = [];
    try { own = Object.getOwnPropertyNames(mod).slice(0, 10); } catch { /* 忽略 */ }
    return `type=${typeof mod} own=[${own.join(",")}] app=${typeof (mod && mod.app)} ` +
           `BrowserWindow=${typeof (mod && mod.BrowserWindow)} screen=${typeof (mod && mod.screen)} ` +
           `globalShortcut=${typeof (mod && mod.globalShortcut)}`;
  };
  const pick = (mod) => {
    if (!mod || mod instanceof Error) return undefined;
    if (mod.app) return mod;
    if (mod.default && mod.default.app) return mod.default;
    if (mod["module.exports"] && mod["module.exports"].app) return mod["module.exports"];
    return undefined;
  };
  const attempt = async (label, loader) => {
    try {
      const mod = await loader();
      results.push(`${label}: ${shapeOf(mod)}`);
      const api = pick(mod);
      if (api) return { api, label };
    } catch (e) {
      results.push(`${label}: THREW ${e && e.message}`);
    }
    return undefined;
  };

  const found =
    await attempt("import('electron')", () => import("electron")) ||
    await attempt("import('electron/main')", () => import("electron/main")) ||
    await attempt("require('electron')", () => Promise.resolve(createRequire(import.meta.url)("electron"))) ||
    await attempt("process.electronBinding", () => Promise.resolve(
      typeof process.electronBinding === "function" ? process.electronBinding("electron") : undefined));

  // 【结论，2026-09-12 实测】该位置（app.asar.unpacked，无论静态/动态 import）只能拿到
  // electron 的"工具/渲染进程变体" own=[net,systemPreferences]，没有 app/BrowserWindow/
  // screen/globalShortcut，因此插件做不了 Electron 边条窗口（升级前 2.0.5 时可以）。
  // 边条改由 ECHO 拉起的独立进程实现（ECHO\sidebar，.NET 7 + WebView2），
  // 热键 panelHotkey 落在 ECHO 自己的 RegisterHotKey（见 app/runtime.toggle_sidebar）。
  // 需要重新验证时，把 plugin/echo-host/electron-probe-entry.js 拉进来跑一次即可。
  for (const line of results) log(`electron 探测 → ${line}`);
  return { api: found && found.api, label: found && found.label };
}

// ECHO API 探活地址
const ECHO_STATUS_URL = `http://127.0.0.1:${ECHO_PORT}/api/status`;
// ECHO 控制面板（仪表盘）地址
const ECHO_PANEL_URL = `http://127.0.0.1:${ECHO_PORT}/`;
// 守护探测间隔（毫秒）
const GUARD_INTERVAL_MS = 15000;
// ECHO 启动超时（毫秒）：超过该时间且连续探活失败才重启
const START_TIMEOUT_MS = 90000;
// 单次探活超时（毫秒）：ECHO 偶发慢响应（录音/模型加载/GC/请求排队）不误判
const PROBE_TIMEOUT_MS = 4000;
// 连续探活失败多少次才判定 ECHO 不可用（防瞬时抖动误判 → 误重启杀死正在录音的会议）
const PROBE_FAIL_LIMIT = 3;
// ECHO 关键事件上报地址（面板「守护进程关键事件」栏数据源）
const GUARD_API_URL = `http://127.0.0.1:${ECHO_PORT}/api/guard/log`;
// ECHO 离线期间关键事件暂存上限（就绪后补发，防长期离线内存膨胀）
const GUARD_PENDING_MAX = 200;
// ECHO stderr 刷屏行过滤（不写 DSH 日志）：httpx 健康检查 / uvicorn access / 模型下载进度
const STDERR_NOISE = /HTTP Request:|INFO: {5}127\.0\.0\.1|Downloading:/;

// ---------------------------------------------------------------- 仪表盘边条参数
// 2026-09-10 需求变更：边条不再与 DSH Desktop 主窗口联动（原实现把边条贴在主窗口
// 右缘外、随主窗口移动/缩放联动并为主窗口让位 —— 体验不佳）。现改为独立窗口，
// 吸附 1 号屏（主屏）右缘；Ctrl+Shift+E 展开/收起（收起为右缘 48px 窄条），
// 展开宽度可拖窗口左边框调整（避开 ECHO 的 Ctrl+Alt+C/V）。
const PANEL_WIDTH = 450;         // 初始展开宽度（2026-09-10 用户要求 450，可拖左边框调整）
const PANEL_MIN_WIDTH = 320;     // 拖边可调的最小宽度
const PANEL_MIN_PAD = 120;       // 展开后屏右侧之外至少保留的左侧内边距（防铺满整屏）
const RAIL_WIDTH = 48;           // 收起后的右缘窄条宽度
const PANEL_TOGGLE_KEY = "Control+Shift+E";    // 展开 ⇄ 收起
const PANEL_COLLAPSE_KEY = "Control+Shift+F";  // 兼容保留：收起 ⇄ 展开
// 窄条迷你状态页（ECHO 项目内）
const RAIL_HTML_PATH = join(ECHO_ROOT, "web", "rail.html");

/**
 * ECHO 仪表盘边条（独立窗口 · 吸附 1 号屏右缘 · 与 DSH 主窗口解耦）。
 *
 * 行为：
 *   - Ctrl+Shift+E：展开 ⇄ 收起（收起 = 右缘 48px 窄条 rail.html；两者都在
 *     1 号屏右缘，不随 DSH 主窗口移动/缩放）
 *   - Ctrl+Shift+F：兼容保留的 收起 ⇄ 展开 切换
 *   - 展开宽度初始 PANEL_WIDTH，**拖动窗口左边框**即可调整（右缘保持吸附）；
 *     高度始终铺满 1 号屏工作区。
 *
 * 返回 { toggle, dispose }。electron 由动态 import 获取（非 Electron 环境或
 * 解析失败时静默降级，不影响 ECHO 托管主体）。
 */
async function attachEchoPanel(log) {
  let app, BrowserWindow, globalShortcut, screen;
  // electron API 获取：ESM 与 CJS 两条路径都试，取"真的拿到 app"的那个。
  // 关键坑：`await import("electron")` 即使成功也可能只给出一个空命名空间
  // （Node 对 CJS 内建的 interop），此时解构出来的 app/screen 全是 undefined，
  // 原先的 try/catch 写法判断不出来，边条会在注册热键时静默失败。
  // 因此这里逐个候选取值并校验 app 存在，全部失败才退化为"没有边条"
  // （ECHO 托管主体不受影响）。
  const { api, label } = await acquireElectronApi(log);
  if (!api) {
    log("无法加载 electron API，仪表盘边条不可用（ECHO 托管仍正常）");
    return { toggle() {}, dispose() {} };
  }
  ({ app, BrowserWindow, globalShortcut, screen } = api);
  log(`electron API 就绪（经 ${label}）BrowserWindow=${typeof BrowserWindow} screen=${typeof screen} globalShortcut=${typeof globalShortcut}`);

  let panel = null;                // ECHO 边条窗口（独立于 DSH 主窗口）
  let railMode = false;            // 是否处于收起态（右缘窄条）
  let loadedMode = null;           // 当前页面："panel" | "rail"
  let disposed = false;
  let ready = false;
  let pendingWidth = PANEL_WIDTH;  // 用户拖边调整后的宽度记忆
  let dockTimer = null;            // 重新吸附防抖定时器

  /** 1 号屏（主屏）工作区；取不到时退化为 1920×1080，保证不会抛错。 */
  function primaryWorkArea() {
    try {
      return screen.getPrimaryDisplay().workArea;
    } catch {
      return { x: 0, y: 0, width: 1920, height: 1080 };
    }
  }

  /** 展开态期望宽度：记忆值夹在 [PANEL_MIN_WIDTH, 屏宽 - PANEL_MIN_PAD]。 */
  function desiredWidth() {
    const a = primaryWorkArea();
    const max = Math.max(PANEL_MIN_WIDTH, a.width - PANEL_MIN_PAD);
    return Math.min(Math.max(pendingWidth, PANEL_MIN_WIDTH), max);
  }

  /** 吸附：右缘贴 1 号屏右缘、上缘贴工作区上缘、高度铺满工作区。 */
  function dock() {
    if (!panel || panel.isDestroyed()) return;
    try {
      const a = primaryWorkArea();
      const w = railMode ? RAIL_WIDTH : desiredWidth();
      panel.setBounds({ x: a.x + a.width - w, y: a.y, width: w, height: a.height });
    } catch { /* 窗口销毁竞态 */ }
  }

  /** 拖边/移动后重新吸附（防抖，避免 setBounds 与正在进行的拖动互抢）。 */
  function scheduleDock() {
    if (dockTimer) clearTimeout(dockTimer);
    dockTimer = setTimeout(() => {
      dockTimer = null;
      if (!panel || panel.isDestroyed()) return;
      if (!railMode) {
        // 记住用户拖出来的宽度（窄条态不记）
        try {
          const w = panel.getSize()[0];
          if (w >= PANEL_MIN_WIDTH) pendingWidth = w;
        } catch { /* 忽略 */ }
      }
      dock();
    }, 60);
  }

  /** 等待 ECHO 就绪（最多 ~60s）后加载仪表盘；避免 8970 未就绪时白屏。 */
  async function loadPanelUrl() {
    if (!panel || panel.isDestroyed()) return;
    for (let i = 0; i < 30; i++) {
      if (railMode) return;
      if (await echoOnline(1200)) break;
      await new Promise((res) => setTimeout(res, 2000));
    }
    if (!panel || panel.isDestroyed() || railMode) return;
    try {
      await panel.webContents.loadURL(ECHO_PANEL_URL);
      loadedMode = "panel";
    } catch {
      log("仪表盘加载失败，稍后自动重试…");
      setTimeout(() => loadPanelUrl(), 3000);
    }
  }

  /** 创建边条窗口（幂等）：无边框、可拖边调宽、不占任务栏。 */
  function ensurePanel() {
    if (panel && !panel.isDestroyed()) return panel;
    const a = primaryWorkArea();
    const w = desiredWidth();
    panel = new BrowserWindow({
      x: a.x + a.width - w,
      y: a.y,
      width: w,
      height: a.height,
      frame: false,
      resizable: true,          // 拖左边框可调宽度（右缘保持吸附）
      skipTaskbar: true,
      autoHideMenuBar: true,
      show: false,
      webPreferences: { nodeIntegration: false, contextIsolation: true, sandbox: true },
    });
    panel.setMenuBarVisibility(false);
    fileLog(`panel window created id=${panel.id} bounds=${JSON.stringify(panel.getBounds())} screenAvailable=${!!screen}`);
    // 面板内打开本机 ECHO 链接（如会议详情）→ 普通窗口；其它一律拒绝
    panel.webContents.setWindowOpenHandler(({ url }) => {
      if (String(url || "").startsWith(`http://127.0.0.1:${ECHO_PORT}/`)) {
        return {
          action: "allow",
          overrideBrowserWindowOptions: {
            width: 1000,
            height: 760,
            autoHideMenuBar: true,
            webPreferences: { nodeIntegration: false, contextIsolation: true, sandbox: true },
          },
        };
      }
      return { action: "deny" };
    });
    // 拖边调整宽度后重新吸附右缘（与 DSH 主窗口无关）
    panel.on("resize", scheduleDock);
    panel.on("move", scheduleDock);
    panel.on("moved", scheduleDock);
    panel.on("resized", scheduleDock);
    // 边条窗口被外部关闭（如 Alt+F4）→ 清理引用，热键可重新展开
    panel.on("closed", () => {
      panel = null;
      railMode = false;
      loadedMode = null;
    });
    return panel;
  }

  /** 边条是否已打开可见。 */
  function isOpen() {
    try {
      return !!(panel && !panel.isDestroyed() && panel.isVisible());
    } catch {
      return false;
    }
  }

  /** 展开为完整仪表盘（吸附 1 号屏右缘）。 */
  function expand() {
    const p = ensurePanel();
    if (!p || p.isDestroyed()) return;
    const wasRail = railMode;
    railMode = false;
    dock();
    if (!p.isVisible()) p.show();
    try { p.focus(); } catch { /* 忽略聚焦失败 */ }
    if (loadedMode === "panel" && !wasRail) {
      // 已是仪表盘页：强制刷新，保证看到最新内容（前端改动无需重启插件）
      try { p.webContents.reloadIgnoringCache(); } catch { try { p.webContents.reload(); } catch {} }
    } else if (loadedMode !== "panel") {
      loadPanelUrl();
    }
    log(`ECHO 仪表盘: 展开（${desiredWidth()}px · 吸附 1 号屏右缘 · 拖左边框调宽 · Ctrl+Shift+E 收起）`);
  }

  /** 收起为 1 号屏右缘 48px 窄条（rail.html）。 */
  function collapseToRail() {
    if (!panel || panel.isDestroyed()) return;
    if (!railMode) {
      try {
        const w = panel.getSize()[0];
        if (w >= PANEL_MIN_WIDTH) pendingWidth = w;   // 记住用户调整后的宽度
      } catch { /* 忽略 */ }
    }
    railMode = true;
    dock();
    if (loadedMode !== "rail") {
      loadedMode = "rail";
      panel.webContents.loadFile(RAIL_HTML_PATH).catch(() => log("窄条页面加载失败"));
    }
    if (!panel.isVisible()) panel.show();
    log("ECHO 仪表盘: 收起为 1 号屏右缘窄条（Ctrl+Shift+E 展开）");
  }

  /**
   * Ctrl+Shift+E：展开 ⇄ 收起。
   * 已展开但不在最前（被其它窗口遮住）时，先提到最前，避免"看不见却把热键吃掉"。
   */
  function toggle() {
    if (!isOpen()) { expand(); return; }
    if (railMode) { expand(); return; }
    let focused = false;
    try { focused = !!panel.isFocused(); } catch { focused = false; }
    if (!focused) { expand(); return; }
    collapseToRail();
  }

  /** Ctrl+Shift+F：兼容保留的 收起 ⇄ 展开 切换。 */
  function toggleCollapse() {
    if (!isOpen() || railMode) expand();
    else collapseToRail();
  }

  function dispose() {
    if (disposed) return;
    disposed = true;
    if (dockTimer) { clearTimeout(dockTimer); dockTimer = null; }
    try { globalShortcut.unregister(PANEL_TOGGLE_KEY); } catch {}
    try { globalShortcut.unregister(PANEL_COLLAPSE_KEY); } catch {}
    try { screen.removeListener("display-metrics-changed", dock); } catch {}
    try { screen.removeListener("display-added", dock); } catch {}
    try { screen.removeListener("display-removed", dock); } catch {}
    if (panel && !panel.isDestroyed()) {
      try { panel.removeAllListeners(); } catch {}
      try { panel.destroy(); } catch {}
    }
    panel = null;
    railMode = false;
    loadedMode = null;
  }

  function activate() {
    if (ready || disposed) return;
    ready = true;
    fileLog(`activate(): app.isReady=${(() => { try { return app.isReady(); } catch { return "?"; } })()} shortcut=${PANEL_TOGGLE_KEY}`);
    try {
      if (globalShortcut.register(PANEL_TOGGLE_KEY, toggle)) {
        log(`仪表盘快捷键已注册: ${PANEL_TOGGLE_KEY}（展开/收起 · 1 号屏右缘）`);
      } else {
        log(`快捷键 ${PANEL_TOGGLE_KEY} 注册失败（可能被占用）`);
      }
      if (globalShortcut.register(PANEL_COLLAPSE_KEY, toggleCollapse)) {
        log(`仪表盘快捷键已注册: ${PANEL_COLLAPSE_KEY}（收起/展开）`);
      } else {
        log(`快捷键 ${PANEL_COLLAPSE_KEY} 注册失败（可能被占用）`);
      }
    } catch (e) {
      log(`注册快捷键异常: ${e.message}`);
    }
    // 显示器分辨率/缩放/增删变化（切主屏、改 DPI）→ 重新吸附 1 号屏右缘。
    // 必须在 app ready 之后注册：Electron 的 screen 模块在 ready 前不可用。
    try { screen.on("display-metrics-changed", dock); } catch {}
    try { screen.on("display-added", dock); } catch {}
    try { screen.on("display-removed", dock); } catch {}
  }

  // 应用就绪后注册（插件加载时 app 可能尚未 ready）
  try {
    if (app.isReady()) activate();
    else app.whenReady().then(activate).catch(() => {});
  } catch { /* 非 Electron 环境（如测试）忽略 */ }

  return { toggle, dispose };
}
/** 优先使用 ECHO_PYTHONW（非 ASCII 路径下的解释器覆盖），回退到工作区 venv。 */
function resolvePythonw() {
  const alt = process.env.ECHO_PYTHONW;
  if (alt && existsSync(alt)) return alt;
  const std = join(ECHO_ROOT, "venv", "Scripts", "pythonw.exe");
  if (existsSync(std)) return std;
  return null;
}

async function echoOnline(timeoutMs = 1500) {
  try {
    const res = await fetch(ECHO_STATUS_URL, { signal: AbortSignal.timeout(timeoutMs) });
    return res.ok;
  } catch {
    return false;
  }
}

export const name = "echo-host";

export function apply(ctx) {
  // [probe] apply 被调用（确认 loader 激活了插件）
  console.error("[echo-host-probe] apply called");
  fileLog("apply called");
  let proc = null;       // 当前 ECHO 子进程
  let guard = null;      // 守护定时器
  let stopping = false;  // 插件卸载中
  let startedAt = 0;     // 最近一次启动时刻（超时判定）
  let restarting = false;
  let probeFail = 0;     // 连续探活失败计数（达到 PROBE_FAIL_LIMIT 才动作）

  const log = (...args) => {
    // 双通道：DSH 日志（可能取不到 logger）+ 本地文件日志（一定写）
    fileLog(args.map((a) => (typeof a === "string" ? a : String(a))).join(" "));
    try { ctx.logger?.info("[echo-host]", ...args); } catch { /* logger 可能未就绪 */ }
  };

  // ---- 守护关键事件上报（面板「守护进程关键事件」栏） ----
  let pendingEvents = [];   // ECHO 离线期间积压的关键事件（就绪后补发）
  let flushBusy = false;

  function guardLog(level, message) {
    const lv = level === "warn" || level === "error" ? level : "info";
    log(`[guard/${lv}] ${message}`);
    pendingEvents.push({ level: lv, message });
    if (pendingEvents.length > GUARD_PENDING_MAX) pendingEvents.shift();
    flushGuardEvents();
  }

  async function flushGuardEvents() {
    if (flushBusy || !pendingEvents.length) return;
    flushBusy = true;
    const batch = pendingEvents.slice();
    try {
      const res = await fetch(GUARD_API_URL, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ events: batch }),
        signal: AbortSignal.timeout(2000),
      });
      if (res.ok) pendingEvents = pendingEvents.slice(batch.length); // 只清已发送的
    } catch { /* ECHO 不在线/未就绪：保留，等健康时由 guardTick 补发 */ }
    finally { flushBusy = false; }
  }

  function killEcho() {
    if (!proc) return;
    const p = proc;
    proc = null;                       // 先清引用：其 exit 事件不再判定为"意外退出"
    const pid = p.pid;
    try { p.kill(); } catch { /* 已退出 */ }
    if (!stopping) guardLog("warn", `主动终止 ECHO (pid=${pid})`);
  }

  function spawnEcho() {
    // 入口即落盘：本函数两次被调用却没有任何日志（2026-09-12），先钉死"到底有没有进来"
    fileLog("spawnEcho(): enter");
    let pyw = null;
    try {
      pyw = resolvePythonw();
    } catch (err) {
      fileLog("spawnEcho(): resolvePythonw threw: " + (err && err.message));
    }
    fileLog(`spawnEcho(): resolvePythonw -> ${pyw || "null"}`);
    if (!pyw) {
      log("未找到 pythonw（ECHO_PYTHONW 或 venv\\Scripts\\pythonw.exe），无法启动 ECHO");
      guardLog("error", "未找到 pythonw，无法启动 ECHO");
      return;
    }
    try {
      // stdout 也接管：ECHO 的启动横幅与热键注册行都写在 stdout，早期失败时
      // 这是唯一线索（2026-09-12 的排障就是缺了它）。
      const child = spawn(pyw, ["-m", "app.main"], {
        cwd: ECHO_ROOT,
        windowsHide: true,   // pythonw 本无窗口，double-safe
        stdio: ["ignore", "pipe", "pipe"],
      });
      fileLog(`spawnEcho(): spawn() returned pid=${child && child.pid}`);
      proc = child;
      startedAt = Date.now();
      child.stdout?.on("data", (d) => {
        const line = String(d).trim();
        if (line) fileLog("ECHO/stdout: " + line.slice(-300));
      });
      child.stderr?.on("data", (d) => {
        const line = String(d).trim();
        if (!line) return;
        if (STDERR_NOISE.test(line)) return;   // 刷屏过滤（健康检查/access/进度），不存
        log("ECHO:", line.slice(-200));
        fileLog("ECHO/stderr: " + line.slice(-300));
        if (/\b(ERROR|Error|Traceback|Exception|FATAL)\b/.test(line)) {
          guardLog("error", `ECHO 输出: ${line.slice(-200)}`);
        }
      });
      // spawn 层面的失败（exe 不存在 / 无执行权限等）不会走 stderr，必须单独记
      child.on("error", (err) => {
        fileLog(`spawn ECHO FAILED (pid=${child.pid}): ${err && err.message}`);
        guardLog("error", `启动 ECHO 失败: ${err && err.message}`);
      });
      child.on("exit", (code, signal) => {
        const livedMs = Date.now() - startedAt;
        const lv = (code === 0 || stopping) ? "info" : "warn";
        fileLog(`ECHO exited pid=${child.pid} code=${code} signal=${signal ?? ""} livedMs=${livedMs}`);
        // 早期退出（<10s）通常是启动参数/Python 环境问题，升级为 error 便于面板可见
        if (!stopping && livedMs < 10000) {
          guardLog("error", `ECHO 启动后 ${Math.round(livedMs / 1000)}s 内即退出 code=${code} signal=${signal ?? ""}`);
        } else {
          guardLog(lv, `ECHO 进程退出 code=${code} signal=${signal ?? ""}`);
        }
        // 只有仍是"当前托管进程"才清引用：否则会把重启后新拉起的进程引用误清，
        // 造成下个守护周期判定"无托管进程"→ 重复拉起（多实例抢 8970）。
        if (proc === child) proc = null;
        if (!stopping) scheduleRestart();
      });
      log(`已启动 ECHO (pid=${child.pid})，等待 8970 就绪…`);
      guardLog("info", `已启动 ECHO (pid=${child.pid})，等待 8970 就绪`);
    } catch (err) {
      log(`启动 ECHO 失败: ${err.message}`);
      guardLog("error", `启动 ECHO 失败: ${err.message}`);
      proc = null;
    }
  }

  function scheduleRestart() {
    if (stopping || restarting) return;
    restarting = true;
    setTimeout(async () => {
      restarting = false;
      if (stopping) return;
      const online = await echoOnline(PROBE_TIMEOUT_MS);
      if (online) { log("ECHO 已就绪"); guardLog("info", "ECHO 已就绪"); return; }
      log("ECHO 不在线，重新拉起…");
      guardLog("warn", "ECHO 不在线，重新拉起");
      // spawn 是同步调用（Node 内部 CreateProcess 可能要上百毫秒），放到下一个
      // 事件循环 tick，避免阻塞 Electron 主进程（界面卡顿/热键延迟）。
      setTimeout(spawnEcho, 0);
    }, 5000);
  }

  async function guardTick() {
    if (stopping) return;
    const online = await echoOnline(PROBE_TIMEOUT_MS);
    if (online) {
      probeFail = 0;
      flushGuardEvents();   // 健康时补发离线期间积压的关键事件
      return;
    }
    probeFail += 1;
    if (probeFail < PROBE_FAIL_LIMIT) {
      log(`探活失败 ${probeFail}/${PROBE_FAIL_LIMIT}，暂不动作（等待下次确认，避免误杀录音）`);
      guardLog("warn", `探活失败 ${probeFail}/${PROBE_FAIL_LIMIT}，暂不动作`);
      return;
    }
    probeFail = 0;
    if (proc) {
      // 进程还在但 API 持续不可达：超过启动/卡死超时则重启
      if (Date.now() - startedAt > START_TIMEOUT_MS) {
        log("ECHO 连续探活失败且超过启动超时，重启…");
        guardLog("error", "连续探活失败且超过启动超时，重启 ECHO");
        killEcho();
        spawnEcho();
      }
      return;
    }
    log("ECHO 连续探活失败且无托管进程，自动拉起…");
    guardLog("warn", "连续探活失败且无托管进程，自动拉起 ECHO");
    setTimeout(spawnEcho, 0);   // spawn 同步阻塞主进程，挪到下一个 tick
  }

  // 启动：先尝试拉起（ECHO 冷启动较慢，异步进行）
  guardLog("info", "echo-host 守护已启动");
  setTimeout(async () => {
    if (stopping) return;
    if (await echoOnline(PROBE_TIMEOUT_MS)) { log("ECHO 已在运行"); guardLog("info", "ECHO 已在运行"); }
    else setTimeout(spawnEcho, 0);
  }, 1500);

  // 守护：周期探测
  guard = setInterval(guardTick, GUARD_INTERVAL_MS);
  guard.unref?.();

  // ECHO 仪表盘边条（独立窗口 · 吸附 1 号屏右缘 · 全局快捷键切换）
  const panelHolder = { toggle() {}, dispose() {} };
  attachEchoPanel(log).then((p) => {
    panelHolder.toggle = p.toggle;
    panelHolder.dispose = p.dispose;
    fileLog("panel attached");
  }).catch((err) => {
    // 绝不再静默吞掉：边条挂了必须留下可查的原因（含 stack 前几行）
    fileLog("panel attach FAILED: " + (err && err.stack ? String(err.stack).split("\n").slice(0, 5).join(" | ") : String(err)));
    log("仪表盘边条初始化失败:", err && err.message ? err.message : String(err));
  });

  // 卸载：移除边条 + 停止守护。
  //
  // 【重要】不再随 DSH Desktop 退出而终止 ECHO。
  // 2026-09-12 的故障链：DSH 退出 → 插件卸载时 killEcho() 把 ECHO 一起杀掉；
  // 而 ECHO 往往是桌面图标/开机自启独立启动的（不是插件拉起的），于是"重启 DSH
  // 就顺手把 ECHO 关了"，而下次启动时 ECHO 已不在、插件又只把"已在运行"记成一次
  // 探活成功，用户看到的就是"服务老是不在"。
  // ECHO 是无状态本地服务（面板是浏览器里的 SPA），与 DSH 生命周期解耦更符合使用习惯：
  // DSH 退出后 ECHO 继续提供面板/热键；要停就用 ECHO 面板的"停止"或 scripts\stop.ps1。
  ctx.effect(() => () => {
    stopping = true;
    clearInterval(guard);
    if (proc) {
      fileLog(`DSH 退出：ECHO (pid=${proc.pid}) 由本插件拉起，保留运行（不再随 DSH 停止）`);
    }
    panelHolder.dispose();
    log("插件卸载，echo-host 守护已停止（ECHO 服务保留运行）");
    guardLog("info", "echo-host 守护已停止（DSH Desktop 退出；ECHO 服务保留运行）");
  });
}
