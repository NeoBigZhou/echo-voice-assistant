/* ECHO 控制面板前端逻辑（原生 JS，无构建链） */
"use strict";

const $ = (sel) => document.querySelector(sel);
const $$ = (sel, root = document) => [...(root || document).querySelectorAll(sel)];
const esc = (s) => String(s ?? "").replace(/[&<>"']/g,
  (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

async function api(path, opts = {}) {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...opts,
  });
  if (!res.ok) throw new Error(`HTTP ${res.status}: ${await res.text()}`);
  return res.json();
}
const post = (p, body) => api(p, { method: "POST", body: JSON.stringify(body || {}) });

function toast(msg, ms = 2600) {
  const t = $("#toast");
  t.textContent = msg;
  t.classList.remove("hidden");
  clearTimeout(t._timer);
  t._timer = setTimeout(() => t.classList.add("hidden"), ms);
}

/**
 * 自绘确认弹窗（替代浏览器原生 confirm）。
 *
 * 为什么不用原生 confirm：2026-09-12 用户反馈"结束录音的确认窗口显示不全"。
 * 边条是 450 逻辑像素宽的 WebView2，宿主窗口是 DPI 感知的 WinForms 窗体，原生
 * 对话框的尺寸/缩放由浏览器接管，窄窗 + 高 DPI 下会出现裁切且无法用 CSS 修正。
 * 自绘后用页面自己的布局：宽度 min(420px, 92vw)、按钮自动换行、长文本折行。
 *
 * @returns {Promise<boolean>} 确认=true，取消/ESC/点遮罩=false
 */
function confirmDialog(message, { okText = "确定", cancelText = "取消", danger = false } = {}) {
  return new Promise((resolve) => {
    const wrap = document.createElement("div");
    wrap.className = "cdlg-mask";
    wrap.innerHTML = `
      <div class="cdlg" role="dialog" aria-modal="true">
        <div class="cdlg-msg">${esc(message)}</div>
        <div class="cdlg-actions">
          <button class="btn" data-act="cancel">${esc(cancelText)}</button>
          <button class="btn ${danger ? "danger" : "primary"}" data-act="ok">${esc(okText)}</button>
        </div>
      </div>`;
    const done = (answer) => {
      document.removeEventListener("keydown", onKey, true);
      wrap.remove();
      resolve(answer);
    };
    const onKey = (e) => {
      if (e.key === "Escape") { e.preventDefault(); done(false); }
      else if (e.key === "Enter") { e.preventDefault(); done(true); }
    };
    wrap.addEventListener("click", (e) => {
      const act = e.target.closest("[data-act]");
      if (act) { done(act.dataset.act === "ok"); return; }
      if (e.target === wrap) done(false);   // 点遮罩 = 取消
    });
    document.addEventListener("keydown", onKey, true);
    document.body.appendChild(wrap);
    const ok = wrap.querySelector('[data-act="ok"]');
    if (ok) ok.focus();
  });
}

/* ================= 视图切换 ================= */
function switchView(name) {
  $$(".view").forEach((v) => v.classList.add("hidden"));
  $(`#view-${name}`).classList.remove("hidden");
  $$(".tab").forEach((t) => t.classList.toggle("active", t.dataset.view === name));
  if (name === "dashboard") { refreshDashboard(); loadTargets(); }
  if (name === "settings") { loadSettings(); loadModelList(); }
  if (name === "history") loadHistory();
  if (name === "meetings") { loadMeetings(); refreshMeetingHeader(); }
  if (name === "boot") { loadBoot(); loadBootLogs(); loadGuardLogs(); }
}
$$(".tab").forEach((t) => t.addEventListener("click", () => switchView(t.dataset.view)));

/* ================= 仪表盘 ================= */
let _levelTimer = null;   // 录音电平轮询

/** 驱动麦克风电平条（5 根竖条，按相位错开形成波动感）。
 *  sel 可指定目标：会议录音用 #micLevel，语音命令收音用 #cmdLevel。 */
function setMicLevel(level, sel = "#micLevel") {
  const bars = $$(sel + " i");
  if (!bars.length) return;
  const v = Math.max(0, Math.min(1, level || 0));
  bars.forEach((b, idx) => {
    const phase = 0.5 + ((idx * 37) % 13) / 26;          // 0.5~1.0 相位差
    const h = v <= 0.02 ? 5 : Math.max(6, Math.min(100, v * 100 * phase));
    b.style.height = h + "%";
  });
}

/* "说话"按钮的进行态：点下去立刻有反馈，三个阶段文字/配色不同（用户 2026-09-12）。
   状态来自 /api/status 的 busy+busyPhase，所以热键/窄条发起的语音也会同步显示。 */
const CAPTURE_PHASE = {
  listening:    { text: "🎤 正在听…", cls: "listening", tip: "正在收音，说完停一下就会自动结束" },
  transcribing: { text: "✍ 转写中…",  cls: "working",   tip: "正在把语音转成文字" },
  running:      { text: "⏳ 处理中…",  cls: "working",   tip: "已发给 DSH，正在执行（详情看会话）" },
};

function renderCaptureBtn(phase) {
  const btn = $("#btnCapture");
  if (!btn) return;
  const s = phase ? (CAPTURE_PHASE[phase] || CAPTURE_PHASE.running) : null;
  btn.classList.toggle("listening", !!s && s.cls === "listening");
  btn.classList.toggle("working", !!s && s.cls === "working");
  btn.textContent = s ? s.text : "🎤 说话";
  btn.title = s ? s.tip : "点一下开始语音命令";
  btn.disabled = !!phase;
}

/* ================= 模型容灾路由（仪表盘小卡片） ================= */
const FO_ROUTE_TEXT = {
  internal: "内网 ✅", public: "公网（回退）", failed: "失败", idle: "等待请求"
};
const FO_ROUTE_CLS = {
  internal: ["online", "--green"], public: ["idle", "--yellow"],
  failed: ["error", "--red"], idle: ["idle", "--muted"]
};

async function refreshFailoverCard() {
  const card = $("#failoverCard");
  if (!card) return;
  try {
    const d = await api("/api/failover/health");
    const rt = d.routes || {};
    const last = rt.last_route || "idle";
    const [badgeCls, colorVar] = FO_ROUTE_CLS[last] || FO_ROUTE_CLS.idle;
    const badge = $("#foBadge");
    badge.textContent = last === "idle" ? "尚无请求" : (FO_ROUTE_TEXT[last] || last);
    badge.className = "badge " + badgeCls;
    $("#foStateText").textContent = FO_ROUTE_TEXT[last] || "—";
    $("#foStateText").style.color = `var(${colorVar})`;
    const dot = $("#foDot");
    dot.style.background = `var(${colorVar})`;
    dot.style.boxShadow = last === "idle" ? "none" : `0 0 8px var(${colorVar})`;
    $("#foInt").textContent = rt.internal || 0;
    $("#foPub").textContent = rt.public || 0;
    $("#foFail").textContent = rt.failed || 0;
    $("#foReq").textContent = rt.requests || 0;
    $("#foTime").textContent = d.proxy_online
      ? (rt.last_route_at ? `最近判定 ${rt.last_route_at}` : "自本次启动尚无请求")
      : "容灾代理未运行（请求将直连内网，无回退）";
  } catch (e) {
    const badge = $("#foBadge");
    badge.textContent = "代理离线";
    badge.className = "badge error";
    $("#foStateText").textContent = "不可达";
    $("#foStateText").style.color = "var(--red)";
    $("#foDot").style.background = "var(--red)";
    $("#foTime").textContent = "无法连接容灾代理：" + e.message;
  }
}

function gotoFailover() {
  switchView("failover");
  const frame = $("#foFrame");
  if (frame && !frame.src) frame.src = "http://127.0.0.1:8899/";
}
$("#failoverCard").addEventListener("click", (e) => {
  if (e.target.closest("a")) return;   // 让 "详情 ›" 链接走自己的 handler
  gotoFailover();
});
$$("[data-goto-link='failover']").forEach((a) =>
  a.addEventListener("click", (e) => { e.preventDefault(); gotoFailover(); }));

async function refreshDashboard() {
  refreshFailoverCard();            // 模型容灾小卡片（独立容错，不阻塞主刷新）
  try {
    const st = await api("/api/status");
    // 会议控制
    const mb = $("#meetingBadge");
    mb.textContent = st.meeting.active ? "录音中" : "空闲";
    mb.className = "badge " + (st.meeting.active ? "active" : "idle");
    $("#meetingInfo").textContent = st.meeting.active
      ? `正在录音：${st.meeting.folder}`
      : (st.meeting.error ? `上次错误：${st.meeting.error}` : "未在录音");
    $("#btnMeeting").textContent = st.meeting.active ? "停止录音" : "开始录音";
    $("#btnMeeting").className = "btn big " + (st.meeting.active ? "danger" : "");
    // 录音中：显示麦克风电平波动
    // 语音命令收音阶段（busyPhase=listening）同样显示波形 —— 后端复用录音器的电平回调，不开新采样流
    const micLevel = $("#micLevel");
    const cmdLevel = $("#cmdLevel");
    const listening = !!(st.busy && st.busyPhase === "listening");
    if (micLevel) micLevel.classList.toggle("hidden", !st.meeting.active);
    if (cmdLevel) cmdLevel.classList.toggle("hidden", !listening);
    if (st.meeting.active || listening) {
      if (!_levelTimer) {
        _levelTimer = setInterval(async () => {
          try {
            const lv = await api("/api/audio/level");
            setMicLevel(lv.level || 0, "#micLevel");
            setMicLevel(lv.level || 0, "#cmdLevel");
          } catch (e) { /* ignore */ }
        }, 120);
      }
    } else {
      clearInterval(_levelTimer);
      _levelTimer = null;
      setMicLevel(0, "#micLevel");
      setMicLevel(0, "#cmdLevel");
    }
    // "说话"按钮动效：点下去立刻有反馈；收音/转写/处理三个阶段文字与配色不同（用户 2026-09-12）
    renderCaptureBtn(st.busy ? st.busyPhase || "running" : null);
    // 近期命令（2026-09-12 用户要求：仪表盘只保留 2 条，把纵向空间让给会议录音卡）
    const cmds = await api("/api/commands?limit=2");
    renderCmdList($("#recentCmds"), cmds.items, false);
    // 近期会议（最近 5 条）
    const meets = await api("/api/meetings?limit=5");
    renderMeetingItems($("#recentMeetings"), meets.items);
    $("#topStatus").textContent = `运行 ${fmtUptime(st.uptime)} · ${st.busy ? "命令处理中" : "空闲"}`;
  } catch (e) {
    $("#topStatus").textContent = "连接失败：" + e.message;
  }
}

function fmtUptime(s) {
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60);
  return h > 0 ? `${h}时${m}分` : `${m}分${s % 60}秒`;
}

// 会议时长 hh:mm 格式：满1小时显示 "H:MM"，不足1小时仅显分钟数（不含秒）
function fmtHM(sec) {
  const t = Math.max(0, Math.round(sec || 0));
  const h = Math.floor(t / 3600), m = Math.floor((t % 3600) / 60);
  return h > 0 ? `${h}:${String(m).padStart(2, "0")}` : String(m);
}

const STATUS_TEXT = { online: "在线", offline: "离线", active: "工作中", idle: "空闲",
  error: "错误", disabled: "未启用", paused: "暂停", unknown: "未知",
  transcribing: "转写中", sent: "已发送", running: "执行中", done: "完成", failed: "失败",
  pending: "排队中", recording: "录音中", transcribed: "已完成", interrupted: "已中断",
  starting: "启动中" };

function renderCmdList(el, items, withReply) {
  if (!items.length) {
    el.innerHTML = `<div class="empty">暂无命令</div>`;
    return;
  }
  el.innerHTML = items.map((c) => {
    const stCls = ["done", "sent", "running"].includes(c.status) ? c.status
      : (c.status === "failed" ? "error" : "idle");
    return `<div class="cmd-item">
      <div class="head"><span class="badge ${stCls}">${STATUS_TEXT[c.status] || c.status}</span>
        <span class="muted" style="font-size:12px">${esc(c.source)}</span>
        <span class="time">${esc(c.ts || "")}</span></div>
      <div class="text">${esc(c.text)}</div>
      ${withReply && c.reply ? `<div class="reply">↳ ${esc(c.reply.slice(0, 300))}</div>` : ""}
      ${c.error ? `<div class="reply" style="color:var(--red)">⚠ ${esc(c.error)}</div>` : ""}
    </div>`;
  }).join("");
}

/* ================= 命令目标（工作区/对话） ================= */
let _targets = { workspaces: [], sessions: [] };

function shortName(p) {
  const parts = String(p || "").replace(/\\/g, "/").split("/");
  return parts[parts.length - 1] || p;
}

async function loadTargets() {
  try {
    _targets = await api("/api/dsh/targets");
  } catch (e) {
    _targets = { workspaces: [], sessions: [] };
  }
  const ws = $("#wsSelect");
  if (!ws) return;
  const prevWs = ws.dataset.val || "";
  ws.innerHTML = `<option value="">默认（ECHO 固定会话）</option>` +
    _targets.workspaces.map((w) =>
      `<option value="${esc(w)}">${esc(shortName(w))}</option>`).join("");
  ws.dataset.val = prevWs || ws.value || "";
  renderSessSelect();
}

function renderSessSelect() {
  const wsSel = $("#wsSelect");
  const ss = $("#sessSelect");
  if (!wsSel || !ss) return;
  const ws = wsSel.value;
  const list = ws ? (_targets.sessions || []).filter((s) => s.cwd === ws) : [];
  const prev = ss.dataset.val || "";
  ss.innerHTML = `<option value="">自动（该工作区最近对话 / 新建）</option>` +
    list.map((s) =>
      `<option value="${esc(s.sessionId)}">${esc(s.title || shortName(s.sessionId))}${s.running ? " ●" : ""}</option>`).join("");
  ss.dataset.val = prev || ss.value || "";
}
$("#wsSelect").addEventListener("change", renderSessSelect);

function currentTarget() {
  const ws = $("#wsSelect").value || "";
  const sid = $("#sessSelect").value || "";
  return { workspace: ws || undefined, session_id: sid || undefined };
}

/* 仪表盘事件 */
$("#btnCmdSend").addEventListener("click", async () => {
  const text = $("#cmdInput").value.trim();
  if (!text) return;
  try {
    const t = currentTarget();
    const r = await post("/api/assistant/command", { text, source: "web", ...t });
    toast(r.message);
    $("#cmdInput").value = "";
    refreshDashboard();
  } catch (e) { toast("发送失败：" + e.message); }
});
$("#cmdInput").addEventListener("keydown", (e) => { if (e.key === "Enter") $("#btnCmdSend").click(); });

$("#btnCapture").addEventListener("click", async () => {
  const btn = $("#btnCapture");
  btn.blur();
  if (btn.disabled) return;
  btn.disabled = true;
  renderCaptureBtn("listening");          // 先给本地反馈，不等后端轮询回来
  try { const r = await post("/api/assistant/capture", { source: "web" }); toast(r.message); }
  catch (e) { toast("失败：" + e.message); renderCaptureBtn(null); }
  finally { btn.disabled = false; refreshDashboard(); }
});

/* 会议录音按钮防误触：
   点击后立即失焦（blur），否则焦点留在按钮上，之后按回车/空格会再次触发
   click —— 此时按钮文字已是「停止录音」，就会误发 /api/meeting/stop 把录音停掉。
   再加 800ms 防抖，双击/连按只生效一次（服务端 start/stop 也已有原子锁）。
   停止录音前弹确认框，避免误停丢失录音。 */
let _meetingBusy = false;
$("#btnMeeting").addEventListener("click", async (e) => {
  e.currentTarget.blur();
  if (_meetingBusy) return;
  const active = $("#btnMeeting").textContent === "停止录音";
  if (active && !(await confirmDialog("确定要停止录音并开始转写吗？", { okText: "停止并转写", danger: true }))) return;
  _meetingBusy = true;
  try {
    const r = await post(active ? "/api/meeting/stop" : "/api/meeting/start", {});
    toast(r.message);
    refreshDashboard();
  } catch (e2) { toast("失败：" + e2.message); }
  finally { setTimeout(() => { _meetingBusy = false; }, 800); }
});

/* 仪表盘"全部"跳转 */
$("#gotoHistory").addEventListener("click", (e) => { e.preventDefault(); switchView("history"); });
$("#gotoMeetings").addEventListener("click", (e) => { e.preventDefault(); switchView("meetings"); });

/* 右下角箭头：把展开的面板收成屏幕右缘的折叠条（与折叠条底部的隐藏箭头互为反向操作）。
   走 WebView2 内置桥（宿主 Program.cs 的 WebMessageReceived），不经过 ECHO 的 HTTP；
   页面不在边条里运行时（整窗模式 / 浏览器直接打开）没有可收起的边条，按钮隐藏。 */
(() => {
  const btn = $("#btnRailCollapse");
  if (!btn) return;
  const bridge = window.chrome && window.chrome.webview;
  if (!bridge || typeof bridge.postMessage !== "function") { btn.classList.add("hidden"); return; }
  btn.addEventListener("click", () => bridge.postMessage("rail-collapse"));
})();

/* ================= 设置 ================= */
let _settingsCache = [];

/* 分组展示顺序 = 业务相关性（与后端 grp 取值解耦，后端不因展示顺序而改动）：
   通用(基础) → 语音命令(主用法) → 唤醒词 → 会议 → 纪要归档 → 面板(界面) → DSH(底层接入) */
const SET_GROUP_ORDER = ["general", "voice", "wake", "meeting", "worklog", "panel", "dsh"];
const SET_GROUP_NAMES = { general: "通用", voice: "语音命令", wake: "唤醒词",
  meeting: "会议", worklog: "纪要归档", panel: "面板", dsh: "DSH 服务" };
/* 默认展开；用户折叠过的分组记在 localStorage，刷新/重开面板后保持 */
const SET_COLLAPSE_KEY = "echo.settings.collapsedGroups";

function _collapsedGroups() {
  try { return new Set(JSON.parse(localStorage.getItem(SET_COLLAPSE_KEY) || "[]")); }
  catch (e) { return new Set(); }
}
function _saveCollapsedGroups(set) {
  try { localStorage.setItem(SET_COLLAPSE_KEY, JSON.stringify([...set])); } catch (e) { /* 忽略 */ }
}

async function loadSettings() {
  try {
    const r = await api("/api/settings");
    _settingsCache = r.settings;
    const groups = {};
    r.settings.forEach((s) => { (groups[s.grp] = groups[s.grp] || []).push(s); });
    // 已知分组按业务相关性排序，未知分组排到末尾（保持出现顺序）
    const known = SET_GROUP_ORDER.filter((g) => groups[g]);
    const extra = Object.keys(groups).filter((g) => !SET_GROUP_ORDER.includes(g));
    const collapsed = _collapsedGroups();
    const form = $("#settingsForm");
    form.innerHTML = [...known, ...extra].map((g) => {
      const items = groups[g];
      const isCollapsed = collapsed.has(g);
      return `<div class="set-group${isCollapsed ? " collapsed" : ""}" data-grp="${esc(g)}">
        <div class="set-group-title" role="button" tabindex="0" aria-expanded="${!isCollapsed}">
          <span class="set-arrow">▶</span>
          <span>${esc(SET_GROUP_NAMES[g] || g)}</span>
          <span class="set-count">${items.length}</span>
        </div>
        <div class="set-group-body">${items.map((s) => renderSettingRow(s)).join("")}</div>
      </div>`;
    }).join("");
  } catch (e) { toast("加载设置失败：" + e.message); }
}

/* 分组折叠/展开（事件委托，重绘后无需重新绑定） */
function toggleSetGroup(titleEl) {
  const box = titleEl.closest(".set-group");
  if (!box) return;
  const g = box.dataset.grp;
  const nowCollapsed = box.classList.toggle("collapsed");
  titleEl.setAttribute("aria-expanded", String(!nowCollapsed));
  const collapsed = _collapsedGroups();
  if (nowCollapsed) collapsed.add(g); else collapsed.delete(g);
  _saveCollapsedGroups(collapsed);
}
$("#settingsForm").addEventListener("click", (e) => {
  const title = e.target.closest(".set-group-title");
  if (title) toggleSetGroup(title);
});
$("#settingsForm").addEventListener("keydown", (e) => {
  if (e.key !== "Enter" && e.key !== " ") return;
  const title = e.target.closest(".set-group-title");
  if (title) { e.preventDefault(); toggleSetGroup(title); }
});
/* 全部折叠 / 全部展开 */
$("#btnSettingsCollapseAll")?.addEventListener("click", () => {
  const boxes = $$("#settingsForm .set-group");
  const anyOpen = boxes.some((b) => !b.classList.contains("collapsed"));
  const collapsed = _collapsedGroups();
  boxes.forEach((b) => {
    b.classList.toggle("collapsed", anyOpen);
    b.querySelector(".set-group-title")?.setAttribute("aria-expanded", String(!anyOpen));
    if (anyOpen) collapsed.add(b.dataset.grp); else collapsed.delete(b.dataset.grp);
  });
  _saveCollapsedGroups(collapsed);
});

/* ---------------- 设置 → 服务：重启 ECHO ----------------
   后端收到请求后立刻返回，真正的停/起由脱离进程组的 restart-echo.ps1 做（见 app/runtime.py）。
   这里负责：确认 → 置灰按钮 → 轮询 /api/status 等它回来（期间连接会被拒，属正常）。 */
async function waitForEchoBack(timeoutMs = 90000) {
  const t0 = Date.now();
  let down = false;
  while (Date.now() - t0 < timeoutMs) {
    try {
      const st = await api("/api/status");
      if (down || st) return st;          // 能应答即视为已恢复
    } catch (e) {
      down = true;                        // 服务正在重启：连接被拒
    }
    await new Promise((r) => setTimeout(r, 1000));
  }
  return null;
}

$("#btnRestartEcho").addEventListener("click", async (e) => {
  const btn = e.currentTarget;
  btn.blur();
  if (btn.disabled) return;
  if (!(await confirmDialog("确定重启 ECHO 服务？正在进行的录音或命令会中断。",
                            { okText: "重启", danger: true }))) return;
  btn.disabled = true;
  btn.classList.add("working");           // 复用"说话"按钮的进行态动效
  btn.textContent = "重启中…";
  const state = $("#restartState");
  try {
    const r = await post("/api/system/restart", {});
    if (!r.ok) { toast(r.message || "重启请求被拒绝"); return; }
    if (state) state.textContent = "服务重启中，等待重连…";
    toast(r.message || "正在重启…");
    const st = await waitForEchoBack();
    if (st) {
      if (state) state.textContent = "已重连（pid " + ((st.components || [])
        .filter((c) => c.name === "server")[0] || {}).pid + "）";
      toast("ECHO 已重启");
      loadSettings();                     // 拉一遍新进程的设置元数据
    } else {
      if (state) state.textContent = "90 秒内未重连，请看 data/logs/restart.log";
      toast("重启后 90 秒未重连，请查看 data\\logs\\restart.log");
    }
  } catch (err) {
    if (state) state.textContent = "请求失败：" + err.message;
    toast("重启请求失败：" + err.message);
  } finally {
    btn.disabled = false;
    btn.classList.remove("working");
    btn.textContent = "重启 ECHO 服务";
  }
});

/* ---------------- 设置 → 模型：清单 + 下载 ----------------
   清单来自 /api/models（app/modelinfo.py）：显示名可随便起，但"落地路径"是代码约定、
   改了加载器就找不到模型，所以路径在界面上原样展示、不翻译。
   下载只对上游有稳定源的三类开放（sensevoice / whisper 各档 / qwen3asr）；
   sherpa、pyannote、唤醒词只能从源机拷贝，界面只给复制说明。 */
let _modelJobs = {};
let _modelPoll = null;

function fmtMb(mb) {
  if (!mb) return "0 MB";
  return mb >= 1024 ? (mb / 1024).toFixed(1) + " GB" : mb + " MB";
}

function renderModelList(items, jobs) {
  const el = $("#modelList");
  if (!el) return;
  _modelJobs = (jobs && jobs.items) || {};
  const ready = items.filter((m) => m.ready).length;
  $("#modelSummary").textContent = `已就绪 ${ready}/${items.length}`;
  el.innerHTML = items.map((m) => {
    const job = _modelJobs[m.id] || {};
    const running = job.status === "running";
    const failed = job.status === "failed";
    const downloadable = m.source !== "copy";
    const badge = running ? `<span class="badge running">下载中 ${job.percent || 0}%</span>`
      : (m.ready ? `<span class="badge online">已就绪</span>` : `<span class="badge idle">未安装</span>`);
    const size = `${m.size}${m.local_mb ? `（本地 ${fmtMb(m.local_mb)}）` : ""}`;
    const btns = [];
    if (downloadable) {
      btns.push(`<button class="btn mini" data-dl="${esc(m.id)}" data-force="${m.ready ? "1" : "0"}" `
        + `${running || (_modelJobs.__active && !failed) ? "disabled" : ""}>`
        + (running ? `下载中 ${job.percent || 0}%` : (failed ? "重试" : (m.ready ? "重新下载" : "下载"))) + `</button>`);
    }
    if (m.cmd) btns.push(`<button class="btn mini" data-copy="${esc(m.cmd)}">复制命令</button>`);
    if (m.source === "copy") btns.push(`<button class="btn mini" data-copy="${esc(m.target)}">复制目标路径</button>`);
    return `<div class="model-row${m.ready ? " ok" : ""}">
      <div class="m-head"><span class="m-name">${esc(m.name)}</span>${badge}</div>
      <div class="m-meta">${esc(m.purpose)} · ${esc(size)}</div>
      <div class="m-path" title="落地路径（代码约定，不要改名）">${esc(m.target)}</div>
      <div class="m-how">${esc(m.how)}</div>
      ${running ? `<div class="m-bar"><i style="width:${Math.max(3, job.percent || 0)}%"></i></div>` : ""}
      ${failed ? `<div class="m-how" style="color:var(--red)">${esc(job.message || "下载失败")}</div>` : ""}
      <div class="m-actions">${btns.join("")}</div>
    </div>`;
  }).join("");

  $$("#modelList [data-dl]").forEach((b) => b.addEventListener("click", async () => {
    b.disabled = true;
    try {
      const r = await post("/api/models/download", { id: b.dataset.dl, force: b.dataset.force === "1" });
      toast(r.message);
    } catch (e) { toast("下载请求失败：" + e.message); }
    loadModelList();
  }));
  $$("#modelList [data-copy]").forEach((b) => b.addEventListener("click", async () => {
    try { await navigator.clipboard.writeText(b.dataset.copy); toast("已复制"); }
    catch (e) { toast("复制失败，请手动选择文本"); }
  }));
}

async function loadModelList() {
  const el = $("#modelList");
  if (!el) return;
  try {
    const r = await api("/api/models");
    renderModelList(r.items || [], r.jobs || {});
    // 有任务在跑就持续刷新进度，跑完自动停
    const active = r.jobs && r.jobs.active;
    if (active && !_modelPoll) {
      _modelPoll = setInterval(loadModelList, 1500);
    } else if (!active && _modelPoll) {
      clearInterval(_modelPoll);
      _modelPoll = null;
      loadSettings();          // 下载完成后模型下拉的状态也可能变
    }
  } catch (e) {
    el.innerHTML = `<div class="empty">加载失败：${esc(e.message)}</div>`;
  }
}

function renderSettingRow(s) {
  const id = "set-" + s.key;
  let ctl = "";
  if (s.value_type === "bool") {
    ctl = `<input type="checkbox" class="ctl" id="${id}" data-key="${s.key}" ${s.value ? "checked" : ""}>`;
  } else if (s.options && s.options.length) {
    ctl = `<select class="ctl" id="${id}" data-key="${s.key}">` +
      s.options.map((o) => `<option value="${esc(o)}" ${String(o) === String(s.value) ? "selected" : ""}>${esc(o)}</option>`).join("") +
      `</select>`;
  } else if (s.value_type === "int" || s.value_type === "float") {
    ctl = `<input type="number" step="${s.value_type === "float" ? "any" : "1"}" class="ctl" id="${id}" data-key="${s.key}" value="${esc(s.value)}">`;
  } else if (s.value_type === "list") {
    ctl = `<input class="ctl" id="${id}" data-key="${s.key}" value="${esc((s.value || []).join(","))}" placeholder="逗号分隔">`;
  } else {
    ctl = `<input class="ctl" id="${id}" data-key="${s.key}" value="${esc(s.value)}">`;
  }
  return `<div class="set-row">
    <label for="${id}">${esc(s.label || s.key)}</label>
    ${ctl}
    <div class="desc">${esc(s.description || "")}</div>
  </div>`;
}

$("#btnSettingsSave").addEventListener("click", async () => {
  const values = {};
  $$("#settingsForm [data-key]").forEach((el) => {
    const key = el.dataset.key;
    const meta = _settingsCache.find((s) => s.key === key);
    if (!meta) return;
    if (meta.value_type === "bool") values[key] = el.checked;
    else if (meta.value_type === "list") values[key] = el.value.split(/[,，]/).map(s => s.trim()).filter(Boolean);
    else if (meta.value_type === "int") values[key] = parseInt(el.value, 10) || 0;
    else if (meta.value_type === "float") values[key] = parseFloat(el.value) || 0;
    else values[key] = el.value;
  });
  try {
    const r = await api("/api/settings", { method: "PUT", body: JSON.stringify({ values }) });
    toast("已保存 " + Object.keys(r.updated).length + " 项");
  } catch (e) { toast("保存失败：" + e.message); }
});

/* ================= 历史 ================= */
async function loadHistory() {
  try {
    const r = await api("/api/commands?limit=200");
    renderCmdList($("#historyList"), r.items, true);
  } catch (e) { toast("加载历史失败：" + e.message); }
}
$("#btnClearCmds").addEventListener("click", async () => {
  if (!(await confirmDialog("确认清空全部命令历史？", { okText: "清空", danger: true }))) return;
  try { await api("/api/commands", { method: "DELETE" }); loadHistory(); }
  catch (e) { toast("清空失败：" + e.message); }
});

/* ================= 会议 ================= */
let _meetings = [];
let _txStatus = {};   // meeting_id -> 转写进度（轮询 /api/transcribe/status）

function meetingBadgeCls(status) {
  if (status === "recording") return "active";
  if (status === "transcribing") return "transcribing";
  if (status === "error" || status === "interrupted") return "error";
  return "idle";
}

function renderMeetingItems(el, items) {
  if (!items.length) {
    el.innerHTML = `<div class="empty">暂无会议记录</div>`;
    return;
  }
  el.innerHTML = items.map((m) => {
    const started = m.started_at ? m.started_at.replace("T", " ").slice(0, 16) : "";
    const tx = _txStatus[m.id];
    let txHtml = "";
    if (m.status === "transcribing") {
      const pct = tx ? (tx.percent || 0) : 3;
      const info = tx ? `${tx.detail || ""}` : "准备中…";
      txHtml = `<div class="tx-bar"><i style="width:${pct}%"></i></div>
        <div class="tx-info">${esc(info)}</div>`;
    }
    const shortTitle = (m.title || "").trim();
    const dur = Math.round(m.duration_seconds || 0);
    // 已自动命名（转写+纪要生成后写入 title）就只显示正式名称：
    // 开始时的时间戳文件名（m.name）只是目录标识，不再是"会议名称"，
    // 显示出来反而重复——开始时间已经在下面的 m-meta 里了（2026-09-12 起）。
    const nameHtml = `<div class="m-name">${esc(shortTitle || m.name)}</div>`;
    const hasSummary = m.has_summary ? `<span class="m-summary-tag" title="已生成会议纪要">📄</span>` : "";
    return `<div class="meeting-item" data-id="${m.id}">
      <span class="badge ${meetingBadgeCls(m.status)}">${STATUS_TEXT[m.status] || m.status}</span>
      <div class="grow">
        ${nameHtml}
        <div class="m-meta">${esc(started)} · ${fmtHM(dur)} · ${m.segments || 0} 段 ${hasSummary}</div>
        ${txHtml}
      </div>
    </div>`;
  }).join("");
  $$(".meeting-item", el).forEach((it) =>
    it.addEventListener("click", () => openMeetingDetail(parseInt(it.dataset.id, 10))));
}

async function pollTranscribe() {
  try {
    _txStatus = await api("/api/transcribe/status");
  } catch (e) { return; }
  const v = $(".tab.active");
  if (!v) return;
  if (v.dataset.view === "meetings") {
    const r = await api("/api/meetings?limit=100");
    renderMeetingItems($("#meetingList"), r.items);
    refreshMeetingHeader();          // 停留在会议列表页时按钮也要跟着录音状态变
  } else if (v.dataset.view === "dashboard") {
    const r = await api("/api/meetings?limit=5");
    renderMeetingItems($("#recentMeetings"), r.items);
  }
}

async function loadMeetings() {
  try {
    const r = await api("/api/meetings?limit=100");
    _meetings = r.items;
    renderMeetingItems($("#meetingList"), r.items);
  } catch (e) { toast("加载会议失败：" + e.message); }
}

/* ================= 会议详情（独立窗口） ================= */
function openMeetingDetail(id) {
  window.open(`/web/meeting.html?id=${id}`, "_blank");
}

/* 会议列表页的录音按钮：必须跟随录音状态（用户 2026-09-12 反馈——开始录音后点"全部 ›"
   进会议列表，按钮还显示"开始录音"，点了只会重复调 start）。 */
function renderMeetingListBtn(active) {
  const btn = $("#btnMeetingFromList");
  if (!btn) return;
  btn.dataset.active = active ? "1" : "0";
  btn.textContent = active ? "停止录音" : "开始录音";
  btn.className = "btn" + (active ? " danger" : "");
  btn.title = active ? "停止录音并开始转写" : "开始会议录音";
}

/** 只读轻量状态（/api/meeting/status），用于列表页按钮与标题跟随录音状态。 */
async function refreshMeetingHeader() {
  try {
    const st = await api("/api/meeting/status");
    renderMeetingListBtn(!!st.active);
  } catch (e) { /* 面板/服务可能正在重连，忽略 */ }
}

let _meetingListBusy = false;
$("#btnMeetingFromList").addEventListener("click", async (e) => {
  const btn = e.currentTarget;
  btn.blur();
  if (_meetingListBusy) return;
  const active = btn.dataset.active === "1";
  if (active && !(await confirmDialog("确定要停止录音并开始转写吗？", { okText: "停止并转写", danger: true }))) return;
  _meetingListBusy = true;
  try {
    const r = await post(active ? "/api/meeting/stop" : "/api/meeting/start", {});
    toast(r.message);
    await refreshMeetingHeader();
    loadMeetings();
  } catch (e2) { toast("失败：" + e2.message); }
  finally { setTimeout(() => { _meetingListBusy = false; }, 800); }
});

/* 清理 2 分钟以内的短会议（含音频文件） */
$("#btnCleanShort").addEventListener("click", async (e) => {
  e.currentTarget.blur();
  if (!(await confirmDialog("删除 2 分钟以内的会议录音（含音频与转写文件）？此操作不可恢复。",
                            { okText: "删除", danger: true }))) return;
  try {
    const r = await post("/api/meetings/clean-short", { max_minutes: 2 });
    toast(r.count ? `已清理 ${r.count} 个短会议` : "没有 2 分钟以内的会议");
    loadMeetings();
  } catch (e2) { toast("清理失败：" + e2.message); }
});

/* ================= 启动 ================= */
let _bootSettings = [];

function bootBadgeCls(status) {
  if (status === "online" || status === "active") return "online";
  if (status === "failed") return "error";
  if (status === "starting" || status === "running") return "running";
  if (status === "disabled") return "disabled";
  if (status === "idle") return "idle";
  return "idle";
}

function _settingMeta(key) {
  return _bootSettings.find((s) => s.key === key) || {};
}

function _settingValue(key) {
  return _settingMeta(key).value;
}

async function loadBoot() {
  try {
    const [bs, sr] = await Promise.all([api("/api/boot/status"), api("/api/settings")]);
    _bootSettings = sr.settings;
    renderBoot(bs);
  } catch (e) {
    $("#bootSummary").textContent = "加载失败：" + e.message;
  }
}

function renderBoot(bs) {
  const s = bs.summary;
  $("#bootSummary").textContent =
    `就绪 ${s.ready}/${s.total} · 失败 ${s.failed} · 进行中 ${s.running}`;
  const sttOpts = _settingMeta("sttModel").options || [];
  const meetOpts = _settingMeta("meetingSttModel").options || [];
  const ttsEngine = _settingValue("ttsEngine");
  $("#bootComponents").innerHTML = bs.components.map((c) => {
    const cls = bootBadgeCls(c.status);
    const bar = c.status === "starting"
      ? `<div class="boot-bar"><i style="width:${Math.max(4, Math.round(c.progress * 100))}%"></i></div>` : "";
    let ctl = "";
    if (c.id === "stt-cmd" || c.id === "stt-meeting") {
      const opts = c.id === "stt-cmd" ? sttOpts : meetOpts;
      const cur = c.id === "stt-cmd" ? _settingValue("sttModel") : _settingValue("meetingSttModel");
      ctl = `<select class="ctl boot-model" data-model="${c.id}">` +
        opts.map((o) => `<option value="${esc(o)}" ${String(o) === String(cur) ? "selected" : ""}>${esc(o)}</option>`).join("") + `</select>`;
    } else if (c.id === "tts") {
      const online = ttsEngine !== "sapi";
      ctl = `<label class="boot-switch"><input type="checkbox" data-ttsonline ${online ? "checked" : ""}>
        <span>${online ? "在线" : "离线"}</span></label>`;
    }
    const btns = [];
    if (c.can_start) btns.push(`<button class="btn mini" data-boot="${c.id}:start">${c.status === "failed" ? "重试" : "启动"}</button>`);
    if (c.can_stop) btns.push(`<button class="btn mini danger" data-boot="${c.id}:stop">停止</button>`);
    const sub = c.substep ? ` · <span class="boot-sub">${esc(c.substep)}</span>` : "";
    const dur = c.duration ? `<span class="boot-dur">${c.duration}s</span>` : "";
    // 没有控件就不渲染 boot-ctl：模板里那个空 span 在窄边条的网格布局下会多占一行（含行间距）
    const ctlHtml = (ctl || btns.length)
      ? `<span class="boot-ctl">${ctl} ${btns.join("")}</span>` : "";
    return `<div class="boot-row" data-cid="${c.id}">
      <span class="boot-ic">${c.icon}</span>
      <span class="boot-name">${esc(c.label)}</span>
      <span class="badge ${cls}">${STATUS_TEXT[c.status] || c.status}</span>
      <span class="boot-detail">${esc(c.detail)}${sub}</span>
      ${dur}
      ${bar}
      ${ctlHtml}
    </div>`;
  }).join("");
  // 事件
  $$("#bootComponents .boot-model").forEach((sel) => sel.addEventListener("change", async (e) => {
    const key = e.currentTarget.dataset.model === "stt-cmd" ? "sttModel" : "meetingSttModel";
    try {
      await api("/api/settings", { method: "PUT", body: JSON.stringify({ values: { [key]: e.currentTarget.value } }) });
      toast("已切换模型，重新加载中…");
      await post(`/api/boot/component/${e.currentTarget.dataset.model}/start`, {});
      loadBoot();
    } catch (err) { toast("切换失败：" + err.message); }
  }));
  $$("#bootComponents [data-ttsonline]").forEach((chk) => chk.addEventListener("change", async (e) => {
    try {
      await api("/api/settings", { method: "PUT", body: JSON.stringify({ values: { ttsEngine: e.currentTarget.checked ? "edge-tts" : "sapi" } }) });
      toast("已切换 TTS 模式");
      loadBoot();
    } catch (err) { toast("切换失败：" + err.message); }
  }));
  $$("#bootComponents [data-boot]").forEach((btn) => btn.addEventListener("click", async (e) => {
    const [cid, act] = e.currentTarget.dataset.boot.split(":");
    try {
      const r = await post(`/api/boot/component/${cid}/${act}`, {});
      toast(r.message);
      loadBoot();
    } catch (err) { toast("操作失败：" + err.message); }
  }));
}

async function loadBootLogs() {
  try {
    const r = await api("/api/logs?source=boot&limit=80");
    const el = $("#bootLogs");
    // DB 已按 id DESC（最新在前），直接渲染即为倒序
    const items = r.items || [];
    el.innerHTML = items.length
      ? items.map((l) => `<div class="boot-log-line"><span class="muted">${esc(l.ts || "")}</span> <span class="lv-${l.level}">${esc(l.message || "")}</span></div>`).join("")
      : `<div class="empty">暂无启动日志</div>`;
    el.scrollTop = 0;   // 最新在顶部，停在顶部查看
  } catch (e) { /* ignore */ }
}

async function loadGuardLogs() {
  try {
    const r = await api("/api/logs?source=guard&limit=60");
    const el = $("#guardLogs");
    const items = r.items || [];
    el.innerHTML = items.length
      ? items.map((l) => `<div class="boot-log-line"><span class="muted">${esc(l.ts || "")}</span> <span class="lv-${l.level}">${esc(l.message || "")}</span></div>`).join("")
      : `<div class="empty">暂无守护进程关键事件（echo-host 尚未上报）</div>`;
    el.scrollTop = 0;
  } catch (e) { /* ignore */ }
}

// PWA：注册 Service Worker（可安装为独立窗口应用）
if ("serviceWorker" in navigator) {
  window.addEventListener("load", () => {
    navigator.serviceWorker.register("/sw.js").catch(() => {});
  });
}
switchView("dashboard");
setInterval(() => {
  const v = $(".tab.active");
  if (v && v.dataset.view === "dashboard") refreshDashboard();
  else if (v && v.dataset.view === "boot") { loadBoot(); loadBootLogs(); loadGuardLogs(); }
}, 2000);
// 转写进度轮询（会议列表进度条）
setInterval(pollTranscribe, 2000);
