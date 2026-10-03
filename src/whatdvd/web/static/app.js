"use strict";

// ---------- 常量与状态 ----------

const $ = (id) => document.getElementById(id);
const KIND_LABEL = { dir: "目录", dvd: "DVD", iso: "ISO" };
const JOB_LABEL = { run: "截图与 MediaInfo", torrent: "做种" };
const JOB_SHORT = { run: "截图", torrent: "做种" };

// 图标：固定的 SVG 片段，不含外部输入
const ICONS = {
  folder: '<path d="M3 7.5A2.5 2.5 0 0 1 5.5 5H9l2 2h7.5A2.5 2.5 0 0 1 21 9.5v7a2.5 2.5 0 0 1-2.5 2.5h-13A2.5 2.5 0 0 1 3 16.5z"/>',
  disc: '<circle cx="12" cy="12" r="9"/><circle cx="12" cy="12" r="2.5"/><path d="M12 5.5a6.5 6.5 0 0 1 6.5 6.5"/>',
  iso: '<path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z"/><path d="M14 3v5h5"/><circle cx="12" cy="14.5" r="3"/>',
  up: '<path d="M15 18l-6-6 6-6"/>',
  chev: '<path d="M9 6l6 6-6 6"/>',
  play: '<path d="M7 5l12 7-12 7z"/>',
  image: '<rect x="3" y="5" width="18" height="14" rx="2"/><circle cx="9" cy="10" r="1.6"/><path d="M21 16l-5-5-8 8"/>',
  peers: '<circle cx="6" cy="12" r="2.5"/><circle cx="18" cy="6" r="2.5"/><circle cx="18" cy="18" r="2.5"/><path d="M8.3 10.9l7.4-3.7M8.3 13.1l7.4 3.7"/>',
  copy: '<rect x="9" y="9" width="11" height="11" rx="2"/><path d="M5 15V6a2 2 0 0 1 2-2h8"/>',
  check: '<path d="M5 12.5l4.5 4.5L19 7"/>',
  down: '<path d="M12 4v11"/><path d="M7 10l5 5 5-5"/><path d="M5 20h14"/>',
  link: '<path d="M14 4h6v6"/><path d="M20 4l-9 9"/><path d="M18 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1h5"/>',
  next: '<path d="M5 12h14"/><path d="M13 6l6 6-6 6"/>',
  gear: '<circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1.1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z"/>',
  logout: '<path d="M14 4h4a2 2 0 0 1 2 2v12a2 2 0 0 1-2 2h-4"/><path d="M10 16l-4-4 4-4"/><path d="M6 12h10"/>',
  alert: '<circle cx="12" cy="12" r="9"/><path d="M12 7.5v5.5"/><path d="M12 16.5h.01"/>',
  search: '<circle cx="11" cy="11" r="6.5"/><path d="M20 20l-4.3-4.3"/>',
  refresh: '<path d="M20 11a8 8 0 1 0-2.3 5.7"/><path d="M20 5v6h-6"/>',
};

const state = {
  config: null,
  listing: null,        // 侧栏当前目录的浏览结果
  selected: null,       // 当前查看的来源路径
  checked: new Set(),   // 勾选的路径（批量）
  jobs: [],             // 任务摘要，新的在前
  details: new Map(),   // 已完成任务的详情缓存
  stream: null,
  route: null,
  opts: null,           // 表单选项，在各页面之间共享
  titles: new Map(),    // 来源路径 → 从 TMDB 选中的片名 { match, region, edition }
  pollTimer: null,
  toastTimer: null,
  releaseTimer: null,
};

class AuthError extends Error {}

// ---------- 工具 ----------

function setIcon(span, name) {
  span.innerHTML = `<svg viewBox="0 0 24 24" aria-hidden="true">${ICONS[name]}</svg>`;
}

function icon(name) {
  const span = document.createElement("span");
  span.className = "ico";
  setIcon(span, name);
  return span;
}

function h(tag, attrs = {}, ...children) {
  const el = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (value === null || value === undefined || value === false) continue;
    if (key === "class") el.className = value;
    else if (key === "style") el.style.cssText = value;  // 通过 CSSOM 设置，不受 CSP 对 style 属性的限制
    else if (key.startsWith("on")) el.addEventListener(key.slice(2), value);
    else el.setAttribute(key, value === true ? "" : String(value));
  }
  for (const child of children.flat()) {
    if (child === null || child === undefined || child === false) continue;
    el.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return el;
}

function formatBytes(size) {
  let value = size;
  for (const unit of ["B", "KiB", "MiB"]) {
    if (value < 1024) return unit === "B" ? `${size} B` : `${value.toFixed(1)} ${unit}`;
    value /= 1024;
  }
  return `${value.toFixed(2)} GiB`;
}

function timecode(seconds) {
  const s = seconds % 86400;
  const pad = (n) => String(n).padStart(2, "0");
  return `${pad(Math.floor(s / 3600))}:${pad(Math.floor((s % 3600) / 60))}:${pad(s % 60)}`;
}

function duration(seconds) {
  if (seconds < 60) return `${seconds} 秒`;
  const m = Math.floor(seconds / 60);
  return seconds % 60 ? `${m} 分 ${seconds % 60} 秒` : `${m} 分钟`;
}

function stamp(ts) {
  const d = new Date(ts * 1000);
  const today = new Date().toDateString() === d.toDateString();
  return today ? clock(ts) : d.toLocaleString("zh-CN", { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit" });
}

function clock(ts) {
  return new Date(ts * 1000).toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" });
}

function basename(path) {
  const parts = path.split("/").filter(Boolean);
  return parts.length ? parts[parts.length - 1] : path;
}

function parentOf(path) {
  const index = path.lastIndexOf("/");
  return index > 0 ? path.slice(0, index) : "/";
}

function rootOf(path) {
  return state.config.roots.find((root) => path === root || path.startsWith(`${root}/`)) || null;
}

function shortPath(path) {
  const root = rootOf(path);
  if (!root) return path;
  return [basename(root), ...path.slice(root.length).split("/").filter(Boolean)].join(" / ");
}

function pieceLabel(n) {
  const bytes = 2 ** n;
  return bytes >= 1024 ** 2 ? `${bytes / 1024 ** 2} MiB` : `${bytes / 1024} KiB`;
}

function formatDetail(detail) {
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) return detail.map((d) => String(d.msg || "").replace(/^Value error, /, "")).join("；");
  return "";
}

function toast(message) {
  const el = $("toast");
  el.textContent = message;
  el.hidden = false;
  clearTimeout(state.toastTimer);
  state.toastTimer = setTimeout(() => { el.hidden = true; }, 2200);
}

async function api(path, options = {}) {
  const init = { method: options.method || "GET", headers: {} };
  if (options.json !== undefined) {
    init.method = options.method || "POST";
    init.headers["Content-Type"] = "application/json";
    init.body = JSON.stringify(options.json);
  }
  const response = await fetch(path, init);
  if (response.status === 401) {
    showLogin();
    throw new AuthError("未登录");
  }
  if (!response.ok) {
    let message = `请求失败（HTTP ${response.status}）`;
    try { message = formatDetail((await response.json()).detail) || message; } catch { /* 非 JSON */ }
    throw new Error(message);
  }
  if (response.status === 204) return null;
  const type = response.headers.get("Content-Type") || "";
  return type.includes("application/json") ? response.json() : response.text();
}

async function writeClipboard(text) {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    // 非 HTTPS 的远程地址没有剪贴板 API，退回到选中文本复制
    const area = h("textarea", { class: "offscreen" });
    area.value = text;
    document.body.append(area);
    area.select();
    let ok = false;
    try { ok = document.execCommand("copy"); } catch { ok = false; }
    area.remove();
    return ok;
  }
}

function copyButton(label, getText, cls = "glass") {
  const glyph = icon("copy");
  const text = h("span", {}, label);
  const button = h("button", { type: "button", class: `btn small ${cls}` }, glyph, text);
  let timer = null;
  button.addEventListener("click", async () => {
    const ok = await writeClipboard(await getText());
    clearTimeout(timer);
    setIcon(glyph, ok ? "check" : "alert");
    text.textContent = ok ? "已复制" : "复制失败";
    timer = setTimeout(() => { setIcon(glyph, "copy"); text.textContent = label; }, 1800);
  });
  return button;
}

function notice(kind, message) {
  return h("div", { class: `notice ${kind}`, role: kind === "bad" ? "alert" : null }, icon("alert"), h("div", {}, message));
}

function statusOf(job) {
  if (job.status === "done" && job.ok === false) return { cls: "warn", text: "完成，有问题" };
  const pct = job.progress !== null && job.progress !== undefined ? ` ${Math.round(job.progress * 100)}%` : "";
  const text = { queued: "排队中", running: `运行中${pct}`, done: "完成", failed: "失败" }[job.status] || job.status;
  return { cls: job.status, text };
}

function progressBar(job) {
  const status = statusOf(job);
  const finished = job.status === "done" || job.status === "failed";
  const indeterminate = job.status === "running" && (job.progress === null || job.progress === undefined);
  const width = finished ? 100 : Math.round((job.progress || 0) * 100);
  return h("div", { class: `bar ${status.cls}${indeterminate ? " indeterminate" : ""}`, role: "progressbar", "aria-valuenow": width, "aria-valuemin": 0, "aria-valuemax": 100 },
    h("i", { style: `width:${width}%` }));
}

// ---------- 登录 ----------

function showLogin() {
  closeStream();
  clearTimeout(state.pollTimer);
  state.pollTimer = null;
  $("app").hidden = true;
  $("login").hidden = false;
  $("login-token").focus();
}

async function login(event) {
  event.preventDefault();
  const error = $("login-error");
  error.hidden = true;
  const response = await fetch("/api/login", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ token: $("login-token").value }),
  });
  if (!response.ok) {
    error.textContent = response.status === 401 ? "token 不正确。" : `登录失败（HTTP ${response.status}）`;
    error.hidden = false;
    return;
  }
  $("login-token").value = "";
  await start();
}

async function logout() {
  try { await api("/api/logout", { method: "POST" }); } catch { /* 忽略 */ }
  showLogin();
}

// ---------- 侧栏：媒体库 ----------

async function loadListing(path) {
  const data = await api(path ? `/api/browse?path=${encodeURIComponent(path)}` : "/api/browse");
  if (!state.listing || state.listing.path !== data.path) state.checked.clear();
  state.listing = data;
  renderListing();
  renderBatch();
}

function renderListing() {
  const data = state.listing;
  // 路径
  const crumbs = [];
  if (state.config.roots.length > 1 || data.path === null) {
    crumbs.push(h("a", { href: "#/", "aria-current": data.path === null ? "page" : null }, "全部"));
  }
  if (data.path !== null) {
    const root = rootOf(data.path);
    let current = root;
    const parts = [basename(root), ...data.path.slice(root.length).split("/").filter(Boolean)];
    parts.forEach((part, index) => {
      if (index > 0) current = `${current}/${part}`;
      if (crumbs.length) crumbs.push(h("span", { "aria-hidden": "true" }, "/"));
      crumbs.push(h("a", { href: `#/browse/${encodeURIComponent(current)}`, "aria-current": index === parts.length - 1 ? "page" : null }, part));
    });
  }
  $("crumbs").replaceChildren(...crumbs);

  // 条目
  const items = [];
  const singleRootTop = state.config.roots.length === 1 && data.parent === null;
  if (data.path !== null && !singleRootTop) {
    const target = data.parent ? `#/browse/${encodeURIComponent(data.parent)}` : "#/";
    items.push(h("li", { class: "entry up" }, h("span", { class: "spacer" }), h("a", { href: target }, icon("up"), h("span", { class: "name" }, "上一级"))));
  }
  for (const entry of data.entries) {
    const checked = state.checked.has(entry.path);
    const box = h("input", { type: "checkbox", "aria-label": `勾选 ${entry.name}`, checked });
    box.addEventListener("change", () => {
      if (box.checked) state.checked.add(entry.path); else state.checked.delete(entry.path);
      renderBatch();
    });
    items.push(h("li", { class: `entry ${entry.kind}${state.selected === entry.path ? " on" : ""}` },
      box,
      h("a", { href: `#/browse/${encodeURIComponent(entry.path)}`, title: entry.path },
        icon({ dir: "folder", dvd: "disc", iso: "iso" }[entry.kind] || "folder"),
        h("span", { class: "name" }, data.path === null ? shortPath(entry.path) : entry.name),
        entry.kind !== "dir" ? h("span", { class: "tag" }, KIND_LABEL[entry.kind]) : null)));
  }
  if (!data.entries.length) items.push(h("li", { class: "side-empty" }, "这里没有子目录或 ISO 文件"));
  $("entries").replaceChildren(...items);
  $("check-all").hidden = !data.entries.length;
}

function checkAll() {
  const paths = state.listing.entries.map((e) => e.path);
  const all = paths.every((p) => state.checked.has(p));
  paths.forEach((p) => (all ? state.checked.delete(p) : state.checked.add(p)));
  renderListing();
  renderBatch();
}

function renderBatch() {
  const n = state.checked.size;
  $("batch").hidden = n === 0;
  $("batch-count").textContent = `已选 ${n} 项`;
}

// ---------- 侧栏：任务 ----------

async function refreshJobs() {
  try {
    state.jobs = await api("/api/jobs");
  } catch (error) {
    if (!(error instanceof AuthError)) schedulePoll();
    return;
  }
  renderTasks();
  // 当前打开的任务：更新顶部进度
  if (state.route && state.route.name === "job") {
    const job = state.jobs.find((j) => j.id === state.route.id);
    if (job) updateJobHero(job);
  }
  schedulePoll();
}

function schedulePoll() {
  clearTimeout(state.pollTimer);
  const active = state.jobs.some((j) => j.status === "queued" || j.status === "running");
  state.pollTimer = setTimeout(refreshJobs, active ? 1500 : 6000);
}

function renderTasks() {
  $("tasks-empty").hidden = state.jobs.length > 0;
  $("task-count").textContent = state.jobs.length ? String(state.jobs.length) : "";
  const currentId = state.route && state.route.name === "job" ? state.route.id : null;
  $("tasks").replaceChildren(...state.jobs.map((job) => {
    const status = statusOf(job);
    return h("li", {}, h("a", { class: "task", href: `#/job/${job.id}`, title: job.path, "aria-current": job.id === currentId ? "page" : null },
      h("span", { class: "top" }, icon(job.kind === "run" ? "image" : "peers"), h("span", { class: "name" }, basename(job.path))),
      h("span", { class: "sub" }, `${JOB_SHORT[job.kind]} · ${clock(job.created_at)} · ${status.text}`),
      progressBar(job)));
  }));
}

// ---------- 提交任务 ----------

function readOptions() {
  const count = Number($("opt-count")?.value || state.opts.count);
  const upload = $("opt-upload") ? $("opt-upload").checked : state.opts.upload;
  const tracker = $("opt-tracker") ? $("opt-tracker").value : state.opts.tracker;
  const piece = Number($("opt-piece")?.value || state.opts.piece);
  const seedName = $("opt-seedname") ? $("opt-seedname").value.trim() : "";
  state.opts = { count, upload, tracker, piece, seedName };
  return state.opts;
}

function jobBodies(kind, path, single) {
  const opts = state.opts;
  // 发种名称只用于单个来源：批量提交时各自用原名
  const seed_name = single ? opts.seedName || "" : "";
  const run = { kind: "run", path, count: opts.count, upload: opts.upload, seed_name };
  const chosen = single ? state.titles.get(path) : null;
  if (chosen) {
    const t = chosen.match;
    run.title = { title: t.title, original_title: t.original_title, original_language: t.original_language, year: t.year, imdb_id: t.imdb_id, tmdb_url: t.url };
    run.region = chosen.region;
    run.edition = chosen.edition;
  }
  const torrent = { kind: "torrent", path, announces: opts.tracker.split(/\s+/).filter(Boolean), piece_length: opts.piece, seed_name };
  return kind === "both" ? [run, torrent] : kind === "run" ? [run] : [torrent];
}

async function submit(kind, paths) {
  readOptions();
  const bodies = paths.flatMap((path) => jobBodies(kind, path, paths.length === 1));
  let first = null;
  try {
    for (const body of bodies) {
      const job = await api("/api/jobs", { json: body });
      first = first || job;
    }
  } catch (error) {
    if (!(error instanceof AuthError)) toast(error.message);
    await refreshJobs();
    return;
  }
  toast(bodies.length > 1 ? `已加入队列：${bodies.length} 个任务` : "已开始");
  await refreshJobs();
  if (first) location.hash = `#/job/${first.id}`;
}

async function onBatch(event) {
  const action = event.target.closest("[data-batch]")?.dataset.batch;
  if (!action) return;
  if (action === "clear") {
    state.checked.clear();
    renderListing();
    renderBatch();
    return;
  }
  const paths = [...state.checked];
  state.checked.clear();
  renderListing();
  renderBatch();
  await submit(action, paths);
}

// ---------- 主区域：通用 ----------

function hero({ eyebrow, title, meta = [], backdrop = null, compact = false, children = [] }) {
  const bg = h("div", { class: `backdrop${backdrop ? "" : " fallback"}`, "aria-hidden": "true" });
  if (backdrop) bg.style.backgroundImage = `url("${backdrop}")`;
  return h("section", { class: `hero${compact ? " compact" : ""}` }, bg,
    eyebrow ? h("p", { class: "eyebrow" }, eyebrow) : null,
    h("h1", {}, title),
    meta.length ? h("div", { class: "meta" }, meta) : null,
    children);
}

function optionsRow() {
  const o = state.opts;
  const pieces = [];
  const [low, high] = state.config.piece_length_range;
  for (let n = low; n <= high; n += 1) {
    pieces.push(h("option", { value: n, selected: n === o.piece }, n === 24 ? `${pieceLabel(n)}（默认）` : pieceLabel(n)));
  }
  return h("div", { class: "options" },
    h("label", {}, "截图", h("input", { id: "opt-count", type: "number", min: 1, max: 100, value: o.count }), h("span", { class: "hint" }, "张/盘")),
    h("label", { class: "switch" }, h("input", { id: "opt-upload", type: "checkbox", checked: o.upload }), h("span", { class: "track" }), "上传 Pixhost 并生成发布说明"),
    h("label", {}, "Tracker", h("input", { id: "opt-tracker", type: "text", value: o.tracker, placeholder: "可留空，多个用空格分隔", spellcheck: "false" })),
    h("label", {}, "分块", h("select", { id: "opt-piece" }, pieces)),
    state.config.seed_dir ? h("label", { class: "wide", title: `用硬链接放到发种目录 ${state.config.seed_dir.path}，原始下载不动` },
      "发种名称", h("input", { id: "opt-seedname", type: "text", value: "", placeholder: "留空用原名；例如 IMDb 片名和年份", spellcheck: "false" }),
      h("span", { class: "hint" }, "最外层文件夹名")) : null);
}

// ---------- 查片名（TMDB）----------

function titlePanel(path, info) {
  const heading = h("h2", { class: "section-title" }, "片名");
  if (!info.tmdb) {
    return h("section", {}, heading, notice("", "在设置页面填写 TMDB API Key 后，可以在这里查片名，按英文名和原名给出 PTP 发种名称和 BHD 标题。"));
  }
  const query = h("input", { type: "search", value: info.guess.query, placeholder: "片名（英文、原名或俄文都可以）", spellcheck: "false" });
  const year = h("input", { type: "number", value: info.guess.year || "", placeholder: "年份", min: 1870, max: 2100 });
  const go = h("button", { type: "button", class: "btn small glass" }, icon("search"), "查 TMDB");
  const results = h("div", { class: "title-results" });
  const chosenBox = h("div", {});
  const search = async () => {
    go.disabled = true;
    results.replaceChildren(h("span", { class: "hint" }, "查询中…"));
    try {
      const params = new URLSearchParams({ q: query.value.trim() });
      if (year.value) params.set("year", year.value);
      const data = await api(`/api/tmdb/search?${params}`);
      results.replaceChildren(...(data.results.length ? data.results.map((m) => {
        const pick = h("button", { type: "button", class: "title-option" },
          h("b", {}, `${m.title}${m.year ? ` (${m.year})` : ""}`),
          h("span", {}, [m.kind === "tv" ? "剧集" : "电影", m.original_title && m.original_title !== m.title ? m.original_title : null].filter(Boolean).join(" · ")));
        pick.addEventListener("click", () => choose(m, pick));
        return pick;
      }) : [h("span", { class: "hint" }, "没有找到，换个写法或去掉年份再试")]));
    } catch (error) {
      results.replaceChildren();
      if (!(error instanceof AuthError)) toast(error.message);
    } finally {
      go.disabled = false;
    }
  };
  const choose = async (m, button) => {
    button.disabled = true;
    try {
      const detail = await api(`/api/tmdb/${m.kind}/${m.id}?disc=${encodeURIComponent(info.disc_kind)}`);
      const previous = state.titles.get(path);
      state.titles.set(path, { match: detail, region: previous?.region || "", edition: previous?.edition || "" });
      const seedBox = $("opt-seedname");
      if (seedBox && !seedBox.value.trim()) seedBox.value = detail.ptp_name;
      renderChosen();
    } catch (error) {
      if (!(error instanceof AuthError)) toast(error.message);
    } finally {
      button.disabled = false;
    }
  };
  const renderChosen = () => {
    const chosen = state.titles.get(path);
    if (!chosen) { chosenBox.replaceChildren(); return; }
    const t = chosen.match;
    const bhd = h("code", {});
    const updateBhd = () => {
      bhd.textContent = [t.bhd_head, chosen.edition, chosen.region, "‹PAL/NTSC›", info.disc_kind, "MPEG-2", "‹音轨›"].filter(Boolean).join(" ");
    };
    const field = (key, placeholder) => {
      const box = h("input", { type: "text", value: chosen[key], placeholder, spellcheck: "false" });
      box.addEventListener("input", () => { chosen[key] = box.value.trim(); updateBhd(); });
      return box;
    };
    updateBhd();
    const fill = h("button", { type: "button", class: "btn small glass" }, "填入发种名称");
    fill.addEventListener("click", () => { const box = $("opt-seedname"); if (box) { box.value = t.ptp_name; box.focus(); } });
    const clear = h("button", { type: "button", class: "btn small glass" }, "不用这个");
    clear.addEventListener("click", () => { state.titles.delete(path); renderChosen(); });
    chosenBox.replaceChildren(h("div", { class: "title-chosen" },
      h("p", {}, h("b", {}, `${t.title}${t.year ? ` (${t.year})` : ""}`),
        t.original_title && t.original_title !== t.title ? h("span", {}, ` · 原名 ${t.original_title}`) : null,
        h("a", { href: t.url, target: "_blank", rel: "noopener noreferrer" }, "TMDB"),
        t.imdb_url ? h("a", { href: t.imdb_url, target: "_blank", rel: "noopener noreferrer" }, `IMDb ${t.imdb_id}`) : h("span", { class: "hint" }, "TMDB 中没有 IMDb 编号")),
      h("dl", {},
        h("dt", {}, "PTP 发种名称"), h("dd", {}, h("code", {}, t.ptp_name),
          state.config.seed_dir ? fill : h("span", { class: "hint" }, "改文件夹名需要先在设置页面填写发种目录"),
          copyButton("复制", async () => t.ptp_name)),
        h("dt", {}, "BHD 标题"), h("dd", {}, bhd, h("span", { class: "hint" }, "制式和音轨在生成截图后补全，任务结果中给出完整标题")),
        h("dt", {}, "版本"), h("dd", {}, field("edition", "可留空，例如 Director's Cut")),
        h("dt", {}, "地区或发行商"), h("dd", {}, field("region", "可留空，例如 RUS、Criterion Collection"))),
      h("p", { class: "hint" }, "生成截图时会带上这个片名；IMDb 名和 TMDB 名偶尔不同，PTP 以 IMDb 为准，请点链接核对。"),
      clear));
  };
  go.addEventListener("click", search);
  query.addEventListener("keydown", (event) => { if (event.key === "Enter") search(); });
  renderChosen();
  const from = info.guess.from === "release" ? "（按资源标题猜的）" : "（按文件夹名猜的）";
  return h("section", {}, heading,
    h("div", { class: "title-search" }, query, year, go, h("span", { class: "hint" }, from)),
    results, chosenBox);
}

// 来源所在的浏览目录和发种目录不在同一个文件系统时，无法建立硬链接
function seedDirProblem(path) {
  const seed = state.config.seed_dir;
  if (!seed) return null;
  if (seed.error) return seed.error;
  const root = rootOf(path);
  return root && seed.other_filesystem.includes(root)
    ? `${root} 和发种目录 ${seed.path} 不在同一个文件系统（或分属不同的挂载卷），无法建立硬链接，处理和做种会失败。请把发种目录设在同一个分区上；Docker 中两者要在同一个挂载卷里。`
    : null;
}

function setMain(...nodes) {
  $("main").replaceChildren(...nodes);
}

async function firstScreenshot(path) {
  // 同一来源最近一次成功的截图任务里的第一张截图，用作背景
  const job = state.jobs.find((j) => j.path === path && j.kind === "run" && j.status === "done");
  if (!job) return null;
  const detail = await jobDetail(job.id);
  for (const disc of detail.result?.discs || []) {
    const shot = (disc.screenshots || []).find((s) => s.ok);
    if (shot) return fileUrl(detail, shot.file);
  }
  return null;
}

async function jobDetail(id, fresh = false) {
  if (!fresh && state.details.has(id)) return state.details.get(id);
  const detail = await api(`/api/jobs/${id}`);
  if (detail.status === "done" || detail.status === "failed") state.details.set(id, detail);
  return detail;
}

function fileUrl(job, name) {
  return `/api/jobs/${job.id}/files/${encodeURIComponent(name)}`;
}

// ---------- 页面：来源 ----------

async function showSource(path) {
  state.selected = path;
  const data = await api(`/api/browse?path=${encodeURIComponent(path)}`);
  // 目录直接在侧栏展开；DVD 文件夹和 ISO 留在上一级列表中高亮
  if (data.kind === "dir" || data.parent === null) {
    if (!state.listing || state.listing.path !== data.path) state.checked.clear();
    state.listing = data;
    renderListing();
    renderBatch();
  } else if (!state.listing || state.listing.path !== data.parent) {
    await loadListing(data.parent);
  } else {
    renderListing();
  }

  const [info, backdrop] = await Promise.all([api(`/api/source?path=${encodeURIComponent(path)}`), firstScreenshot(path)]);
  if (state.selected !== path) return;
  const types = [...new Set(info.discs.map((d) => d.media_type))].join(" + ");
  const meta = info.discs.length
    ? [h("span", {}, `${info.discs.length} 张盘`), h("span", {}, types), h("span", {}, formatBytes(info.total_bytes))]
    : [h("span", {}, "这里没有找到 DVD")];
  meta.push(h("span", { class: "path", title: path }, shortPath(path)));

  const hasDiscs = info.discs.length > 0;
  const actions = h("div", { class: "actions" },
    h("button", { type: "button", class: "btn light", disabled: !hasDiscs, onclick: () => submit("run", [path]) }, icon("play"), "生成截图与 MediaInfo"),
    h("button", { type: "button", class: "btn glass", onclick: () => submit("torrent", [path]) }, "做种"),
    h("button", { type: "button", class: "btn glass", disabled: !hasDiscs, onclick: () => submit("both", [path]) }, "两者都做"));

  const sections = [];
  const seedProblem = seedDirProblem(path);
  if (seedProblem) sections.push(notice("bad", seedProblem));
  if (hasDiscs) {
    sections.push(h("section", {}, h("h2", { class: "section-title" }, "包含的盘"),
      h("div", { class: "cards" }, info.discs.map((d) =>
        h("div", { class: "card", title: d.path }, h("b", {}, d.name),
          h("span", {}, `${d.media_type} · ${KIND_LABEL[d.kind]} · ${formatBytes(d.bytes)}`),
          h("span", { class: "where" }, shortPath(d.kind === "iso" ? d.path : parentOf(d.path))))))));
  }
  if (hasDiscs) sections.push(titlePanel(path, info));
  const related = state.jobs.filter((j) => j.path === path);
  if (related.length) {
    sections.push(h("section", {}, h("h2", { class: "section-title" }, "这个来源的任务"),
      h("div", { class: "cards" }, related.map((j) => {
        const status = statusOf(j);
        return h("a", { class: "card", href: `#/job/${j.id}` }, h("b", {}, JOB_LABEL[j.kind]), h("span", {}, `${clock(j.created_at)} · ${status.text}`));
      }))));
  }
  setMain(
    hero({ eyebrow: KIND_LABEL[info.kind] || "目录", title: info.name || path, meta, backdrop, children: [actions, optionsRow()] }),
    h("div", { class: "content" }, sections));
  const chosen = state.titles.get(path);
  if (chosen && $("opt-seedname")) $("opt-seedname").value = chosen.match.ptp_name;
}

// ---------- 页面：任务 ----------

function closeStream() {
  if (state.stream) state.stream.close();
  state.stream = null;
}

function updateJobHero(summary) {
  const pill = $("job-pill");
  if (!pill) return;
  const status = statusOf(summary);
  pill.className = `pill ${status.cls}`;
  pill.textContent = status.text;
  const bar = $("job-bar");
  if (bar) {
    const fresh = progressBar(summary);
    fresh.id = "job-bar";
    fresh.style.cssText = bar.style.cssText;
    bar.replaceWith(fresh);
  }
}

/** 按创建时间排在当前任务之后的下一个已完成任务，到末尾后从头开始。 */
function nextDoneJob(currentId) {
  const done = state.jobs.filter((j) => j.status === "done").slice().reverse();
  const index = done.findIndex((j) => j.id === currentId);
  const next = done[(index + 1) % done.length];
  return next && next.id !== currentId ? next : null;
}

async function showJob(id, tab) {
  closeStream();
  let job;
  try {
    job = await jobDetail(id, true);
  } catch (error) {
    if (!(error instanceof AuthError)) setMain(h("div", { class: "content", style: "padding-top:48px" }, notice("bad", error.message)));
    return;
  }
  if (state.route.name !== "job" || state.route.id !== id) return;
  state.selected = job.path;
  renderTasks();
  if (state.listing && state.listing.path !== null) renderListing();

  const finished = job.status === "done" || job.status === "failed";
  const status = statusOf(job);
  const next = finished ? nextDoneJob(id) : null;
  let backdrop = null;
  if (job.kind === "run" && job.result) {
    const disc = job.result.discs.find((d) => (d.screenshots || []).some((s) => s.ok));
    if (disc) backdrop = fileUrl(job, disc.screenshots.find((s) => s.ok).file);
  }
  const heroNode = hero({
    eyebrow: JOB_LABEL[job.kind],
    title: basename(job.path),
    compact: true,
    backdrop,
    meta: [h("span", {}, h("span", { id: "job-pill", class: `pill ${status.cls}` }, status.text)), h("span", {}, clock(job.created_at)), h("span", { class: "path", title: job.path }, shortPath(job.path))],
    children: [
      finished ? null : Object.assign(progressBar(job), { id: "job-bar", style: "margin-top:16px;max-width:520px" }),
      h("div", { class: "actions" },
        h("a", { class: "btn glass small", href: `#/browse/${encodeURIComponent(job.path)}` }, icon("up"), "回到来源"),
        next ? h("a", { class: "btn glass small", href: `#/job/${next.id}` }, "下一个完成的任务", icon("next")) : null),
    ],
  });

  const tabs = h("div", { class: "tabs", role: "tablist" },
    h("a", { role: "tab", href: `#/job/${id}`, "aria-selected": String(tab !== "log") }, "结果"),
    h("a", { role: "tab", href: `#/job/${id}/log`, "aria-selected": String(tab === "log") }, "日志", h("span", { id: "log-count", class: "count" }, job.events.length || "")));

  const log = h("pre", { id: "job-log", class: "log" });
  const appendLog = (event) => {
    const near = log.scrollHeight - log.scrollTop - log.clientHeight < 40;
    log.append(h("span", { class: event.level === "error" ? "e" : null }, `${event.message}\n`));
    const count = $("log-count");
    if (count) count.textContent = String(log.childElementCount);
    if (near) log.scrollTop = log.scrollHeight;
  };
  job.events.forEach(appendLog);
  if (!job.events.length && job.status === "queued") {
    const ahead = state.jobs.filter((j) => j.id !== id && (j.status === "running" || (j.status === "queued" && j.created_at < job.created_at))).length;
    log.append(`排队中，前面还有 ${ahead} 个任务。同一时间最多运行 ${state.config.max_jobs} 个。\n`);
  }

  let body;
  if (tab === "log") body = log;
  else if (!finished) body = notice("", "任务进行中，完成后这里显示结果。进度可以在“日志”里看。");
  else body = h("div", { style: "display:flex;flex-direction:column;gap:32px" }, renderResult(job));

  setMain(heroNode, h("div", { class: "content" }, h("div", {}, tabs), body));
  if (tab === "log") log.scrollTop = log.scrollHeight;

  if (!finished) {
    const stream = new EventSource(`/api/jobs/${id}/events?after=${job.events.length}`);
    state.stream = stream;
    stream.addEventListener("log", (message) => { if (tab === "log") appendLog(JSON.parse(message.data)); else { const c = $("log-count"); if (c) c.textContent = String(Number(c.textContent || 0) + 1); } });
    stream.addEventListener("end", async () => {
      closeStream();
      await refreshJobs();
      if (state.route.name === "job" && state.route.id === id) showJob(id, tab);
    });
    stream.addEventListener("error", () => {
      if (stream.readyState === EventSource.CLOSED) api("/api/config").catch(() => {});
    });
  }
}

// 做种的数据在哪里，以及“添加到 qB 做种”
function seedRow(job, result) {
  const qb = state.config.qbit;
  const linked = state.config.seed_dir && result.seed_path.startsWith(`${state.config.seed_dir.path}/`);
  const where = h("span", {}, `${linked ? "发种目录" : "原始下载"}：`, h("code", { class: "path" }, result.seed_path));
  if (!qb) return h("p", { class: "seed-row" }, where, h("span", { class: "hint" }, "配置 qBittorrent 后可以一键添加做种"));
  const button = h("button", { type: "button", class: "btn glass", disabled: !!result.seeded },
    result.seeded ? "已添加到 qBittorrent" : "添加到 qB 做种");
  button.title = `添加到分类 ${qb.seed_category}，跳过校验直接做种`;
  button.addEventListener("click", async () => {
    button.disabled = true;
    try {
      const r = await api(`/api/jobs/${job.id}/seed`, { method: "POST" });
      result.seeded = true;
      button.textContent = "已添加到 qBittorrent";
      toast(r.added ? `已添加到 qBittorrent（分类 ${r.category}，保存路径 ${r.save_path}）` : "qBittorrent 中已有这个种子");
    } catch (error) {
      button.disabled = false;
      if (!(error instanceof AuthError)) toast(error.message);
    }
  });
  return h("p", { class: "seed-row" }, where, button);
}

function renderResult(job) {
  const nodes = [];
  if (job.status === "failed") nodes.push(notice("bad", job.error || "任务失败"));
  const result = job.result;
  if (!result) return nodes;

  if (job.kind === "torrent") {
    const n = job.params.announces.length;
    nodes.push(h("div", { class: "file" }, icon("peers"),
      h("div", { class: "t" }, h("b", {}, result.torrent_file),
        h("span", {}, `private · 分块 ${pieceLabel(job.params.piece_length)} · ${n ? `${n} 个 Tracker` : "未填写 Tracker"}`)),
      h("a", { class: "btn amber", href: fileUrl(job, result.torrent_file), download: result.torrent_file }, icon("down"), "下载种子")));
    if (result.seed_path) nodes.push(seedRow(job, result));
    const extra = result.extra_files || [];
    if (extra.length) {
      nodes.push(notice("warn", h("div", {},
        h("b", {}, `发现 ${extra.length} 个和上传无关的文件，建议删除后重新做种（PTP 2.1.3）`),
        h("ul", { class: "extra-files" }, extra.map((f) => h("li", {}, h("code", {}, f.path), `（${f.reason}）`))))));
    }
    return nodes;
  }

  const shots = result.discs.flatMap((d) => d.screenshots || []);
  nodes.push(h("p", { class: "summary" },
    h("span", {}, h("b", {}, result.discs.length), " 张盘"),
    h("span", {}, "截图 ", h("b", {}, `${shots.filter((s) => s.ok).length} / ${shots.length}`)),
    job.params.upload ? h("span", {}, "已上传 ", h("b", {}, shots.filter((s) => s.url).length), " 张") : h("span", {}, "未上传图床")));
  if (result.names) {
    const n = result.names;
    nodes.push(h("section", { class: "code" },
      h("header", {}, "BHD 标题", h("span", { class: "sp" }),
        n.imdb_id ? h("a", { class: "fmt", href: `https://www.imdb.com/title/${n.imdb_id}/`, target: "_blank", rel: "noopener noreferrer" }, `IMDb ${n.imdb_id}`) : null,
        copyButton("复制", async () => n.bhd, "amber")),
      h("pre", {}, n.bhd)));
    if (!n.audio) nodes.push(notice("warn", "MediaInfo 中没有读到音轨，BHD 标题缺少音轨部分，请手动补上。"));
  }
  if (result.seed_path && state.config.seed_dir && result.seed_path.startsWith(`${state.config.seed_dir.path}/`)) {
    nodes.push(h("p", { class: "seed-row" }, "发种目录：", h("code", { class: "path" }, result.seed_path)));
  }

  if (result.post) {
    nodes.push(h("section", { class: "code" },
      h("header", {}, "发布说明", h("span", { class: "sp" }), h("span", { class: "fmt" }, "BBCode"), copyButton("复制", async () => result.post, "amber")),
      h("pre", {}, result.post)));
  } else if (job.params.upload) {
    nodes.push(notice("warn", "截图没有全部上传成功，未生成发布说明。请查看日志后重新运行。"));
  } else {
    nodes.push(notice("", "这次没有上传图床，所以没有生成发布说明。"));
  }

  for (const disc of result.discs) {
    const section = h("section", { style: "display:flex;flex-direction:column;gap:18px" });
    const pills = disc.vob ? [
      h("span", { class: "pill mono" }, `${disc.media_type} · ${disc.standard || `高度 ${disc.height}`}`),
      h("span", { class: "pill mono" }, `${disc.size[0]}×${disc.size[1]}`),
      h("span", { class: "pill mono" }, duration(disc.duration)),
    ] : [];
    section.append(h("h2", { class: "section-title", style: "margin:0" }, disc.name || disc.label, pills));
    if (disc.error) section.append(notice("bad", disc.error));
    if (disc.screenshots) {
      section.append(h("div", { class: "gallery" }, disc.screenshots.map((shot) => {
        if (!shot.ok) return h("div", { class: "shot fail" }, "截图失败", h("span", {}, timecode(shot.at)));
        const url = fileUrl(job, shot.file);
        return h("figure", { class: "shot" },
          h("a", { href: url, target: "_blank", rel: "noopener", title: shot.file }, h("img", { src: url, alt: `${shot.file}，${timecode(shot.at)}`, loading: "lazy" })),
          h("figcaption", {}, h("span", {}, timecode(shot.at)),
            shot.url ? h("a", { href: shot.url, target: "_blank", rel: "noopener noreferrer" }, "直链", icon("link")) : null));
      })));
    }
    if (disc.vob) {
      section.append(h("div", { class: "two" },
        disc.mediainfo_file ? mediainfoPanel(job, disc) : h("div"),
        h("dl", { class: "facts" },
          h("dt", {}, "容量"), h("dd", {}, `${disc.media_type} · ${formatBytes(disc.total_bytes)}`),
          h("dt", {}, "制式"), h("dd", {}, disc.standard || `未知（高度 ${disc.height}）`),
          h("dt", {}, "截图尺寸"), h("dd", {}, `${disc.width}×${disc.height} → ${disc.size[0]}×${disc.size[1]}`),
          h("dt", {}, "PAR"), h("dd", {}, disc.par),
          h("dt", {}, "VOB"), h("dd", {}, h("code", {}, disc.vob), ` · ${duration(disc.duration)}`),
          h("dt", {}, "IFO"), h("dd", {}, h("code", {}, disc.ifo || "无")),
          h("dt", {}, "来源"), h("dd", { title: disc.source }, h("code", {}, shortPath(disc.source))))));
    }
    nodes.push(section);
  }
  return nodes;
}

function mediainfoPanel(job, disc) {
  let text = null;
  const pre = h("pre", { hidden: true });
  const load = async () => {
    if (text === null) {
      pre.textContent = "加载中…";
      text = await api(fileUrl(job, disc.mediainfo_file));
      pre.textContent = text;
    }
    return text;
  };
  const panel = h("section", { class: "code collapsed" });
  const toggle = h("button", { type: "button", class: "toggle", "aria-expanded": "false" }, icon("chev"), "MediaInfo");
  toggle.addEventListener("click", async () => {
    const open = toggle.getAttribute("aria-expanded") !== "true";
    toggle.setAttribute("aria-expanded", String(open));
    panel.classList.toggle("collapsed", !open);
    pre.hidden = !open;
    if (open) await load();
  });
  panel.append(h("header", {}, toggle, h("span", { class: "sp" }), h("span", { class: "fmt" }, "VOB + IFO"), copyButton("复制", load)), pre);
  return panel;
}

// ---------- 页面：设置、空白 ----------

// ---------- 设置 ----------

const PATH_MAP_HINT = "每行一条：qB 中的路径 = whatdvd 中的路径";

async function showSettings() {
  state.selected = null;
  if (state.listing) renderListing();
  const data = await api("/api/settings");
  if (!state.route || state.route.name !== "settings") return;
  renderSettings(data);
}

function renderSettings(data) {
  const v = data.values;
  const o = data.options;
  const f = data.fixed;
  const ctl = {};   // 表单控件，按 "表.键" 存放

  const row = (label, control, hint) => h("label", { class: "form-row" },
    h("span", { class: "form-label" }, label),
    h("span", { class: "form-control" }, control, hint ? h("small", {}, hint) : null));
  const input = (key, value, attrs = {}) => (ctl[key] = h("input", { type: "text", value, spellcheck: "false", ...attrs }));
  const number = (key, value, min, max) => input(key, String(value), { type: "number", min, max });
  const select = (key, value, options) => (ctl[key] = h("select", {},
    options.map(([val, label]) => h("option", { value: val, selected: String(val) === String(value) }, label))));
  const toggle = (key, checked, label) => {
    ctl[key] = h("input", { type: "checkbox", checked });
    return h("span", { class: "switch" }, ctl[key], h("span", { class: "track" }), label);
  };
  const area = (key, value, rows, placeholder) => (ctl[key] = h("textarea", { rows, spellcheck: "false", placeholder }, value));
  const secret = (key, isSet, placeholder) => {
    ctl[key] = h("input", { type: "password", autocomplete: "new-password", placeholder: isSet ? "已设置，留空不修改" : placeholder });
    if (!isSet) return ctl[key];
    ctl[`${key}.clear`] = h("input", { type: "checkbox" });
    return h("span", { class: "inline" }, ctl[key], h("label", { class: "check" }, ctl[`${key}.clear`], "清除"));
  };
  const section = (title, desc, ...rows) => h("section", { class: "form-section" },
    h("h2", {}, title), desc ? h("p", { class: "form-desc" }, desc) : null, ...rows);

  const pieces = [];
  for (let n = o.piece_length_range[0]; n <= o.piece_length_range[1]; n += 1) pieces.push([n, `${pieceLabel(n)}${n === 24 ? "（默认）" : ""}`]);

  // qB 测试
  const qbResult = h("small", { class: "test-result" });
  const qbTest = h("button", { type: "button", class: "btn small glass" }, "测试连接");
  qbTest.addEventListener("click", () => testConnection(qbTest, qbResult, "/api/settings/test/qbittorrent", {
    url: ctl["qbittorrent.url"].value.trim(),
    username: ctl["qbittorrent.username"].value,
    password: secretValue(ctl, "qbittorrent.password"),
  }, (r) => `连接成功，qBittorrent ${r.version}`));

  // rutor 测试：搜索 DVD9 第 1 页
  const ruResult = h("small", { class: "test-result" });
  const ruTest = h("button", { type: "button", class: "btn small glass" }, "测试连接");
  ruTest.addEventListener("click", () => testConnection(ruTest, ruResult, "/api/settings/test/rutor", {
    url: ctl["rutor.url"].value.trim(),
  }, (r) => `连接成功，搜索 DVD9 共 ${r.total} 条结果`));

  // Jackett 测试：成功后把已配置的站点填进下拉框
  const jkResult = h("small", { class: "test-result" });
  const jkTest = h("button", { type: "button", class: "btn small glass" }, "测试连接");
  const indexerList = h("datalist", { id: "jackett-indexers" }, h("option", { value: "all" }, "全部已配置的站点"));
  jkTest.addEventListener("click", () => testConnection(jkTest, jkResult, "/api/settings/test/jackett", {
    url: ctl["jackett.url"].value.trim(),
    api_key: secretValue(ctl, "jackett.api_key"),
  }, (r) => {
    indexerList.replaceChildren(h("option", { value: "all" }, "全部已配置的站点"),
      ...r.indexers.map((i) => h("option", { value: i.id }, i.name)));
    return r.indexers.length ? `连接成功，已配置的站点：${r.indexers.map((i) => `${i.name}（${i.id}）`).join("、")}` : "连接成功，但 Jackett 中还没有配置站点";
  }));

  const tmResult = h("small", { class: "test-result" });
  const tmTest = h("button", { type: "button", class: "btn small glass" }, "测试");
  tmTest.addEventListener("click", () => testConnection(tmTest, tmResult, "/api/settings/test/tmdb", {
    api_key: secretValue(ctl, "tmdb.api_key"),
  }, () => "API Key 有效"));

  const pathMap = Object.entries(v.qbittorrent.path_map).map(([a, b]) => `${a} = ${b}`).join("\n");
  const form = h("form", { class: "settings-form", novalidate: true },
    section("截图", null,
      row("每盘张数", number("screenshots.count", v.screenshots.count, 1, 100)),
      row("比例修正", select("screenshots.aspect", v.screenshots.aspect, o.aspect_modes.map((m) => [m, ASPECT_LABELS[m] || m]))),
      row("剔除黑屏", toggle("screenshots.dark_filter", v.screenshots.dark_filter, "多截一张删掉最小的，小于 120 KB 的换时间点重截"))),
    section("图床", null,
      row("Pixhost 域名", select("pixhost.domain", v.pixhost.domain, o.pixhost_domains.map((d) => [d, d]))),
      row("代理", input("pixhost.proxy", v.pixhost.proxy, { placeholder: "例如 http://127.0.0.1:7890，留空不用" }))),
    section("做种", null,
      row("默认 Tracker", area("torrent.announces", v.torrent.announces.join("\n"), 3, "每行一个，可留空"), "做种表单里预填"),
      row("默认分块", select("torrent.piece_length", v.torrent.piece_length, pieces))),
    section("发布说明模板", "每张盘套用一次。可用变量：$name（盘名）、$mediainfo、$screenshots（每张截图一行 [img]直链[/img]）。留空使用默认 BBCode 模板。",
      row("模板", area("post.template_text", v.post.template_text, 8, data.default_template),
        v.post.template ? `配置文件中指定了模板文件 ${v.post.template}；这里填写后优先使用这里的内容。` : null)),
    section("任务", null,
      row("同时运行", number("max_jobs", v.max_jobs, 1, 8), "个任务；修改后立即生效"),
      row("ISO 临时目录", input("temp_dir", v.temp_dir, { placeholder: "留空使用系统临时目录" }), "ISO 解包会写入约 1 GB 的 VOB"),
      row("发种目录", input("seed_dir", v.seed_dir, { placeholder: "留空不使用，直接处理原始下载" }),
        "处理和做种前用硬链接把盘放到这里，可以另起最外层文件夹名，原始下载不动。必须和下载目录在同一个文件系统（Docker 中在同一个挂载卷里）")),
    section("qBittorrent", "填写地址即启用：下载“资源”页中选中的种子，并自动处理这个分类下下载完成的种子。只对接 Web API，不负责部署。",
      row("地址", h("span", { class: "inline" }, input("qbittorrent.url", v.qbittorrent.url, { placeholder: "例如 http://192.168.1.10:8080，留空不启用" }), qbTest), qbResult),
      row("用户名", input("qbittorrent.username", v.qbittorrent.username, { autocomplete: "off" })),
      row("密码", secret("qbittorrent.password", v.qbittorrent.password_set, "")),
      row("分类", input("qbittorrent.category", v.qbittorrent.category), "这个分类中下载完成的种子会被自动处理"),
      row("做种分类", input("qbittorrent.seed_category", v.qbittorrent.seed_category),
        "“添加到 qB 做种”用的分类，必须和上面的分类不同"),
      row("保存路径", input("qbittorrent.save_path", v.qbittorrent.save_path, { placeholder: "qB 中的路径；留空用分类或 qB 的默认路径" })),
      row("路径映射", area("qbittorrent.path_map", pathMap, 2, "/downloads = /media/qb"),
        `${PATH_MAP_HINT}。两边看到的路径一样时留空。映射后的目录必须在允许浏览的目录内。`),
      row("检查间隔", number("qbittorrent.interval", v.qbittorrent.interval, 10, 3600), "秒")),
    section("rutor 直连", "填写地址即启用：直接读取 rutor 的搜索页（公开站，不需要账号）。比通过 Jackett 多读翻页的结果，全面搜索能拿到几乎全部资源。启用后建议在 Jackett 中去掉 rutor，避免重复搜索。",
      row("地址", h("span", { class: "inline" },
        input("rutor.url", v.rutor.url, { placeholder: "https://rutor.info 或 https://rutor.is，留空不启用", list: "rutor-mirrors" }),
        h("datalist", { id: "rutor-mirrors" }, h("option", { value: "https://rutor.info" }), h("option", { value: "https://rutor.is" })),
        ruTest), ruResult),
      row("搜索关键词", input("rutor.queries", v.rutor.queries.join(" ")), "多个用空格分隔"),
      row("自动搜索", number("rutor.interval", v.rutor.interval, 0, 10080), "分钟一次，只读最新的一页；0 为只手动搜索"),
      row("只要影视类", toggle("rutor.films_only", v.rutor.films_only, "去掉音乐、其他（讲座、教程）、体育分类中的结果；每个关键词多搜这三个分类"))),
    section("Jackett", "填写地址和 API Key 即启用：在“资源”页搜索 DVD 原盘。请在 Jackett 中关掉 kinozal、rutracker 等俄语站点的“Strip Cyrillic Letters”和“Add RUS to end of all titles”：开着会删掉片名和 сжатый、Лицензия 等过滤用的标记。kinozal 的标题写“DVD-9”，搜索关键词要包含 DVD-9、DVD-5。",
      row("地址", h("span", { class: "inline" }, input("jackett.url", v.jackett.url, { placeholder: "例如 http://192.168.1.10:9117，留空不启用" }), jkTest), jkResult),
      row("API Key", secret("jackett.api_key", v.jackett.api_key_set, "Jackett 页面右上角的 API Key")),
      row("站点", h("span", {}, input("jackett.indexer", v.jackett.indexer, { list: "jackett-indexers" }), indexerList), "all 为全部已配置的站点；点“测试连接”后可以从列表中选"),
      row("搜索关键词", input("jackett.queries", v.jackett.queries.join(" ")), "多个用空格分隔"),
      row("自动搜索", number("jackett.interval", v.jackett.interval, 0, 10080), "分钟一次；0 为只手动搜索"),
      row("只要影视类", toggle("jackett.films_only", v.jackett.films_only, "只要分类 2000（电影）和 5000（电视剧、动画、纪录片），去掉音乐、培训、体育等；Jackett 的 rutor 不区分分类，无法过滤"))),
    section("TMDB", "填写 API Key 即启用：在来源页查片名，按 IMDb / TMDB 的英文名和原名给出 PTP 发种名称和 BHD 标题。API Key 在 themoviedb.org 的账号设置中免费申请，v3 API Key 和 v4 读取令牌都可以。",
      row("API Key", h("span", { class: "inline" }, secret("tmdb.api_key", v.tmdb.api_key_set, "TMDB 的 API Key 或读取令牌"), tmTest), tmResult)));

  const error = h("div", { class: "form-errors" });
  const save = h("button", { type: "submit", class: "btn amber" }, "保存");
  const reset = h("button", { type: "button", class: "btn glass" }, "全部恢复为配置文件");
  reset.hidden = data.overridden.length === 0;
  const status = h("span", { class: "save-status" },
    data.overridden.length ? `设置页面修改过 ${data.overridden.length} 项` : "所有项都来自配置文件或默认值");
  form.append(h("div", { class: "save-bar" }, save, reset, status));
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const changes = collectSettings(ctl, v, pathMap);
    if (changes === null) return;
    if (!Object.keys(changes).length) { toast("没有改动"); return; }
    await saveSettings(save, error, () => api("/api/settings", { method: "PUT", json: changes }), "已保存，立即生效");
  });
  reset.addEventListener("click", async () => {
    if (!confirm("去掉设置页面保存的所有修改，恢复为配置文件和默认值？")) return;
    await saveSettings(reset, error, () => api("/api/settings", { method: "DELETE" }), "已恢复为配置文件");
  });

  const fixedRow = (term, value) => [h("dt", {}, term), h("dd", {}, value)];
  const fixed = h("section", { class: "form-section" }, h("h2", {}, "服务"),
    h("p", { class: "form-desc" }, "以下几项只能在配置文件中修改，改后重启服务生效：它们关系到监听地址、登录和能访问哪些文件。"),
    h("dl", { class: "settings" },
      fixedRow("监听地址", f.listen),
      fixedRow("允许浏览的目录", f.roots.join("\n")),
      fixedRow("输出目录", f.output_dir),
      fixedRow("登录 token", f.token),
      fixedRow("数据库", f.database),
      fixedRow("配置文件", f.config_file || "无（全部使用默认值）"),
      fixedRow("设置页面的修改保存在", f.settings_file)));

  setMain(
    hero({ eyebrow: "whatdvd", title: "设置", compact: true, meta: [h("span", {}, "保存后立即生效，不用重启")] }),
    h("div", { class: "content" }, error, form, fixed));
}

function secretValue(ctl, key) {
  if (ctl[`${key}.clear`] && ctl[`${key}.clear`].checked) return "";
  return ctl[key].value === "" ? null : ctl[key].value;
}

function collectSettings(ctl, v, pathMapText) {
  const changes = {};
  const put = (table, key, value, old) => {
    if (JSON.stringify(value) === JSON.stringify(old)) return;
    if (table) (changes[table] = changes[table] || {})[key] = value;
    else changes[key] = value;
  };
  let invalid = null;
  const int = (key) => {
    const value = Number(ctl[key].value);
    if (ctl[key].value.trim() === "" || !Number.isInteger(value)) invalid = invalid || key;
    return value;
  };
  const lines = (key) => ctl[key].value.split("\n").map((x) => x.trim()).filter(Boolean);

  put("screenshots", "count", int("screenshots.count"), v.screenshots.count);
  put("screenshots", "aspect", ctl["screenshots.aspect"].value, v.screenshots.aspect);
  put("screenshots", "dark_filter", ctl["screenshots.dark_filter"].checked, v.screenshots.dark_filter);
  put("pixhost", "domain", ctl["pixhost.domain"].value, v.pixhost.domain);
  put("pixhost", "proxy", ctl["pixhost.proxy"].value.trim(), v.pixhost.proxy);
  put("torrent", "announces", lines("torrent.announces"), v.torrent.announces);
  put("torrent", "piece_length", int("torrent.piece_length"), v.torrent.piece_length);
  put("post", "template_text", ctl["post.template_text"].value.trim() ? ctl["post.template_text"].value : "", v.post.template_text);
  put("", "max_jobs", int("max_jobs"), v.max_jobs);
  put("", "temp_dir", ctl.temp_dir.value.trim(), v.temp_dir);
  put("", "seed_dir", ctl.seed_dir.value.trim(), v.seed_dir);

  const q = v.qbittorrent;
  put("qbittorrent", "url", ctl["qbittorrent.url"].value.trim(), q.url);
  put("qbittorrent", "username", ctl["qbittorrent.username"].value, q.username);
  const password = secretValue(ctl, "qbittorrent.password");
  if (password !== null) put("qbittorrent", "password", password, undefined);
  put("qbittorrent", "category", ctl["qbittorrent.category"].value.trim(), q.category);
  put("qbittorrent", "seed_category", ctl["qbittorrent.seed_category"].value.trim(), q.seed_category);
  put("qbittorrent", "save_path", ctl["qbittorrent.save_path"].value.trim(), q.save_path);
  if (ctl["qbittorrent.path_map"].value.trim() !== pathMapText.trim()) {
    const map = {};
    for (const line of lines("qbittorrent.path_map")) {
      const [remote, local] = line.split("=").map((x) => (x || "").trim());
      if (!remote || !local) { toast(`路径映射格式不对：${line}。${PATH_MAP_HINT}`); return null; }
      map[remote] = local;
    }
    put("qbittorrent", "path_map", map, undefined);
  }
  put("qbittorrent", "interval", int("qbittorrent.interval"), q.interval);

  put("rutor", "url", ctl["rutor.url"].value.trim(), v.rutor.url);
  put("rutor", "queries", ctl["rutor.queries"].value.split(/[\s,，]+/).filter(Boolean), v.rutor.queries);
  put("rutor", "interval", int("rutor.interval"), v.rutor.interval);
  put("rutor", "films_only", ctl["rutor.films_only"].checked, v.rutor.films_only);

  const j = v.jackett;
  put("jackett", "url", ctl["jackett.url"].value.trim(), j.url);
  const key = secretValue(ctl, "jackett.api_key");
  if (key !== null) put("jackett", "api_key", key, undefined);
  const tmKey = secretValue(ctl, "tmdb.api_key");
  if (tmKey !== null) put("tmdb", "api_key", tmKey, undefined);
  put("jackett", "indexer", ctl["jackett.indexer"].value.trim() || "all", j.indexer);
  put("jackett", "queries", ctl["jackett.queries"].value.split(/[\s,，]+/).filter(Boolean), j.queries);
  put("jackett", "interval", int("jackett.interval"), j.interval);
  put("jackett", "films_only", ctl["jackett.films_only"].checked, j.films_only);
  if (invalid) {
    toast("请填写整数");
    ctl[invalid].focus();
    return null;
  }
  return changes;
}

async function saveSettings(button, errorBox, request, message) {
  button.disabled = true;
  errorBox.replaceChildren();
  try {
    const data = await request();
    state.config = await api("/api/config");
    $("nav-releases").hidden = !releasesEnabled();
    refreshReleaseCount();
    toast(message);
    renderSettings(data);
  } catch (error) {
    if (error instanceof AuthError) return;
    errorBox.replaceChildren(notice("bad", error.message));
    window.scrollTo({ top: 0, behavior: "smooth" });
  } finally {
    button.disabled = false;
  }
}

async function testConnection(button, output, url, body, describe) {
  button.disabled = true;
  output.className = "test-result";
  output.textContent = "连接中…";
  try {
    const result = await api(url, { json: body });
    output.classList.add("ok");
    output.textContent = describe(result);
  } catch (error) {
    if (error instanceof AuthError) return;
    output.classList.add("bad");
    output.textContent = error.message;
  } finally {
    button.disabled = false;
  }
}

// ---------- 资源（Jackett 候选 + qBittorrent 下载） ----------

const RELEASE_TABS = [["new", "候选"], ["active", "进行中"], ["finished", "已完成"], ["ignored", "已忽略"]];
const RELEASE_STATUS = {
  new: ["候选", ""], ignored: ["已忽略", ""], sent: ["已推送", "queued"], downloading: ["下载中", "running"],
  processing: ["处理中", "running"], done: ["完成", "done"], failed: ["失败", "failed"],
};
const RELEASE_EMPTY = {
  new: "还没有候选。点“立即搜索”从 Jackett 搜索 DVD 原盘。",
  active: "没有正在下载或处理的资源。",
  finished: "还没有处理完的资源。",
  ignored: "没有忽略的资源。",
};

function releasesEnabled() {
  return Boolean(state.config && (state.config.qbit || state.config.jackett || state.config.rutor));
}

async function refreshReleaseCount(counts) {
  if (!releasesEnabled()) return;
  if (!counts) {
    try { counts = (await api("/api/releases?group=ignored")).counts; } catch { return; }
  }
  $("release-count").textContent = counts.new ? String(counts.new) : "";
}

function scheduleReleasePoll(group, busy) {
  clearTimeout(state.releaseTimer);
  state.releaseTimer = setTimeout(() => {
    if (state.route && state.route.name === "releases" && state.route.group === group) showReleases(group, true);
  }, busy ? 4000 : 30000);
}

const RELEASE_PAGE = 50;

function releaseFilter() {
  state.releaseFilter = state.releaseFilter || { q: "", kind: "", seeded: false, clean: false, page: 0 };
  return state.releaseFilter;
}

async function showReleases(group, quiet = false, force = false) {
  state.selected = null;
  if (!quiet && state.listing) renderListing();
  const f = releaseFilter();
  if (!quiet) f.page = 0;  // 从别的页面或标签进入时回到第一页
  const params = new URLSearchParams({ group, offset: String(f.page * RELEASE_PAGE), limit: String(RELEASE_PAGE) });
  if (f.q.trim()) params.set("q", f.q.trim());
  if (f.kind) params.set("kind", f.kind);
  if (f.seeded) params.set("seeded", "true");
  if (f.clean) params.set("clean", "true");
  const data = await api(`/api/releases?${params}`);
  if (!state.route || state.route.name !== "releases" || state.route.group !== group) return;
  // 轮询刷新时，不打断正在操作的按钮和正在输入的筛选框
  const active = document.activeElement;
  if (quiet && !force && (document.querySelector("#main [data-busy]") || (active && active.closest("#main .release-filter")))) {
    scheduleReleasePoll(group, true);
    return;
  }
  if (data.total && f.page * RELEASE_PAGE >= data.total) {  // 筛选后页数变少
    f.page = Math.floor((data.total - 1) / RELEASE_PAGE);
    await showReleases(group, quiet, force);
    return;
  }
  // 重绘后把光标放回筛选框原来的位置，输入不中断
  const typing = active && active.matches("#main .release-filter input[type=search]")
    ? [active.selectionStart, active.selectionEnd] : null;
  renderReleases(group, data);
  if (typing) {
    const box = document.querySelector("#main .release-filter input[type=search]");
    box.focus();
    box.setSelectionRange(...typing);
  }
  refreshReleaseCount(data.counts);
  const busy = data.status.searching || data.status.backfill.running || data.counts.active > 0;
  scheduleReleasePoll(group, busy);
}

function renderReleases(group, data) {
  const s = data.status;
  const c = state.config;
  const bf = s.backfill;
  const searchable = s.jackett || s.rutor;
  const sources = [s.jackett ? `Jackett（${c.jackett.indexer === "all" ? "全部站点" : c.jackett.indexer}）` : null,
    s.rutor ? "rutor 直连" : null].filter(Boolean);
  const meta = [
    h("span", {}, sources.length ? `来源：${sources.join("、")}` : "未配置搜索来源"),
    searchable && bf.running ? h("span", {}, `全面搜索中 ${bf.done}/${bf.total}，新增 ${bf.added} 个`) : null,
    searchable && !bf.running && s.last_search ? h("span", {}, `上次搜索 ${clock(s.last_search)}，新增 ${s.last_added} 个`) : null,
    searchable && !bf.running && bf.last ? h("span", {}, `上次全面搜索 ${clock(bf.last)}，新增 ${bf.added} 个`) : null,
    h("span", {}, s.qbit ? `qBittorrent 分类 ${c.qbit.category}` : "未配置 qBittorrent"),
  ].filter(Boolean);
  const search = h("button", { type: "button", class: "btn amber", disabled: !searchable || s.searching },
    icon("search"), s.searching && !bf.running ? "搜索中…" : "立即搜索");
  search.addEventListener("click", () => releaseRequest(search, "/api/releases/refresh", (r) => `新增 ${r.added} 个候选`));
  const backfill = h("button", { type: "button", class: "btn glass", disabled: !searchable || s.searching,
    title: "日常搜索只读最新的一页。全面搜索按“关键词 年份”逐年搜索（rutor 直连还会翻完每一页），可以找到更早发布的资源，需要几分钟。" },
  bf.running ? `全面搜索中 ${Math.round((bf.done / Math.max(bf.total, 1)) * 100)}%` : "按年份全面搜索");
  backfill.addEventListener("click", () => releaseRequest(backfill, "/api/releases/backfill", (r) => `开始全面搜索，共 ${r.total} 次查询`));
  const sync = h("button", { type: "button", class: "btn glass", disabled: !s.qbit }, icon("refresh"), "检查下载");
  sync.addEventListener("click", () => releaseRequest(sync, "/api/releases/sync", () => "已检查 qBittorrent"));

  const tabs = h("div", { class: "tabs", role: "tablist" }, RELEASE_TABS.map(([name, label]) =>
    h("a", { role: "tab", href: `#/releases/${name}`, "aria-selected": String(name === group) },
      label, h("span", { class: "count" }, data.counts[name] || ""))));

  const problems = [];
  if (s.search_error) problems.push(notice("bad", `搜索：${s.search_error}`));
  if (s.sync_error) problems.push(notice("bad", `qBittorrent：${s.sync_error}`));

  const sourceInfo = sourceStatus(s);

  const list = data.releases.length
    ? h("ul", { class: "releases" }, data.releases.map((r) => releaseRow(r, s)))
    : notice("", data.counts[group] ? "没有符合筛选条件的资源。" : RELEASE_EMPTY[group]);

  setMain(
    hero({ eyebrow: "资源", title: "DVD 原盘", compact: true, meta, children: [h("div", { class: "actions" }, search, backfill, sync)] }),
    h("div", { class: "content" }, h("div", {}, tabs), ...problems, sourceInfo, releaseFilterBar(group, data), list, releasePager(group, data)));
}

// 每个来源的自动搜索设置和上次搜索的结果，用来判断新资源为什么没出现
function sourceStatus(s) {
  const names = Object.keys({ ...s.schedule, ...s.sources });
  if (!names.length) return null;
  return h("ul", { class: "source-status" }, names.map((name) => {
    const plan = s.schedule[name];
    const last = s.sources[name];
    const parts = [];
    if (plan) parts.push(plan.every ? `每 ${plan.every} 分钟自动搜索${plan.next ? `，下次 ${stamp(plan.next)}` : ""}` : "不自动搜索（间隔为 0），只在点“立即搜索”时搜索");
    if (last) {
      parts.push(`上次${last.kind} ${stamp(last.last)}：${last.results} 条结果，新增 ${last.added} 个`);
      parts.push(last.newest ? `最新一条发布于 ${stamp(last.newest)}` : "结果中没有发布时间");
    } else {
      parts.push("启动后还没搜索过");
    }
    return h("li", { class: last && last.error ? "bad" : "" }, h("b", {}, name === "rutor" ? "rutor 直连" : name),
      h("span", {}, parts.join(" · ")), last && last.error ? h("span", { class: "error" }, last.error) : null);
  }));
}

function releaseFilterBar(group, data) {
  const f = releaseFilter();
  const reload = () => { f.page = 0; showReleases(group, true, true); };
  const q = h("input", { type: "search", value: f.q, placeholder: "按标题筛选，例如片名或年份", spellcheck: "false" });
  let timer = null;
  q.addEventListener("input", () => {
    f.q = q.value;
    clearTimeout(timer);
    timer = setTimeout(reload, 350);
  });
  q.addEventListener("keydown", (event) => { if (event.key === "Enter") { clearTimeout(timer); reload(); } });
  const kind = h("select", {}, [["", "全部"], ["DVD9", "DVD9"], ["DVD5", "DVD5"], ["multi", "多张盘"]].map(([value, label]) =>
    h("option", { value, selected: f.kind === value }, label)));
  kind.addEventListener("change", () => { f.kind = kind.value; reload(); });
  const check = (key, label) => {
    const box = h("input", { type: "checkbox", checked: f[key] });
    box.addEventListener("change", () => { f[key] = box.checked; reload(); });
    return h("label", { class: "check" }, box, label);
  };
  const shown = data.total === data.counts[group] ? `共 ${data.total} 个` : `筛选出 ${data.total} 个，共 ${data.counts[group]} 个`;
  return h("div", { class: "release-filter" }, q, kind, check("seeded", "有做种者"), check("clean", "没有提示"),
    h("span", { class: "filter-count" }, shown));
}

function releasePager(group, data) {
  const f = releaseFilter();
  const pages = Math.ceil(data.total / RELEASE_PAGE);
  if (pages <= 1) return null;
  const go = (page) => { f.page = page; showReleases(group, true, true).then(() => window.scrollTo(0, 0)); };
  const prev = h("button", { type: "button", class: "btn small glass", disabled: f.page === 0 }, "上一页");
  const next = h("button", { type: "button", class: "btn small glass", disabled: f.page >= pages - 1 }, "下一页");
  prev.addEventListener("click", () => go(f.page - 1));
  next.addEventListener("click", () => go(f.page + 1));
  return h("div", { class: "pager" }, prev, h("span", {}, `第 ${f.page + 1} / ${pages} 页`), next);
}

function releaseRow(r, s) {
  const [statusText, statusCls] = RELEASE_STATUS[r.status] || [r.status, ""];
  // 详情页地址来自站点，只接受 http(s)
  const title = r.details_url && /^https?:\/\//i.test(r.details_url)
    ? h("a", { class: "title", href: r.details_url, target: "_blank", rel: "noopener noreferrer", title: "打开发布页" }, r.title)
    : h("span", { class: "title" }, r.title);
  const facts = [
    r.source,
    r.kind,
    r.size ? formatBytes(r.size) : null,
    r.published ? new Date(r.published * 1000).toLocaleDateString("zh-CN") : null,
    r.seeders !== null && r.seeders !== undefined ? `${r.seeders} 做种` : null,
  ].filter(Boolean);

  const side = [];
  const button = (label, cls, url, done) => {
    const b = h("button", { type: "button", class: `btn small ${cls}` }, label);
    b.addEventListener("click", () => releaseRequest(b, url, done));
    return b;
  };
  const base = `/api/releases/${encodeURIComponent(r.id)}`;
  if (r.status === "new") {
    const download = button("下载", "amber", `${base}/download`, () => "已推送到 qBittorrent");
    if (!s.qbit) { download.disabled = true; download.title = "没有配置 qBittorrent"; }
    side.push(download, button("忽略", "glass", `${base}/ignore`, () => "已忽略"));
  } else if (r.status === "ignored") {
    side.push(button("恢复", "glass", `${base}/ignore?undo=true`, () => "已恢复到候选"));
  } else if (r.status === "sent" || r.status === "downloading") {
    const pct = Math.round((r.progress || 0) * 100);
    side.push(h("span", { class: `pill ${statusCls}` }, r.status === "sent" ? statusText : `${statusText} ${pct}%`));
  } else {
    side.push(h("span", { class: `pill ${statusCls}` }, statusText));
    if (r.job_id && state.jobs.some((j) => j.id === r.job_id)) {
      side.push(h("a", { class: "btn small glass", href: `#/job/${r.job_id}` }, r.status === "processing" ? "查看进度" : "查看结果"));
    }
    if (r.status === "failed") {
      if (r.local_path) side.push(button("重新处理", "glass", `${base}/reprocess`, () => "已重新开始处理"));
      else if (s.qbit) side.push(button("重新下载", "glass", `${base}/download`, () => "已推送到 qBittorrent"));
    }
  }

  const notes = [
    ...(r.labels || []).map((label) => h("span", { class: "chip good" }, label)),
    ...(r.warnings || []).map((w) => h("span", { class: "chip warn" }, w)),
  ];
  const extra = [];
  if (r.status === "sent" || r.status === "downloading") {
    const bar = h("div", { class: "bar" }, h("i", {}));
    bar.firstChild.style.width = `${Math.round((r.progress || 0) * 100)}%`;
    extra.push(bar);
  }
  if (r.error) extra.push(h("p", { class: "release-error" }, r.error));
  if (r.status === "done" && r.output_dir) {
    extra.push(h("p", { class: "release-path" }, `输出：${r.output_dir}${r.post_file ? `/${r.post_file}` : ""}`));
  }
  return h("li", { class: "release" },
    h("div", { class: "info" }, title, h("div", { class: "release-facts" }, facts.map((f) => h("span", {}, f))),
      notes.length ? h("div", { class: "chips" }, notes) : null, ...extra),
    h("div", { class: "side" }, side));
}

async function releaseRequest(button, url, done) {
  button.disabled = true;
  button.dataset.busy = "1";
  try {
    const result = await api(url, { method: "POST" });
    toast(done(result || {}));
  } catch (error) {
    if (error instanceof AuthError) return;
    toast(error.message);
  } finally {
    delete button.dataset.busy;
  }
  await refreshJobs();
  if (state.route && state.route.name === "releases") await showReleases(state.route.group, true);
}

const ASPECT_LABELS = {
  ua: "ua：同 Upload-Assistant，只放大不缩小",
  minfo: "minfo：按 DAR，高度不变",
  jietu: "jietu：按 PAR，只放大不缩小",
};

function showEmpty() {
  state.selected = null;
  setMain(h("div", { class: "empty" }, h("span", { class: "disc-mark huge", "aria-hidden": "true" }),
    h("h2", {}, "选择要处理的 DVD"), h("p", {}, "在左侧打开 DVD 文件夹、ISO，或包含多张盘的目录。勾选多项可以批量处理。")));
}

// ---------- 路由 ----------

function parseRoute() {
  const hash = location.hash.replace(/^#\/?/, "");
  if (hash.startsWith("browse/")) return { name: "browse", path: decodeURIComponent(hash.slice(7)) };
  const job = hash.match(/^job\/([0-9a-f]+)(\/log)?$/);
  if (job) return { name: "job", id: job[1], tab: job[2] ? "log" : "result" };
  if (hash === "settings") return { name: "settings" };
  const releases = hash.match(/^releases(?:\/(new|active|finished|ignored))?$/);
  if (releases) return { name: "releases", group: releases[1] || "new" };
  return { name: "home" };
}

async function route() {
  closeStream();
  clearTimeout(state.releaseTimer);
  state.route = parseRoute();
  $("nav-settings").setAttribute("aria-current", state.route.name === "settings" ? "page" : "false");
  $("nav-releases").setAttribute("aria-current", state.route.name === "releases" ? "page" : "false");
  window.scrollTo(0, 0);
  try {
    if (state.route.name === "browse") await showSource(state.route.path);
    else if (state.route.name === "job") await showJob(state.route.id, state.route.tab);
    else if (state.route.name === "settings") await showSettings();
    else if (state.route.name === "releases") {
      if (!releasesEnabled()) throw new Error("没有配置 Jackett 或 qBittorrent，资源页不可用。请在配置文件中设置 [jackett] 和 [qbittorrent]。");
      await showReleases(state.route.group);
    }
    else {
      if (state.config.roots.length === 1) {
        location.replace(`#/browse/${encodeURIComponent(state.config.roots[0])}`);
        return;
      }
      await loadListing(null);
      showEmpty();
    }
  } catch (error) {
    if (error instanceof AuthError) return;
    setMain(h("div", { class: "content", style: "padding-top:48px" }, notice("bad", error.message), h("p", {}, h("a", { class: "btn glass small", href: "#/", style: "margin-top:12px" }, "回到首页"))));
  }
  renderTasks();
}

// ---------- 启动 ----------

async function start() {
  try {
    state.config = await api("/api/config");
  } catch (error) {
    if (!(error instanceof AuthError)) {
      $("login-error").textContent = error.message;
      $("login-error").hidden = false;
    }
    return;
  }
  const c = state.config;
  state.opts = state.opts || { count: c.screenshot_count, upload: true, tracker: c.announces.join(" "), piece: c.piece_length };
  $("login").hidden = true;
  $("app").hidden = false;
  $("nav-releases").hidden = !releasesEnabled();
  refreshReleaseCount();
  await refreshJobs();
  if (!state.listing) {
    try { await loadListing(c.roots.length === 1 ? c.roots[0] : null); } catch { /* route() 会显示错误 */ }
  }
  await route();
}

document.addEventListener("DOMContentLoaded", () => {
  $("nav-settings").append(icon("gear"));
  $("logout").append(icon("logout"));
  $("login-form").addEventListener("submit", login);
  $("logout").addEventListener("click", logout);
  $("check-all").addEventListener("click", checkAll);
  $("batch").addEventListener("click", onBatch);
  document.addEventListener("change", (event) => {
    if (event.target.closest(".options")) readOptions();
  });
  window.addEventListener("hashchange", () => { if (state.config) route(); });
  start();
});
