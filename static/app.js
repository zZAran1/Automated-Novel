const $ = (sel) => document.querySelector(sel);

// 服务端设置了 APP_TOKEN 时，通过 ?token=xxx 访问并随请求携带
const TOKEN = new URLSearchParams(location.search).get("token") || "";

let current = null; // { id, outline, stats, network, chapters: {no: ChapterOut} }

/* ---------- 基础工具 ---------- */

async function api(method, path, body) {
  const headers = { "Content-Type": "application/json" };
  if (TOKEN) headers["Authorization"] = "Bearer " + TOKEN;
  const res = await fetch(path, {
    method,
    headers,
    body: body ? JSON.stringify(body) : undefined,
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const detail = data.detail;
    throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail || res.statusText));
  }
  return data;
}

let toastTimer = null;
function toast(msg, type = "info") {
  const el = $("#toast");
  el.textContent = msg;
  el.className = "toast" + (type === "error" ? " error" : "");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => el.classList.add("hidden"), type === "error" ? 6000 : 3500);
}

/* ---------- 列表分页 ---------- */

// 每页条目数：条目是列表容器的直接子元素，不足一页时分页条自动隐藏
const PAGE_SIZE = 10;
const pageState = {}; // 容器 id -> 当前页码（从 1 开始）

// 显示容器内第 page 页的条目（超出范围自动收敛），返回总页数；无分页条的列表不参与分页
function showPage(containerId, page) {
  const container = $(containerId);
  const pager = $(containerId + "-pager");
  if (!container || !pager) return;
  const items = [...container.children];
  const totalPages = Math.max(1, Math.ceil(items.length / PAGE_SIZE));
  pageState[containerId] = Math.min(Math.max(1, page), totalPages);
  const start = (pageState[containerId] - 1) * PAGE_SIZE;
  items.forEach((el, i) => (el.style.display = i >= start && i < start + PAGE_SIZE ? "" : "none"));
  renderPager(containerId, totalPages);
}

// 渲染分页条：上一页/下一页 + 页码
function renderPager(containerId, totalPages) {
  const pager = $(containerId + "-pager");
  if (totalPages <= 1) {
    pager.innerHTML = "";
    pager.className = "pager hidden";
    return;
  }
  pager.className = "pager";
  pager.innerHTML = `
    <button class="ghost small" data-pg="prev">上一页</button>
    <span class="muted">第 ${pageState[containerId]}/${totalPages} 页</span>
    <button class="ghost small" data-pg="next">下一页</button>`;
  pager.querySelector('[data-pg="prev"]').addEventListener("click", () =>
    showPage(containerId, pageState[containerId] - 1));
  pager.querySelector('[data-pg="next"]').addEventListener("click", () =>
    showPage(containerId, pageState[containerId] + 1));
}

/* ---------- 表单：动态行 ---------- */

function addRow(containerId, fields, cls) {
  // fields 元素为 [占位符, 最大长度]，长度上限与后端 main.py 的字段校验保持一致
  const row = document.createElement("div");
  row.className = "row " + cls;
  row.innerHTML =
    fields.map(([p, max]) => `<input placeholder="${p}" maxlength="${max}">`).join("") +
    `<button class="del" title="删除">✕</button>`;
  row.querySelector(".del").addEventListener("click", () => {
    row.remove();
    showPage(containerId, pageState[containerId] || 1); // 删空一页后自动收敛页码
  });
  $(containerId).appendChild(row);
  return row;
}

function readRows(containerId, keys, requiredIdx = []) {
  const rows = [...$(containerId).children];
  const out = [];
  for (const row of rows) {
    const inputs = [...row.querySelectorAll("input")];
    const values = inputs.map((i) => i.value.trim());
    if (values.every((v) => !v)) continue; // 整行留空直接忽略
    for (const idx of requiredIdx) {
      if (!values[idx]) throw new Error(`「${inputs[idx].placeholder}」为必填项`);
    }
    out.push(Object.fromEntries(keys.map((k, i) => [k, values[i]])));
  }
  return out;
}

/* ---------- 小说列表 ---------- */

async function loadNovelList() {
  const sel = $("#open-select");
  try {
    const list = await api("GET", "/outlines");
    sel.innerHTML = list.length
      ? list
          .map(
            (o) =>
              `<option value="${o.id}">#${o.id} ${o.title}（${o.chapter_count} 章 / 已写 ${o.written_chars} 字）</option>`
          )
          .join("")
      : '<option value="">暂无已保存的小说</option>';
  } catch (e) {
    sel.innerHTML = '<option value="">小说列表加载失败</option>';
  }
}

/* ---------- 新建小说 ---------- */

// 读取表单并构造 /outline 创建入参，校验失败抛 Error
function collectPayload() {
  const title = $("#f-title").value.trim();
  const synopsis = $("#f-synopsis").value.trim();
  if (!title) throw new Error("请填写书名");
  if (!synopsis) throw new Error("请填写故事梗概");

  const chapters = readRows("#chapter-rows", ["title", "summary"], [0]);
  if (!chapters.length) throw new Error("请至少填写一章章节大纲");
  const characters = readRows("#character-rows", ["name", "gender", "identity", "personality"], [0]);
  const relations = readRows("#relation-rows", ["source", "target", "relation"], [0, 1, 2]);
  const names = new Set();
  for (const c of characters) {
    if (names.has(c.name)) throw new Error(`人物「${c.name}」重复，请合并为一个`);
    names.add(c.name);
  }
  for (const r of relations) {
    for (const end of [r.source, r.target]) {
      if (!names.has(end)) throw new Error(`人物关系中的「${end}」不在人物列表中`);
    }
  }
  const totalRaw = $("#f-total").value.trim();
  let totalChars = null;
  if (totalRaw) {
    totalChars = Number(totalRaw);
    if (!Number.isSafeInteger(totalChars) || totalChars <= 0) {
      throw new Error("「全文字数」必须是大于 0 的整数");
    }
  }
  // 与后端 config.py 的 LENGTH_PRESETS、CHAPTER_TARGET_CHARS[0] 保持一致
  const PRESETS = { 短篇: 20000, 中篇: 300000, 长篇: 1100000 };
  const CHAPTER_MIN_CHARS = 1500;
  const length = document.querySelector("input[name=length]:checked").value;
  const planTotal = totalChars ?? PRESETS[length];
  const least = chapters.length * CHAPTER_MIN_CHARS;
  if (planTotal < least) {
    throw new Error(
      `计划全书 ${planTotal} 字与 ${chapters.length} 章不匹配：` +
      `每章至少约 ${CHAPTER_MIN_CHARS} 字，${chapters.length} 章至少需要约 ${least} 字，请增加总字数或减少章数`
    );
  }
  return {
    title,
    genre: $("#f-genre").value.trim(),
    synopsis,
    chapters,
    length,
    total_chars: totalChars,
    characters,
    relations,
  };
}

// 创建小说并进入工作台
async function createNovel(payload) {
  const out = await api("POST", "/outline", payload);
  toast(`创建成功，小说 ID：${out.id}`);
  await enterWorkspace(out.id);
  loadNovelList();
  return out;
}

async function submitOutline() {
  let payload;
  try {
    payload = collectPayload();
  } catch (e) {
    return toast(e.message, "error");
  }

  const btn = $("#btn-submit");
  btn.disabled = true;
  btn.textContent = "创建中…";
  try {
    await createNovel(payload);
  } catch (e) {
    toast(e.message, "error");
  } finally {
    btn.disabled = false;
    btn.textContent = "创建并进入工作台";
  }
}

/* ---------- 小说描述窗口 ---------- */

// 把生成的写作计划填入新建表单
function fillForm(plan) {
  $("#f-title").value = plan.title;
  $("#f-genre").value = plan.genre;
  $("#f-synopsis").value = plan.synopsis;
  const fillRows = (containerId, fields, keys, rows, cls) => {
    $(containerId).innerHTML = "";
    for (const item of rows) {
      const row = addRow(containerId, fields, cls);
      const inputs = row.querySelectorAll("input");
      keys.forEach((k, i) => (inputs[i].value = item[k] ?? ""));
    }
    showPage(containerId, 1);
  };
  fillRows("#chapter-rows", [["章节标题", 100], ["情节概要", 2500]], ["title", "summary"], plan.chapters, "chapter");
  fillRows("#character-rows", [["姓名", 20], ["性别", 10], ["身份", 100], ["性格", 200]], ["name", "gender", "identity", "personality"], plan.characters, "character");
  fillRows("#relation-rows", [["人物A 姓名", 20], ["人物B 姓名", 20], ["关系描述", 200]], ["source", "target", "relation"], plan.relations, "relation");
  const radio = document.querySelector(`input[name=length][value="${plan.length}"]`);
  if (radio) radio.checked = true;
  $("#f-total").value = plan.total_chars ?? "";
}

// 由小说描述生成写作计划（SSE），返回与 /outline 创建入参一致的计划
async function streamPlan(body) {
  const events = await openSse("/outline/plan/stream", body);
  for await (const ev of events) {
    if (ev.type === "progress") {
      $("#plan-status").textContent = ev.detail;
    } else if (ev.type === "done") {
      return ev.outline;
    } else if (ev.type === "error") {
      throw new Error(ev.detail);
    }
  }
  throw new Error("连接中断，未收到写作计划");
}

// 生成写作计划并填充表单，创建小说后直接连续生成整本小说；篇幅由模型根据描述判断
async function planAndStart() {
  const description = $("#f-desc").value.trim();
  if (!description) return toast("请先输入小说描述", "error");

  const btn = $("#btn-plan");
  const status = $("#plan-status");
  btn.disabled = true;
  btn.textContent = "生成中…";
  try {
    status.textContent = "正在生成写作计划…";
    const plan = await streamPlan({ description });
    fillForm(plan);
    status.textContent = "内容已填充，正在创建小说并开始生成…";
    const out = await createNovel(collectPayload());
    status.textContent = "";
    toast(`《${out.outline.title}》已创建，开始连续生成整本小说`);
    autoGenerate($("#btn-auto")); // 不等待，逐章流式输出在工作台展示
  } catch (e) {
    status.textContent = "";
    toast(e.message, "error");
  } finally {
    btn.disabled = false;
    btn.textContent = "生成内容并开写";
  }
}

/* ---------- 工作台 ---------- */

async function enterWorkspace(id) {
  let data;
  try {
    data = await api("GET", `/outline/${id}`);
  } catch (e) {
    return toast(e.message, "error");
  }
  current = { id, outline: data.outline, stats: null, network: null, chapters: {} };

  $("#form-view").classList.add("hidden");
  $("#work-view").classList.remove("hidden");
  $("#work-title").textContent = data.outline.title;
  $("#work-meta").textContent =
    `${data.outline.genre || "未填题材"} · ID ${id} · 共 ${data.outline.chapters.length} 章规划`;

  renderChapters();
  renderContent(null);
  refreshStats();
  loadNetwork(); // 后台构建/加载人物关系网
}

function renderChapters() {
  const ul = $("#chapter-list");
  ul.innerHTML = "";
  current.outline.chapters.forEach((ch, i) => {
    const no = i + 1;
    const li = document.createElement("li");
    li.innerHTML = `
      <div class="ch-title"><b>第${no}章</b> ${ch.title}</div>
      <span class="badge" data-badge="${no}"></span>
      <button class="ghost" data-view="${no}">查看</button>
      <button class="primary" data-gen="${no}">生成</button>
      <button class="ghost" data-rewrite="${no}" title="重新生成本章及其后到已写进度的所有章节">从本章重写</button>
      <button class="ghost" data-del="${no}" title="删除本章正文（章节大纲保留）">删除</button>`;
    li.querySelector("[data-gen]").addEventListener("click", (e) => generateChapter(no, e.target));
    li.querySelector("[data-view]").addEventListener("click", () => viewChapter(no));
    li.querySelector("[data-rewrite]").addEventListener("click", (e) => regenerateFrom(no, e.target));
    li.querySelector("[data-del]").addEventListener("click", (e) => deleteChapter(no, e.target));
    ul.appendChild(li);
  });
  updateBadges();
  showPage("#chapter-list", 1);
}

function updateBadges() {
  // 章节状态优先取本地缓存，未点开过的章节用字数统计兜底，保证打开工作台时显示准确
  const statsIndex = new Map((current.stats?.chapters || []).map((c) => [c.chapter_no, c]));
  document.querySelectorAll("[data-badge]").forEach((el) => {
    const ch = current.chapters[el.dataset.badge] || statsIndex.get(Number(el.dataset.badge));
    el.textContent = ch ? `${ch.char_count} 字` : "未生成";
  });
}

async function generateChapter(no, btn) {
  btn.disabled = true;
  btn.textContent = "生成中…";
  toast(`第${no}章生成中，正文将实时输出`);
  try {
    const ch = await streamChapter(no);
    current.chapters[no] = ch;
    updateBadges();
    renderContent(ch);
    await refreshStats();
    loadNetwork();
    toast(`第${no}章生成完成，${ch.char_count} 字`);
    if (ch.stale_chapters && ch.stale_chapters.length) {
      toast(`注意：第 ${ch.stale_chapters.join("、")} 章基于旧前文生成，内容已过时，建议重新生成`, "error");
    }
  } catch (e) {
    if (e.name === "AbortError") {
      markStopped();
      toast("已中断输出并断开连接，本章正文未保存");
    } else {
      toast(e.message, "error");
    }
  } finally {
    abortCtrl = null;
    disableStop();
    btn.disabled = false;
    btn.textContent = "生成";
  }
}

// ---- SSE 流式公共逻辑 ----

// 与后端 count_chars 同口径：不计空白字符
const countChars = (text) => text.replace(/\s/g, "").length;

// reset 事件的原因 -> 提示文案，与后端 generator.py 的重置原因对应
const RESET_TOASTS = {
  压缩: "正文超长，自动压缩中…",
  扩写: "正文偏短，自动扩写中…",
  重写: "与前面章节高度相似，自动重写中…",
};

// 当前内容区展示的正文原文，供「复制」按钮取用
let displayText = "";
// 当前内容区展示的章节号（0 表示没有展示具体章节），删除章节时用于判断是否清空内容区
let displayedNo = 0;

// 正文按换行切段渲染为 <p>，段首缩进两字；段内不做二次换行处理
function renderProse(prose, text) {
  prose.innerHTML = text
    .split(/\n+/)
    .map((s) => s.trim())
    .filter(Boolean)
    .map((p) => `<p>${p.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")}</p>`)
    .join("");
}

// 一键复制正文；剪贴板 API 不可用时退回隐藏 textarea 方案
async function copyText(text) {
  if (!text) return toast("暂无可复制的正文", "error");
  try {
    await navigator.clipboard.writeText(text);
    return toast("已复制正文");
  } catch (e) {
    const ta = document.createElement("textarea");
    ta.value = text;
    ta.style.position = "fixed";
    ta.style.opacity = "0";
    document.body.appendChild(ta);
    ta.select();
    let ok = false;
    try {
      ok = document.execCommand("copy");
    } catch (e2) {
      ok = false;
    }
    ta.remove();
    toast(ok ? "已复制正文" : "复制失败，请手动选择文本复制", ok ? "info" : "error");
  }
}

let abortCtrl = null; // 当前生成请求的中断控制器

// 中断输出：主动断开 SSE 连接，后端检测到断开后立即停止大模型生成
function stopGeneration() {
  if (abortCtrl) abortCtrl.abort();
}

function markStopped() {
  const badge = $("#live-badge");
  if (badge) badge.textContent = "已停止（未保存）";
}

function disableStop() {
  const stop = $("#btn-stop");
  if (stop) stop.disabled = true;
}

async function openSse(path, body) {
  const headers = {};
  if (TOKEN) headers["Authorization"] = "Bearer " + TOKEN;
  if (body) headers["Content-Type"] = "application/json";
  abortCtrl = new AbortController();
  const res = await fetch(path, {
    method: "POST",
    headers,
    body: body ? JSON.stringify(body) : undefined,
    signal: abortCtrl.signal,
  });
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new Error(typeof data.detail === "string" ? data.detail : res.statusText);
  }
  return sseEvents(res);
}

async function* sseEvents(res) {
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buf = "";
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buf += decoder.decode(value, { stream: true });
    let idx;
    while ((idx = buf.indexOf("\n\n")) >= 0) {
      const frame = buf.slice(0, idx);
      buf = buf.slice(idx + 2);
      const line = frame.split("\n").find((l) => l.startsWith("data: "));
      if (line) yield JSON.parse(line.slice(6));
    }
  }
}

// 把内容区切换为流式输出模式，返回正文区与实时字数徽章；标题栏带「复制」「停止」按钮
function renderStreaming(no, title, badgePrefix = "") {
  const box = $("#content-box");
  displayedNo = 0; // 流式输出中的是未入库正文，不算"展示某一章"
  box.className = "content-box";
  box.innerHTML = `
    <h3>第${no}章 ${title}<span class="badge" id="live-badge">生成中…</span><button class="ghost small" id="btn-copy" style="margin-left:8px">复制</button><button class="ghost small" id="btn-stop" style="margin-left:8px">■ 停止</button></h3>
    <div class="prose"></div>`;
  $("#btn-stop").addEventListener("click", stopGeneration);
  $("#btn-copy").addEventListener("click", () => copyText(displayText));
  const prose = box.querySelector(".prose");
  const badge = $("#live-badge");
  const show = (text) => {
    displayText = text;
    renderProse(prose, text);
    badge.textContent = `${badgePrefix}${countChars(text)} 字（生成中）`;
  };
  return { prose, badge, show };
}

async function streamChapter(no) {
  const events = await openSse(`/outline/${current.id}/chapters/${no}/generate/stream`);
  const { show } = renderStreaming(no, current.outline.chapters[no - 1].title);
  let text = "";
  for await (const ev of events) {
    if (ev.type === "delta") {
      text += ev.text;
      show(text);
    } else if (ev.type === "reset") {
      text = "";
      show("");
      toast(RESET_TOASTS[ev.reason] || "正在重新生成正文…");
    } else if (ev.type === "done") {
      return ev.chapter;
    } else if (ev.type === "error") {
      throw new Error(ev.detail);
    }
  }
  throw new Error("连接中断，未收到生成结果");
}

// 流式连续生成（一键连续生成与从某章重写共用）：返回 { generated, written_chars, total_chars, reason, lastChapter }
async function streamAuto(path) {
  const events = await openSse(path);
  const result = { generated: [], lastChapter: null };
  let show = null;
  let text = "";
  try {
    for await (const ev of events) {
      if (ev.type === "chapter_start") {
        text = "";
        show = renderStreaming(ev.chapter_no, ev.title, `${ev.chapter_no}/${ev.total_chapters} · `).show;
        show("");
        toast(`开始生成第${ev.chapter_no}章 ${ev.title}`);
      } else if (ev.type === "delta") {
        text += ev.text;
        show(text);
      } else if (ev.type === "reset") {
        text = "";
        show("");
        toast(RESET_TOASTS[ev.reason] || "正在重新生成正文…");
      } else if (ev.type === "chapter_done") {
        current.chapters[ev.chapter.chapter_no] = ev.chapter;
        result.generated.push(ev.chapter.chapter_no);
        result.lastChapter = ev.chapter;
        updateBadges();
        toast(`第${ev.chapter.chapter_no}章完成，${ev.chapter.char_count} 字`);
      } else if (ev.type === "done") {
        Object.assign(result, ev);
        return result;
      } else if (ev.type === "error") {
        throw new Error(`第${ev.chapter_no}章生成失败：${ev.detail}`);
      }
    }
    throw new Error("连接中断，连续生成未完成");
  } catch (e) {
    if (e.name === "AbortError") {
      // 用户主动停止：已完成章节已入库，把补入情况带给调用方
      const stop = new Error("已中断连续生成");
      stop.stopped = true;
      stop.generated = result.generated;
      throw stop;
    }
    throw e;
  }
}

async function autoGenerate(btn) {
  btn.disabled = true;
  btn.textContent = "生成中…";
  try {
    const r = await streamAuto(`/outline/${current.id}/chapters/generate/auto`);
    if (r.lastChapter) renderContent(r.lastChapter);
    await refreshStats();
    loadNetwork();
    toast(`${r.reason}，本次补入 ${r.generated.length} 章`);
  } catch (e) {
    if (e.stopped) {
      markStopped();
      toast(`已中断输出并断开连接，本次已补入 ${e.generated.length} 章`);
    } else {
      toast(e.message, "error");
    }
  } finally {
    // 停止/失败时已完成章节已入库，同步一下状态
    abortCtrl = null;
    disableStop();
    updateBadges();
    refreshStats();
    btn.disabled = false;
    btn.textContent = "⚡ 一键连续生成";
  }
}

// 从某章开始重新生成：重写该章起到当前已写进度的所有章节（中间缺失的章会补写）
async function regenerateFrom(no, btn) {
  if (!confirm(`从第${no}章开始重新生成？该章起到当前已写进度的章节会按顺序全部重写，中间缺失的章会补写`)) return;
  btn.disabled = true;
  btn.textContent = "重写中…";
  try {
    const r = await streamAuto(`/outline/${current.id}/chapters/${no}/regenerate/auto`);
    if (r.lastChapter) renderContent(r.lastChapter);
    await refreshStats();
    loadNetwork();
    toast(`${r.reason}，本次重写 ${r.generated.length} 章`);
  } catch (e) {
    if (e.stopped) {
      markStopped();
      toast(`已中断重写，本次已完成 ${e.generated.length} 章`);
    } else {
      toast(e.message, "error");
    }
  } finally {
    abortCtrl = null;
    disableStop();
    updateBadges();
    refreshStats();
    btn.disabled = false;
    btn.textContent = "从本章重写";
  }
}

// 删除某章正文（章节大纲保留），后续已写章节提示内容过时
async function deleteChapter(no, btn) {
  if (!confirm(`确定删除第${no}章的正文？章节大纲保留，之后可重新生成`)) return;
  btn.disabled = true;
  try {
    const r = await api("DELETE", `/outline/${current.id}/chapters/${no}`);
    delete current.chapters[no];
    if (displayedNo === no) renderContent(null); // 正在看的就是被删的这章，清空内容区
    await refreshStats();
    updateBadges();
    toast(`第${no}章正文已删除`);
    if (r.stale_chapters && r.stale_chapters.length) {
      toast(
        `注意：第 ${r.stale_chapters.join("、")} 章基于旧前文生成，建议从第${r.stale_chapters[0]}章开始重新生成`,
        "error"
      );
    }
  } catch (e) {
    toast(e.message, "error");
  } finally {
    btn.disabled = false;
  }
}

async function viewChapter(no) {
  if (current.chapters[no]) return renderContent(current.chapters[no]);
  try {
    const ch = await api("GET", `/outline/${current.id}/chapters/${no}`);
    current.chapters[no] = ch;
    updateBadges();
    renderContent(ch);
  } catch (e) {
    toast(e.message, "error");
  }
}

function renderContent(ch) {
  const box = $("#content-box");
  if (!ch) {
    displayText = "";
    displayedNo = 0;
    box.className = "content-box muted";
    box.textContent = "点击章节右侧的「生成」或「查看」，正文会显示在这里。";
    return;
  }
  displayText = ch.content;
  displayedNo = ch.chapter_no;
  box.className = "content-box";
  box.innerHTML = `
    <h3>第${ch.chapter_no}章 ${ch.title}<span class="badge">${ch.char_count} 字</span><button class="ghost small" id="btn-copy" style="margin-left:8px">复制</button></h3>
    <div class="prose"></div>`;
  $("#btn-copy").addEventListener("click", () => copyText(displayText));
  renderProse(box.querySelector(".prose"), ch.content);
}

async function refreshStats() {
  try {
    current.stats = await api("GET", `/outline/${current.id}/stats`);
  } catch (e) {
    return; // 统计失败不影响主流程
  }
  const { total_chars, written_chars } = current.stats;
  const pct = total_chars ? Math.min(100, (written_chars / total_chars) * 100) : 0;
  $("#progress-bar").style.width = pct.toFixed(2) + "%";
  $("#progress-text").textContent =
    `已写 ${written_chars.toLocaleString()} / 约 ${total_chars.toLocaleString()} 字（${pct.toFixed(1)}%）`;
  updateBadges(); // 统计里有各章字数，同步章节状态徽章
}

async function loadNetwork() {
  const box = $("#network-box");
  box.innerHTML = '<div class="muted">构建/加载中…（首次约 1 分钟）</div>';
  try {
    const net = await api("GET", `/outline/${current.id}/network`);
    current.network = net;
    renderNetwork(net);
  } catch (e) {
    box.innerHTML = `<div class="muted">人物关系网加载失败：${e.message}</div>`;
  }
}

function renderNetwork(net) {
  const box = $("#network-box");
  const chars = net.characters
    .map((c) => {
      const tags = [c.gender, c.identity].filter(Boolean).map((t) => `<span class="tag">${t}</span>`).join("");
      const desc = c.personality ? `<p>${c.personality}</p>` : "";
      return `<div class="char"><b>${c.name}</b>${tags}${desc}</div>`;
    })
    .join("");
  const relations = net.relations
    .map((r) => `<li><b>${r.from}</b> → <b>${r.to}</b>：${r.relation}</li>`)
    .join("");
  box.innerHTML = `
    <div id="network-chars">${chars || '<div class="muted">暂无人物</div>'}</div>
    <div id="network-chars-pager"></div>
    <ul id="network-relations" class="relation-list">${relations || '<li class="muted">暂无关系</li>'}</ul>
    <div id="network-relations-pager"></div>`;
  showPage("#network-chars", 1);
  showPage("#network-relations", 1);
}

/* ---------- 事件绑定 ---------- */

$("#btn-add-chapter").addEventListener("click", () => {
  addRow("#chapter-rows", [["章节标题", 100], ["情节概要", 2500]], "chapter");
  showPage("#chapter-rows", Infinity); // 新增行跳到末页，保证能看到
});
$("#btn-add-character").addEventListener("click", () => {
  addRow("#character-rows", [["姓名", 20], ["性别", 10], ["身份", 100], ["性格", 200]], "character");
  showPage("#character-rows", Infinity);
});
$("#btn-add-relation").addEventListener("click", () => {
  addRow("#relation-rows", [["人物A 姓名", 20], ["人物B 姓名", 20], ["关系描述", 200]], "relation");
  showPage("#relation-rows", Infinity);
});
$("#btn-submit").addEventListener("click", submitOutline);
$("#btn-plan").addEventListener("click", planAndStart);
$("#btn-auto").addEventListener("click", (e) => autoGenerate(e.target));
$("#btn-new").addEventListener("click", () => {
  $("#work-view").classList.add("hidden");
  $("#form-view").classList.remove("hidden");
  window.scrollTo({ top: 0, behavior: "smooth" });
});
$("#btn-open").addEventListener("click", () => {
  const id = Number($("#open-select").value);
  if (!id) return toast("请先选择要打开的小说", "error");
  enterWorkspace(id);
});
$("#btn-delete").addEventListener("click", async () => {
  if (!current) return;
  const title = current.outline.title;
  if (!confirm(`确定删除《${title}》？章节正文与人物关系网将一并删除，不可恢复`)) return;
  try {
    await api("DELETE", `/outline/${current.id}`);
    toast(`已删除《${title}》`);
    current = null;
    $("#work-view").classList.add("hidden");
    $("#form-view").classList.remove("hidden");
    loadNovelList();
  } catch (e) {
    toast(e.message, "error");
  }
});

// 默认给一行章节输入，并加载已有小说列表
addRow("#chapter-rows", [["章节标题", 100], ["情节概要", 2500]], "chapter");
loadNovelList();
