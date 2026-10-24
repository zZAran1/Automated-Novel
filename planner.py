"""从一段小说描述生成完整写作计划：题材、梗概、章节大纲、人物与人物关系。

流程: R1 分析故事设定 -> V3 提炼计划骨架 -> 原创人物关系 -> 分批生成章节大纲 -> 汇总裁剪。
"""

from typing import Any

from characters import format_network, invent_network
from config import CHAPTER_TARGET_CHARS, LENGTH_PRESETS, SUMMARY_MAX_CHARS
from llm import LLMOutputError, chat_json, outline

# 章节大纲分批生成：每批章数过多会超出模型输出预算
BATCH_CHAPTERS = 20
# 每章概要的目标字数（prompt 引导用）
SUMMARY_TARGET_CHARS = (120, 220)


def _clip(value: Any, limit: int) -> str:
    """字段值去掉首尾空白并裁到长度上限（非字符串视为空串），供生成结果越界时兜底。"""
    return value.strip()[:limit] if isinstance(value, str) else ""


def _require_str(data: dict, key: str) -> str:
    """取字符串字段，缺失/非字符串/空白串直接作废，走重试/兜底。"""
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise LLMOutputError(f"{key} 缺失或无效，已丢弃: {data}")
    return value.strip()


def _require_length(data: dict) -> str:
    """取篇幅档位，容忍「短篇小说」等表述；不落在档位内直接作废，走重试。"""
    value = data.get("length")
    if isinstance(value, str):
        for name in LENGTH_PRESETS:
            if name in value:
                return name
    raise LLMOutputError(f"length 不是合法篇幅档位，已丢弃: {value}")


def _plan_skeleton_once(description: str) -> dict:
    """R1 分析故事设定，V3 整理成计划骨架。"""
    analysis = outline(
        f"读者想要的小说描述如下：\n{description}\n\n"
        "请为这部小说做创作规划，写明：\n"
        "1. 书名建议（只给一个）与题材类型\n"
        "2. 故事梗概（400 字以内，主线剧情走向）\n"
        "3. 故事整体结构（分阶段列出各阶段的关键情节、转折与高潮）\n"
        "4. 主要人物设想（每人写明姓名、性别、身份、性格、在故事中的作用）\n"
        "5. 合理的篇幅档位（短篇约2万字/中篇约30万字/长篇约110万字），"
        "按描述与故事规模选一档；描述中明确提到篇幅或字数时以描述为准"
    )
    if len(analysis.strip()) < 50:
        raise LLMOutputError(f"故事分析为空或过短，已丢弃: {analysis[:50]}")
    data = chat_json(
        "把下面的小说创作规划整理成 json，格式为 "
        '{"title": 书名, "genre": 题材, "synopsis": 故事梗概, '
        '"structure": 故事结构与各阶段情节要点, "length": 篇幅档位}，'
        "除 length 外的字段必须是字符串，synopsis 不超过 400 字，"
        "structure 保留各阶段的情节要点；length 只能取 短篇/中篇/长篇 之一。\n\n"
        f"{analysis}",
        required_keys=("title", "genre", "synopsis", "structure", "length"),
        max_tokens=4000,
    )
    return {
        "title": _require_str(data, "title"),
        "genre": _require_str(data, "genre"),
        "synopsis": _require_str(data, "synopsis"),
        "structure": _require_str(data, "structure"),
        "length": _require_length(data),
        "analysis": analysis,
    }


def _plan_skeleton(description: str) -> dict:
    """计划骨架生成，输出不合格重试一次，仍不合格抛 LLMOutputError。"""
    error: LLMOutputError | None = None
    for _ in range(2):
        try:
            return _plan_skeleton_once(description)
        except LLMOutputError as e:
            error = e
    raise error  # type: ignore[misc]


def _plan_cast(skeleton: dict) -> dict:
    """为计划原创人物与人物关系，复用 invent_network 的校验、重试与人物补充逻辑。"""
    return invent_network(
        f"{skeleton['synopsis']}\n\n补充设定参考（故事结构与人物设想，人物姓名以此为准）：\n"
        f"{skeleton['analysis']}",
        skeleton["genre"],
    )


def _plan_batch_once(
    skeleton: dict, cast_text: str, done_text: str, start: int, end: int, total: int
) -> list[dict]:
    """生成一批章节大纲。"""
    lo, hi = SUMMARY_TARGET_CHARS
    data = chat_json(
        f"小说设定:\n"
        f"- 书名: {skeleton['title']}\n"
        f"- 题材: {skeleton['genre']}\n"
        f"- 故事梗概: {skeleton['synopsis']}\n"
        f"- 故事整体结构: {skeleton['structure']}\n\n"
        f"人物（人名必须严格一致）:\n{cast_text}\n\n"
        f"已规划章节:\n{done_text or '（本批是全书开篇）'}\n\n"
        f"任务: 为本书规划第 {start}~{end} 章（全书共 {total} 章）的章节大纲。\n"
        f"要求:\n"
        f"1. 情节承接上文、符合故事整体结构，主线人物只能使用上面人物表中的姓名，"
        f"可新增少量只出场一两章的边缘人物\n"
        f"2. 每章给出章节标题和情节概要，概要 {lo}~{hi} 字，"
        f"写清本章关键事件、人物变化与章末钩子\n"
        f'3. 以 json 格式输出 {{"chapters": [{{"title": 章节标题, "summary": 情节概要}}]}}',
        required_keys=("chapters",),
        max_tokens=6000,
    )
    chapters = data["chapters"]
    if not isinstance(chapters, list) or not chapters:
        raise LLMOutputError(f"chapters 缺失或为空，已丢弃: {data}")
    out = []
    for c in chapters:
        if not isinstance(c, dict):
            raise LLMOutputError(f"章节条目不是对象，已丢弃: {c}")
        out.append({"title": _require_str(c, "title"), "summary": _require_str(c, "summary")})
    return out


def _plan_chapters(
    skeleton: dict, cast_text: str, done_text: str, start: int, end: int, total: int
) -> list[dict]:
    """章节大纲生成，输出不合格重试一次，仍不合格抛 LLMOutputError。"""
    error: LLMOutputError | None = None
    for _ in range(2):
        try:
            return _plan_batch_once(skeleton, cast_text, done_text, start, end, total)
        except LLMOutputError as e:
            error = e
    raise error  # type: ignore[misc]


def _chapter_count(total_chars: int) -> int:
    """按每章目标字数中位值估算章数，保证章数×目标下限不超过计划总字数（符合创建校验）。"""
    per_chapter = sum(CHAPTER_TARGET_CHARS) // 2
    return max(1, total_chars // per_chapter)


def _sanitize(plan: dict) -> dict:
    """裁剪超长字段、过滤非法人物与关系，保证满足 /outline 创建入参的字段上限。"""
    characters = [
        {
            "name": _clip(c.get("name"), 20),
            "gender": _clip(c.get("gender"), 10),
            "identity": _clip(c.get("identity"), 100),
            "personality": _clip(c.get("personality"), 200),
        }
        for c in plan["characters"]
    ]
    characters = [c for c in characters if c["name"]]  # 姓名为空的人物直接丢弃
    names = {c["name"] for c in characters}
    relations = [
        {
            "source": _clip(r.get("source"), 20),
            "target": _clip(r.get("target"), 20),
            "relation": _clip(r.get("relation"), 200),
        }
        for r in plan["relations"]
    ]
    # 关系两端必须仍在人物列表中，断链的关系直接丢弃
    relations = [
        r for r in relations if r["relation"] and r["source"] in names and r["target"] in names
    ]
    chapters = [
        {"title": _clip(c["title"], 100), "summary": _clip(c["summary"], SUMMARY_MAX_CHARS)}
        for c in plan["chapters"]
    ]
    chapters = [c for c in chapters if c["title"] and c["summary"]]
    if not chapters:
        raise LLMOutputError("章节大纲为空，已丢弃")
    return {
        "title": _clip(plan["title"], 100) or "未命名小说",
        "genre": _clip(plan["genre"], 50),
        "synopsis": _clip(plan["synopsis"], 5000),
        "length": plan["length"],
        "chapters": chapters,
        "characters": characters,
        "relations": relations,
    }


def plan_novel_stream(description: str):
    """从一段小说描述生成完整写作计划，篇幅档位由模型按描述判断，逐段产出事件：

    - ("progress", 说明)：当前步骤进度
    - ("done", 计划 dict)：生成完成，字段同 /outline 创建入参（total_chars 恒为空，按档位换算）

    生成内容不合格抛 LLMOutputError，调用方的输入数据不受影响。
    """
    yield ("progress", "正在分析故事设定…")
    skeleton = _plan_skeleton(description)
    total_chars = LENGTH_PRESETS[skeleton["length"]]
    yield ("progress", f"篇幅定为「{skeleton['length']}」（约 {total_chars} 字），正在生成人物与人物关系…")
    cast = _plan_cast(skeleton)
    cast_text = format_network(cast)
    total = _chapter_count(total_chars)
    chapters: list[dict] = []
    while len(chapters) < total:
        start = len(chapters) + 1
        end = min(total, start + BATCH_CHAPTERS - 1)
        # 前文只带一行梗概，保证超长小说的规划 prompt 不会超出模型上下文
        done_text = "\n".join(
            f"第{i}章 {c['title']}：{c['summary'][:40]}" for i, c in enumerate(chapters, 1)
        )
        batch = _plan_chapters(skeleton, cast_text, done_text, start, end, total)
        chapters.extend(batch[: end - start + 1])
        yield ("progress", f"章节大纲 {len(chapters)}/{total} 章")
    plan = _sanitize(
        {
            "title": skeleton["title"],
            "genre": skeleton["genre"],
            "synopsis": skeleton["synopsis"],
            "length": skeleton["length"],
            "chapters": chapters,
            "characters": cast["characters"],
            "relations": [
                {"source": r["from"], "target": r["to"], "relation": r["relation"]}
                for r in cast["relations"]
            ],
        }
    )
    yield ("done", plan)
