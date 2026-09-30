"use strict";

const $ = (id) => document.getElementById(id);
const STORAGE_KEY = "whatdvd.browsePath";
const KIND_LABEL = { dir: "目录", dvd: "DVD", iso: "ISO" };
const JOB_KIND = { run: "截图与 MediaInfo", torrent: "做种" };
const JOB_ICON = { run: "image", torrent: "peers" };

// 图标：固定的 SVG 片段（24×24，描边），只来自这里，不含任何外部输入
const ICONS = {
  folder: '<path d="M3 7.5A2.5 2.5 0 0 1 5.5 5H9l2 2h7.5A2.5 2.5 0 0 1 21 9.5v7a2.5 2.5 0 0 1-2.5 2.5h-13A2.5 2.5 0 0 1 3 16.5z"/>',
  disc: '<circle cx="12" cy="12" r="9"/><circle cx="12" cy="12" r="2.5"/><path d="M12 5.5a6.5 6.5 0 0 1 6.5 6.5"/>',
  iso: '<path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z"/><path d="M14 3v5h5"/><circle cx="12" cy="14.5" r="3"/>',
  back: '<path d="M15 18l-6-6 6-6"/>',
  chev: '<path d="M9 6l6 6-6 6"/>',
  logout: '<path d="M14 4h4a2 2 0 0 1 2 2v12a2 2 0 0 1-2 2h-4"/><path d="M10 16l-4-4 4-4"/><path d="M6 12h10"/>',
  copy: '<rect x="9" y="9" width="11" height="11" rx="2"/><path d="M5 15V6a2 2 0 0 1 2-2h8"/>',
  check: '<path d="M5 12.5l4.5 4.5L19 7"/>',
  download: '<path d="M12 4v11"/><path d="M7 10l5 5 5-5"/><path d="M5 20h14"/>',
  link: '<path d="M14 4h6v6"/><path d="M20 4l-9 9"/><path d="M18 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1h5"/>',
  image: '<rect x="3" y="5" width="18" height="14" rx="2"/><circle cx="9" cy="10" r="1.6"/><path d="M21 16l-5-5-8 8"/>',
  peers: '<circle cx="6" cy="12" r="2.5"/><circle cx="18" cy="6" r="2.5"/><circle cx="18" cy="18" r="2.5"/><path d="M8.3 10.9l7.4-3.7M8.3 13.1l7.4 3.7"/>',
  alert: '<circle cx="12" cy="12" r="9"/><path d="M12 7.5v5.5"/><path d="M12 16.5h.01"/>',
  info: '<circle cx="12" cy="12" r="9"/><path d="M12 11v5.5"/><path d="M12 7.5h.01"/>',
};

const state = {
  config: null, path: null, parent: null, selected: null,
  jobId: null, source: null, timer: null, actionTab: "run",
};

class AuthError extends Error {}

// ---------- 工具函数 ----------

function setIcon(span, name) {
  span.innerHTML = `<svg viewBox="0 0 24 24" aria-hidden="true">${ICONS[name]}</svg>`;
}

function icon(name) {
  const span = document.createElement("span");
  span.className = "icon";
  setIcon(span, name);
  return span;
}

function h(tag, attrs = {}, ...children) {
  const el = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (value === null || value === undefined || value === false) continue;
    if (key === "class") el.className = value;
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
    if (value < 1024) return unit === "B" ? `${size} B` : `${value.toFixed(2)} ${unit}`;
    value /= 1024;
  }
  return `${value.toFixed(2)} GiB`;
}

function formatSeconds(seconds) {
  const s = seconds % 86400;
  const pad = (n) => String(n).padStart(2, "0");
  return `${pad(Math.floor(s / 3600))}:${pad(Math.floor((s % 3600) / 60))}:${pad(s % 60)}`;
}

function formatDuration(seconds) {
  if (seconds < 60) return `${seconds} 秒`;
  const m = Math.floor(seconds / 60);
  return seconds % 60 ? `${m} 分 ${seconds % 60} 秒` : `${m} 分钟`;
}

function formatClock(timestamp) {
  return new Date(timestamp * 1000).toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" });
}

function basename(path) {
  const parts = path.split("/").filter(Boolean);
  return parts.length ? parts[parts.length - 1] : path;
}

function findRoot(path) {
  return (state.config?.roots || []).find((root) => path === root || path.startsWith(`${root}/`)) || null;
}

/** 以根目录名开头的相对路径，例如 downloads / Movie / Disc 1。 */
function shortPath(path) {
  const root = findRoot(path);
  if (!root) return path;
  const rest = path.slice(root.length).split("/").filter(Boolean);
  return [basename(root), ...rest].join(" / ");
}

function formatDetail(detail) {
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) return detail.map((d) => String(d.msg || "").replace(/^Value error, /, "")).join("；");
  return "";
}

function storageGet() {
  try { return localStorage.getItem(STORAGE_KEY); } catch { return null; }
}

function storageSet(value) {
  try {
    if (value) localStorage.setItem(STORAGE_KEY, value);
    else localStorage.removeItem(STORAGE_KEY);
  } catch { /* 浏览器禁用了存储时忽略 */ }
}

function showError(id, message) {
  const el = $(id);
  el.textContent = message;
  el.hidden = !message;
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
    try { message = formatDetail((await response.json()).detail) || message; } catch { /* 非 JSON 响应 */ }
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

/** 复制按钮：点击后短暂显示“已复制”。getText 可以是异步函数。 */
function copyButton(label, getText, primary = false) {
  const text = h("span", {}, label);
  const glyph = icon("copy");
  const button = h("button", { type: "button", class: `btn small${primary ? " primary" : ""}` }, glyph, text);
  let timer = null;
  button.addEventListener("click", async () => {
    const ok = await writeClipboard(await getText());
    clearTimeout(timer);
    setIcon(glyph, ok ? "check" : "alert");
    text.textContent = ok ? "已复制" : "复制失败，请手动选择";
    button.classList.toggle("done", ok && !primary);
    timer = setTimeout(() => {
      setIcon(glyph, "copy");
      text.textContent = label;
      button.classList.remove("done");
    }, 1800);
  });
  return button;
}

function notice(kind, message) {
  return h("div", { class: `notice ${kind}`, role: kind === "bad" ? "alert" : null }, icon(kind === "info" ? "info" : "alert"), h("div", {}, message));
}

// ---------- 登录 ----------

function showLogin() {
  closeStream();
  clearInterval(state.timer);
  state.timer = null;
  $("app").hidden = true;
  $("login").hidden = false;
  $("login-token").focus();
}

async function login(event) {
  event.preventDefault();
  showError("login-error", "");
  const response = await fetch("/api/login", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ token: $("login-token").value }),
  });
  if (!response.ok) {
    showError("login-error", response.status === 401 ? "token 不正确。" : `登录失败（HTTP ${response.status}）`);
    return;
  }
  $("login-token").value = "";
  await start();
}

async function logout() {
  try { await api("/api/logout", { method: "POST" }); } catch { /* 忽略 */ }
  showLogin();
}

// ---------- 浏览 ----------

function renderCrumbs(path) {
  const crumbs = [];
  const multiRoot = state.config.roots.length > 1;
  if (multiRoot || path === null) crumbs.push({ label: "全部目录", path: null });
  if (path !== null) {
    const root = findRoot(path);
    crumbs.push({ label: basename(root), path: root });
    let current = root;
    for (const part of path.slice(root.length).split("/").filter(Boolean)) {
      current = `${current}/${part}`;
      crumbs.push({ label: part, path: current });
    }
  }
  const nodes = [];
  crumbs.forEach((crumb, index) => {
    if (index > 0) nodes.push(h("span", { class: "sep", "aria-hidden": "true" }, "/"));
    const last = index === crumbs.length - 1;
    nodes.push(h("button", {
      type: "button", title: crumb.path || "全部目录", "aria-current": last ? "page" : null,
      onclick: () => browse(crumb.path),
    }, crumb.label));
  });
  $("crumbs").replaceChildren(...nodes);
}

async function browse(path) {
  showError("browse-error", "");
  let data;
  try {
    data = await api(path ? `/api/browse?path=${encodeURIComponent(path)}` : "/api/browse");
  } catch (error) {
    if (error instanceof AuthError) return;
    if (path) {
      storageSet(null);
      await browse(state.config.roots.length === 1 ? state.config.roots[0] : null);
      showError("browse-error", error.message);
      return;
    }
    showError("browse-error", error.message);
    return;
  }
  state.path = data.path;
  state.parent = data.parent;
  storageSet(data.path);
  renderCrumbs(data.path);
  if (data.path) select({ path: data.path, kind: data.kind || "dir" });

  const items = [];
  const atTop = data.path === null || (data.parent === null && state.config.roots.length === 1);
  if (!atTop) {
    items.push(h("li", {}, h("button", { type: "button", class: "entry back", onclick: () => browse(data.parent) },
      icon("back"), h("span", { class: "name" }, "上一级"))));
  }
  for (const entry of data.entries) {
    const isIso = entry.kind === "iso";
    items.push(h("li", {},
      h("button", {
        type: "button",
        class: `entry ${entry.kind}`,
        title: entry.path,
        "data-path": entry.path,
        "aria-current": state.selected?.path === entry.path ? "true" : "false",
        onclick: () => (isIso ? select(entry) : browse(entry.path)),
      },
      icon(entry.kind === "dir" ? "folder" : entry.kind === "iso" ? "iso" : "disc"),
      h("span", { class: "name" }, data.path === null ? shortPath(entry.path) : entry.name),
      entry.kind !== "dir" ? h("span", { class: "tag" }, KIND_LABEL[entry.kind]) : null,
      isIso ? null : h("span", { class: "chev" }, icon("chev")))));
  }
  if (!data.entries.length) items.push(h("li", { class: "side-empty" }, "这里没有子目录或 ISO 文件"));
  $("browse-list").replaceChildren(...items);
}

function select(entry) {
  state.selected = entry;
  $("empty").hidden = true;
  $("source").hidden = false;
  const kind = $("source-kind");
  kind.textContent = KIND_LABEL[entry.kind] || entry.kind;
  kind.className = `kind-tag ${entry.kind}`;
  $("source-name").textContent = basename(entry.path);
  const path = $("source-path");
  path.textContent = shortPath(entry.path);
  path.title = entry.path;
  for (const button of document.querySelectorAll("#browse-list .entry[data-path]")) {
    button.setAttribute("aria-current", button.dataset.path === entry.path ? "true" : "false");
  }
}

// ---------- 操作 ----------

function setActionTab(name) {
  state.actionTab = name;
  $("tab-run").setAttribute("aria-selected", String(name === "run"));
  $("tab-torrent").setAttribute("aria-selected", String(name === "torrent"));
  $("run-form").hidden = name !== "run";
  $("torrent-form").hidden = name !== "torrent";
  showError("submit-error", "");
}

function setupForms() {
  const config = state.config;
  $("run-count").value = config.screenshot_count;
  $("torrent-announces").value = config.announces.join("\n");
  const [low, high] = config.piece_length_range;
  const options = [];
  for (let n = low; n <= high; n += 1) {
    const size = 2 ** n >= 1024 ** 2 ? `${2 ** n / 1024 ** 2} MiB` : `${2 ** n / 1024} KiB`;
    options.push(h("option", { value: n, selected: n === config.piece_length }, n === 24 ? `${size}（默认）` : size));
  }
  $("torrent-piece").replaceChildren(...options);
}

async function submitJob(body) {
  showError("submit-error", "");
  try {
    const job = await api("/api/jobs", { json: body });
    await refreshJobs();
    await openJob(job.id);
  } catch (error) {
    if (!(error instanceof AuthError)) showError("submit-error", error.message);
  }
}

function submitRun(event) {
  event.preventDefault();
  if (!state.selected) return;
  submitJob({ kind: "run", path: state.selected.path, count: Number($("run-count").value), upload: $("run-upload").checked });
}

function submitTorrent(event) {
  event.preventDefault();
  if (!state.selected) return;
  const announces = $("torrent-announces").value.split("\n").map((line) => line.trim()).filter(Boolean);
  submitJob({ kind: "torrent", path: state.selected.path, announces, piece_length: Number($("torrent-piece").value) });
}

// ---------- 任务列表 ----------

function statusOf(job) {
  if (job.status === "done" && job.ok === false) return { cls: "warn", text: "完成，有问题" };
  const text = { queued: "排队中", running: "运行中", done: "完成", failed: "失败" }[job.status] || job.status;
  return { cls: job.status, text };
}

async function refreshJobs() {
  let jobs;
  try { jobs = await api("/api/jobs"); } catch { return; }
  $("jobs-empty").hidden = jobs.length > 0;
  $("jobs-count").textContent = jobs.length ? String(jobs.length) : "";
  $("jobs").replaceChildren(...jobs.map((job) => {
    const status = statusOf(job);
    return h("li", {},
      h("button", {
        type: "button", class: "job-item", title: job.path,
        "aria-current": job.id === state.jobId ? "true" : "false", onclick: () => openJob(job.id),
      },
      icon(JOB_ICON[job.kind] || "info"),
      h("span", { class: "text" },
        h("span", { class: "name" }, basename(job.path)),
        h("span", { class: "sub" }, `${job.kind === "run" ? "截图" : "做种"} · ${formatClock(job.created_at)} · ${status.text}`)),
      h("span", { class: `dot ${status.cls}`, "aria-hidden": "true" })));
  }));
}

// ---------- 任务详情 ----------

function closeStream() {
  if (state.source) state.source.close();
  state.source = null;
}

function setJobTab(name) {
  $("jtab-result").setAttribute("aria-selected", String(name === "result"));
  $("jtab-log").setAttribute("aria-selected", String(name === "log"));
  $("job-result").hidden = name !== "result";
  $("job-log").hidden = name !== "log";
}

function appendLog(event) {
  const log = $("job-log");
  const nearBottom = log.scrollHeight - log.scrollTop - log.clientHeight < 40;
  log.append(h("span", { class: event.level === "error" ? "error" : null }, `${event.message}\n`));
  $("log-count").textContent = String(log.childElementCount);
  if (nearBottom) log.scrollTop = log.scrollHeight;
}

function renderHeader(job) {
  $("job").hidden = false;
  $("job-kind").textContent = JOB_KIND[job.kind] || job.kind;
  $("job-title").textContent = basename(job.path);
  $("job-title").title = job.path;
  const status = statusOf(job);
  const badge = $("job-status");
  badge.className = `status ${status.cls}`;
  badge.replaceChildren(h("span", { class: `dot ${status.cls}`, "aria-hidden": "true" }), status.text);
  $("job-progress").hidden = job.status === "done" || job.status === "failed";
}

async function openJob(id) {
  closeStream();
  state.jobId = id;
  for (const button of document.querySelectorAll(".job-item")) button.setAttribute("aria-current", "false");
  let job;
  try { job = await api(`/api/jobs/${id}`); } catch { return; }
  if (state.jobId !== id) return;
  renderHeader(job);
  $("job-log").replaceChildren();
  $("log-count").textContent = "";
  job.events.forEach(appendLog);
  await refreshJobs();

  if (job.status === "done" || job.status === "failed") {
    renderResult(job);
    setJobTab("result");
    return;
  }
  $("job-result").replaceChildren(notice("info", "任务进行中，完成后在这里显示结果。可以在“日志”中查看进度。"));
  setJobTab("log");
  const source = new EventSource(`/api/jobs/${id}/events?after=${job.events.length}`);
  state.source = source;
  source.addEventListener("log", (message) => appendLog(JSON.parse(message.data)));
  source.addEventListener("end", async () => {
    closeStream();
    const detail = await api(`/api/jobs/${id}`);
    if (state.jobId !== id) return;
    renderHeader(detail);
    renderResult(detail);
    setJobTab("result");
    await refreshJobs();
  });
  source.addEventListener("error", () => {
    // 浏览器会自动重连；连接被关闭时检查是否需要重新登录
    if (source.readyState === EventSource.CLOSED) api("/api/config").catch(() => {});
  });
  $("job").scrollIntoView({ behavior: "smooth", block: "nearest" });
}

function fileUrl(job, name) {
  return `/api/jobs/${job.id}/files/${encodeURIComponent(name)}`;
}

function codePanel({ title, format, text, loader, collapsed }) {
  const pre = h("pre", {}, text ?? "");
  let content = text ?? null;
  const load = async () => {
    if (content === null) {
      pre.textContent = "加载中…";
      content = await loader();
      pre.textContent = content;
    }
    return content;
  };
  const panel = h("section", { class: `panel${collapsed ? " collapsed" : ""}` });
  let heading;
  if (collapsed) {
    pre.hidden = true;
    heading = h("button", { type: "button", class: "toggle", "aria-expanded": "false" }, icon("chev"), title);
    heading.addEventListener("click", async () => {
      const open = heading.getAttribute("aria-expanded") !== "true";
      heading.setAttribute("aria-expanded", String(open));
      panel.classList.toggle("collapsed", !open);
      pre.hidden = !open;
      if (open) await load();
    });
  } else {
    heading = h("h3", {}, title);
  }
  panel.append(
    h("div", { class: "panel-head" }, heading, format ? h("span", { class: "format" }, format) : null, copyButton("复制", load, !collapsed)),
    pre,
  );
  return panel;
}

function renderDisc(job, disc) {
  const chips = [];
  if (disc.vob) {
    chips.push(h("span", { class: "chip strong" }, disc.media_type));
    chips.push(h("span", { class: "chip" }, disc.standard || `高度 ${disc.height}`));
    chips.push(h("span", { class: "chip" }, `${disc.size[0]}×${disc.size[1]}`));
    chips.push(h("span", { class: "chip" }, `PAR ${disc.par}`));
    chips.push(h("span", { class: "chip" }, `VOB ${formatDuration(disc.duration)}`));
    chips.push(h("span", { class: "chip" }, formatBytes(disc.total_bytes)));
  }
  const card = h("section", { class: "disc-card" },
    h("div", { class: "disc-head" }, h("h3", {}, disc.name || disc.label), h("div", { class: "chips" }, chips)));
  if (disc.vob) {
    card.append(h("p", { class: "files", title: disc.source },
      "VOB ", h("b", {}, disc.vob), "　IFO ", h("b", {}, disc.ifo || "无"),
      `　${disc.width}×${disc.height} → ${disc.size[0]}×${disc.size[1]}`));
  }
  if (disc.error) card.append(notice("bad", disc.error));

  if (disc.screenshots) {
    card.append(h("div", { class: "gallery" }, disc.screenshots.map((shot) => {
      if (!shot.ok) {
        return h("div", { class: "thumb failed" }, icon("alert"), "截图失败", h("span", {}, formatSeconds(shot.at)));
      }
      const url = fileUrl(job, shot.file);
      return h("figure", { class: "thumb" },
        h("a", { href: url, target: "_blank", rel: "noopener", title: shot.file },
          h("img", { src: url, alt: `${shot.file}，${formatSeconds(shot.at)}`, loading: "lazy" })),
        h("figcaption", {}, h("span", {}, formatSeconds(shot.at)),
          shot.url ? h("a", { href: shot.url, target: "_blank", rel: "noopener noreferrer" }, "直链", icon("link")) : null));
    })));
  }
  if (disc.mediainfo_file) {
    card.append(codePanel({
      title: "MediaInfo",
      format: "VOB + IFO",
      loader: () => api(fileUrl(job, disc.mediainfo_file)),
      collapsed: true,
    }));
  }
  return card;
}

function renderRunResult(job, result) {
  const nodes = [];
  const shots = result.discs.flatMap((disc) => disc.screenshots || []);
  const okShots = shots.filter((shot) => shot.ok).length;
  const uploaded = shots.filter((shot) => shot.url).length;
  nodes.push(h("p", { class: "summary" },
    h("span", {}, h("strong", {}, result.discs.length), " 张盘"),
    h("span", {}, "截图 ", h("strong", {}, `${okShots} / ${shots.length}`), " 成功"),
    job.params.upload ? h("span", {}, "已上传 ", h("strong", {}, uploaded), " 张") : h("span", {}, "未上传图床")));

  if (result.post) {
    nodes.push(codePanel({ title: "发布说明", format: "BBCode", text: result.post }));
  } else if (job.params.upload) {
    nodes.push(notice("warn", "截图没有全部上传成功，未生成发布说明。请查看日志后重新运行。"));
  } else {
    nodes.push(notice("info", "本次没有上传图床，因此没有生成发布说明。"));
  }
  nodes.push(...result.discs.map((disc) => renderDisc(job, disc)));
  return nodes;
}

function renderTorrentResult(job, result) {
  const count = job.params.announces.length;
  const size = 2 ** job.params.piece_length / 1024 ** 2;
  return [h("div", { class: "file-card" },
    icon("peers"),
    h("div", { class: "text" },
      h("p", { class: "name" }, result.torrent_file),
      h("p", { class: "meta" }, `private · 分块 ${size >= 1 ? `${size} MiB` : `${size * 1024} KiB`} · ${count ? `${count} 个 Tracker` : "未填写 Tracker"}`)),
    h("a", { class: "btn primary", href: fileUrl(job, result.torrent_file), download: result.torrent_file }, icon("download"), "下载种子"))];
}

function renderResult(job) {
  const nodes = [];
  if (job.status === "failed") nodes.push(notice("bad", job.error || "任务失败"));
  if (job.result && job.kind === "run") nodes.push(...renderRunResult(job, job.result));
  if (job.result && job.kind === "torrent") nodes.push(...renderTorrentResult(job, job.result));
  $("job-result").replaceChildren(...nodes);
}

// ---------- 启动 ----------

async function start() {
  try {
    state.config = await api("/api/config");
  } catch (error) {
    if (!(error instanceof AuthError)) showError("login-error", error.message);
    return;
  }
  $("login").hidden = true;
  $("app").hidden = false;
  setupForms();
  const saved = storageGet();
  await browse(saved || (state.config.roots.length === 1 ? state.config.roots[0] : null));
  await refreshJobs();
  if (!state.timer) state.timer = setInterval(refreshJobs, 5000);
}

document.addEventListener("DOMContentLoaded", () => {
  $("logout").append(icon("logout"));
  $("login-form").addEventListener("submit", login);
  $("logout").addEventListener("click", logout);
  $("tab-run").addEventListener("click", () => setActionTab("run"));
  $("tab-torrent").addEventListener("click", () => setActionTab("torrent"));
  $("jtab-result").addEventListener("click", () => setJobTab("result"));
  $("jtab-log").addEventListener("click", () => setJobTab("log"));
  $("run-form").addEventListener("submit", submitRun);
  $("torrent-form").addEventListener("submit", submitTorrent);
  start();
});
