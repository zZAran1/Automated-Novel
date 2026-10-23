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

/* ---------- 表单：动态行 ---------- */

function addRow(containerId, fields, cls) {
  // fields 元素为 [占位符, 最大长度]，长度上限与后端 main.py 的字段校验保持一致
  const row = document.createElement("div");
  row.className = "row " + cls;
  row.innerHTML =
    fields.map(([p, max]) => `<input placeholder="${p}" maxlength="${max}">`).join("") +
    `<button class="del" title="删除">✕</button>`;
  row.querySelector(".del").addEventListener("click", () => row.remove());
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

async function submitOutline() {
  const title = $("#f-title").value.trim();
  const synopsis = $("#f-synopsis").value.trim();
  if (!title) return toast("请填写书名", "error");
  if (!synopsis) return toast("请填写故事梗概", "error");

  let payload;
  try {
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
    // 与后端 main.py 的 LENGTH_PRESETS、config.py 的 CHAPTER_TARGET_CHARS[0] 保持一致
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
    payload = {
      title,
      genre: $("#f-genre").value.trim(),
      synopsis,
      chapters,
      length,
      total_chars: totalChars,
      characters,
      relations,
    };
  } catch (e) {
    return toast(e.message, "error");
  }

  const btn = $("#btn-submit");
  btn.disabled = true;
  btn.textContent = "创建中…";
  try {
    const out = await api("POST", "/outline", payload);
    toast(`创建成功，小说 ID：${out.id}`);
    await enterWorkspace(out.id);
    loadNovelList();
  } catch (e) {
    toast(e.message, "error");
  } finally {
    btn.disabled = false;
    btn.textContent = "创建并进入工作台";
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
      <button class="primary" data-gen="${no}">生成</button>`;
    li.querySelector("[data-gen]").addEventListener("click", (e) => generateChapter(no, e.target));
    li.querySelector("[data-view]").addEventListener("click", () => viewChapter(no));
    ul.appendChild(li);
  });
  updateBadges();
}

function updateBadges() {
  document.querySelectorAll("[data-badge]").forEach((el) => {
    const ch = current.chapters[el.dataset.badge];
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
    toast(e.message, "error");
  } finally {
    btn.disabled = false;
    btn.textContent = "生成";
  }
}

// 流式生成：消费 SSE 事件（delta 追加 / reset 重写清空 / done 完成 / error 失败），正文实时显示
async function streamChapter(no) {
  const headers = {};
  if (TOKEN) headers["Authorization"] = "Bearer " + TOKEN;
  const res = await fetch(`/outline/${current.id}/chapters/${no}/generate/stream`, {
    method: "POST",
    headers,
  });
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new Error(typeof data.detail === "string" ? data.detail : res.statusText);
  }
  const title = current.outline.chapters[no - 1].title;
  const box = $("#content-box");
  box.className = "content-box";
  box.innerHTML = `
    <h3>第${no}章 ${title}<span class="badge" id="live-badge">生成中…</span></h3>
    <div class="prose"></div>`;
  const prose = box.querySelector(".prose");
  const badge = $("#live-badge");
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buf = "";
  let text = "";
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buf += decoder.decode(value, { stream: true });
    let idx;
    while ((idx = buf.indexOf("\n\n")) >= 0) {
      const frame = buf.slice(0, idx);
      buf = buf.slice(idx + 2);
      const line = frame.split("\n").find((l) => l.startsWith("data: "));
      if (!line) continue;
      const ev = JSON.parse(line.slice(6));
      if (ev.type === "delta") {
        text += ev.text;
        prose.textContent = text;
        // 与后端 count_chars 同口径：不计空白字符
        badge.textContent = `${text.replace(/\s/g, "").length} 字（生成中）`;
      } else if (ev.type === "reset") {
        text = "";
        prose.textContent = "";
        toast(ev.reason === "扩写" ? "正文偏短，自动扩写中…" : "正文超长，自动压缩中…");
      } else if (ev.type === "done") {
        return ev.chapter;
      } else if (ev.type === "error") {
        throw new Error(ev.detail);
      }
    }
  }
  throw new Error("连接中断，未收到生成结果");
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
    box.className = "content-box muted";
    box.textContent = "点击章节右侧的「生成」或「查看」，正文会显示在这里。";
    return;
  }
  box.className = "content-box";
  box.innerHTML = `
    <h3>第${ch.chapter_no}章 ${ch.title}<span class="badge">${ch.char_count} 字</span></h3>
    <div class="prose"></div>`;
  box.querySelector(".prose").textContent = ch.content;
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
  if (!net.characters.length) {
    box.innerHTML = '<div class="muted">暂无人物</div>';
    return;
  }
  const chars = net.characters
    .map((c) => {
      const tags = [c.gender, c.identity].filter(Boolean).map((t) => `<span class="tag">${t}</span>`).join("");
      const desc = c.personality ? `<p>${c.personality}</p>` : "";
      return `<div class="char"><b>${c.name}</b>${tags}${desc}</div>`;
    })
    .join("");
  const relations = net.relations.length
    ? `<ul class="relation-list">${net.relations
        .map((r) => `<li><b>${r.from}</b> → <b>${r.to}</b>：${r.relation}</li>`)
        .join("")}</ul>`
    : '<div class="muted" style="margin-top:12px">暂无关系</div>';
  box.innerHTML = chars + relations;
}

/* ---------- 事件绑定 ---------- */

$("#btn-add-chapter").addEventListener("click", () =>
  addRow("#chapter-rows", [["章节标题", 100], ["情节概要", 2500]], "chapter"));
$("#btn-add-character").addEventListener("click", () =>
  addRow("#character-rows", [["姓名", 20], ["性别", 10], ["身份", 100], ["性格", 200]], "character"));
$("#btn-add-relation").addEventListener("click", () =>
  addRow("#relation-rows", [["人物A 姓名", 20], ["人物B 姓名", 20], ["关系描述", 200]], "relation"));
$("#btn-submit").addEventListener("click", submitOutline);
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
