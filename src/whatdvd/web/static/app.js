"use strict";

const $ = (id) => document.getElementById(id);
const KIND_LABEL = { dir: "目录", dvd: "DVD", iso: "ISO" };
const JOB_KIND = { run: "截图", torrent: "做种" };
const STORAGE_KEY = "whatdvd.browsePath";

const state = { config: null, parent: null, path: null, selected: null, jobId: null, source: null, timer: null };

class AuthError extends Error {}

// ---------- 工具函数 ----------

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

function formatClock(timestamp) {
  const date = new Date(timestamp * 1000);
  return date.toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" });
}

function basename(path) {
  const parts = path.split("/").filter(Boolean);
  return parts.length ? parts[parts.length - 1] : path;
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

async function copyText(text, statusEl) {
  let ok = false;
  try {
    await navigator.clipboard.writeText(text);
    ok = true;
  } catch {
    // 非 HTTPS 的远程地址没有剪贴板 API，退回到选中文本复制
    const area = h("textarea", { class: "offscreen" });
    area.value = text;
    document.body.append(area);
    area.select();
    try { ok = document.execCommand("copy"); } catch { ok = false; }
    area.remove();
  }
  statusEl.textContent = ok ? "已复制" : "复制失败，请手动选择文本复制";
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

// ---------- 文件浏览 ----------

async function browse(path) {
  showError("browse-error", "");
  let data;
  try {
    data = await api(path ? `/api/browse?path=${encodeURIComponent(path)}` : "/api/browse");
  } catch (error) {
    if (error instanceof AuthError) return;
    if (path) {
      storageSet(null);
      await browse(null);
      showError("browse-error", error.message);
      return;
    }
    showError("browse-error", error.message);
    return;
  }
  state.path = data.path;
  state.parent = data.parent;
  storageSet(data.path);
  $("browse-path").textContent = data.path || "允许浏览的目录";
  $("browse-up").disabled = data.path === null;
  if (data.path) select({ path: data.path, kind: data.kind || "dir" });

  const items = data.entries.map((entry) =>
    h("li", {},
      h("button", {
        type: "button",
        title: entry.path,
        "aria-current": state.selected && state.selected.path === entry.path ? "true" : null,
        onclick: () => (entry.kind === "iso" ? select(entry) : browse(entry.path)),
      },
      h("span", { class: "name" }, entry.name),
      h("span", { class: `kind ${entry.kind}` }, KIND_LABEL[entry.kind] || entry.kind))));
  if (!items.length) items.push(h("li", { class: "muted" }, "这里没有子目录或 ISO 文件。"));
  $("browse-list").replaceChildren(...items);
}

function select(entry) {
  state.selected = entry;
  $("selection-path").textContent = entry.path;
  const kind = $("selection-kind");
  kind.textContent = KIND_LABEL[entry.kind] || entry.kind;
  kind.hidden = false;
  for (const button of document.querySelectorAll(".action button[type=submit]")) button.disabled = false;
  for (const button of document.querySelectorAll("#browse-list button")) {
    button.setAttribute("aria-current", button.title === entry.path ? "true" : "false");
  }
}

// ---------- 提交任务 ----------

function setupForms() {
  const config = state.config;
  $("run-count").value = config.screenshot_count;
  $("torrent-announces").value = config.announces.join("\n");
  const [low, high] = config.piece_length_range;
  const options = [];
  for (let n = low; n <= high; n += 1) {
    options.push(h("option", { value: n, selected: n === config.piece_length }, `2^${n}（${formatBytes(2 ** n)}）`));
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

// ---------- 任务列表与详情 ----------

function statusBadge(job) {
  if (job.status === "done" && job.ok === false) return { cls: "warn", text: "完成（有错误）" };
  const text = { queued: "排队中", running: "运行中", done: "完成", failed: "失败" }[job.status] || job.status;
  return { cls: job.status, text };
}

async function refreshJobs() {
  let jobs;
  try { jobs = await api("/api/jobs"); } catch { return; }
  $("jobs-empty").hidden = jobs.length > 0;
  $("jobs").replaceChildren(...jobs.map((job) => {
    const badge = statusBadge(job);
    return h("li", {},
      h("button", { type: "button", "aria-current": job.id === state.jobId ? "true" : "false", onclick: () => openJob(job.id) },
        h("span", { class: "kind" }, JOB_KIND[job.kind] || job.kind),
        h("span", { class: "name", title: job.path }, basename(job.path)),
        h("span", { class: `badge ${badge.cls}` }, badge.text),
        h("time", {}, formatClock(job.created_at))));
  }));
}

function closeStream() {
  if (state.source) state.source.close();
  state.source = null;
}

function appendLog(event) {
  const log = $("job-log");
  const nearBottom = log.scrollHeight - log.scrollTop - log.clientHeight < 40;
  log.append(h("span", { class: event.level === "error" ? "error" : null }, `${event.message}\n`));
  if (nearBottom) log.scrollTop = log.scrollHeight;
}

function renderHeader(job) {
  $("job").hidden = false;
  $("job-title").textContent = `${JOB_KIND[job.kind] || job.kind}任务`;
  $("job-path").textContent = job.path;
  const badge = statusBadge(job);
  const status = $("job-status");
  status.className = `badge ${badge.cls}`;
  status.textContent = badge.text;
}

async function openJob(id) {
  closeStream();
  state.jobId = id;
  for (const button of document.querySelectorAll("#jobs button")) button.setAttribute("aria-current", "false");
  let job;
  try { job = await api(`/api/jobs/${id}`); } catch { return; }
  if (state.jobId !== id) return;
  renderHeader(job);
  $("job-log").replaceChildren();
  job.events.forEach(appendLog);
  $("job-result").replaceChildren();
  await refreshJobs();
  if (job.status === "done" || job.status === "failed") {
    await renderResult(job);
    return;
  }
  const source = new EventSource(`/api/jobs/${id}/events?after=${job.events.length}`);
  state.source = source;
  source.addEventListener("log", (message) => appendLog(JSON.parse(message.data)));
  source.addEventListener("end", async () => {
    closeStream();
    const detail = await api(`/api/jobs/${id}`);
    if (state.jobId !== id) return;
    renderHeader(detail);
    await renderResult(detail);
    await refreshJobs();
  });
  source.addEventListener("error", () => {
    // 浏览器会自动重连；连接被关闭时检查是否需要重新登录
    if (source.readyState === EventSource.CLOSED) api("/api/config").catch(() => {});
  });
}

function fileUrl(job, name) {
  return `/api/jobs/${job.id}/files/${encodeURIComponent(name)}`;
}

function copyRow(label, getText) {
  const status = h("span", { class: "muted", role: "status" });
  const button = h("button", { type: "button", onclick: async () => copyText(await getText(), status) }, label);
  return h("div", { class: "copy-row" }, button, status);
}

function renderDisc(job, disc) {
  const facts = [];
  const fact = (term, value) => facts.push(h("dt", {}, term), h("dd", {}, value));
  fact("来源", disc.source);
  if (disc.vob) {
    fact("VOB", disc.vob);
    fact("IFO", disc.ifo || "无");
    fact("容量", `${disc.media_type}，共 ${formatBytes(disc.total_bytes)}`);
    fact("制式", disc.standard || `未知（高度 ${disc.height}）`);
    fact("截图尺寸", `${disc.width}x${disc.height}，PAR ${disc.par} → ${disc.size[0]}x${disc.size[1]}`);
    fact("VOB 时长", `${disc.duration} 秒`);
  }
  const section = h("section", { class: "disc" }, h("h3", {}, disc.name || disc.label), h("dl", { class: "facts" }, facts));
  if (disc.error) section.append(h("p", { class: "error" }, disc.error));

  if (disc.screenshots) {
    section.append(h("div", { class: "shots" }, disc.screenshots.map((shot) => {
      if (!shot.ok) return h("div", { class: "shot failed" }, `${formatSeconds(shot.at)} 截图失败`);
      const url = fileUrl(job, shot.file);
      return h("figure", { class: "shot" },
        h("a", { href: url, target: "_blank", rel: "noopener" }, h("img", { src: url, alt: `${shot.file}（${formatSeconds(shot.at)}）`, loading: "lazy" })),
        h("span", {}, formatSeconds(shot.at), shot.url ? " · " : null,
          shot.url ? h("a", { href: shot.url, target: "_blank", rel: "noopener noreferrer" }, "图床直链") : null));
    })));
  }
  if (disc.mediainfo_file) {
    const pre = h("pre", {}, "加载中…");
    let text = null;
    const load = async () => {
      if (text === null) {
        text = await api(fileUrl(job, disc.mediainfo_file));
        pre.textContent = text;
      }
      return text;
    };
    const details = h("details", { ontoggle: () => details.open && load() }, h("summary", {}, "MediaInfo（VOB 在前，IFO 在后）"), pre);
    section.append(details, copyRow("复制 MediaInfo", load));
  }
  return section;
}

async function renderResult(job) {
  const container = $("job-result");
  const nodes = [];
  if (job.status === "failed") nodes.push(h("p", { class: "error" }, job.error || "任务失败"));
  const result = job.result;
  if (result && job.kind === "torrent") {
    nodes.push(h("a", { class: "button", href: fileUrl(job, result.torrent_file), download: result.torrent_file }, `下载 ${result.torrent_file}`));
  }
  if (result && job.kind === "run") {
    if (result.post) {
      const area = h("textarea", { class: "post", readonly: true, spellcheck: "false", "aria-label": "发布说明" });
      area.value = result.post;
      nodes.push(h("section", { class: "disc" }, h("h3", {}, "发布说明"), copyRow("复制发布说明", async () => result.post), area));
    } else if (job.params.upload) {
      nodes.push(h("p", { class: "error" }, "截图没有全部上传成功，未生成发布说明。请重新运行。"));
    } else {
      nodes.push(h("p", { class: "muted" }, "本次没有上传图床，因此没有生成发布说明。"));
    }
    nodes.push(...result.discs.map((disc) => renderDisc(job, disc)));
  }
  container.replaceChildren(...nodes);
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
  await browse(storageGet());
  await refreshJobs();
  if (!state.timer) state.timer = setInterval(refreshJobs, 5000);
}

document.addEventListener("DOMContentLoaded", () => {
  $("login-form").addEventListener("submit", login);
  $("logout").addEventListener("click", logout);
  $("browse-up").addEventListener("click", () => browse(state.parent));
  $("run-form").addEventListener("submit", submitRun);
  $("torrent-form").addEventListener("submit", submitTorrent);
  start();
});
