"""逐章生成小说正文：遍历人物关系网 -> 召回上文情节 -> 流式生成本章 -> 存入向量库。"""

from characters import format_network
from config import (
    CHAPTER_MAX_CHARS,
    CHAPTER_OVERFLOW_TOLERANCE,
    CHAPTER_TARGET_CHARS,
    PARAGRAPH_MIN_CHARS,
    SIMILARITY_LIMIT,
    SIMILARITY_NGRAM,
)
from llm import write_text_stream
from memory import load_prev_chapters, recall, save_chapter


def count_chars(text: str) -> int:
    """正文字数统计口径：不计空白字符（空格、换行等），标点和数字照常计入。"""
    return sum(1 for ch in text if not ch.isspace())


def _run_pass(prompt: str):
    """跑一轮流式生成：yield ("delta", 增量)，返回该轮完整正文（已去首尾空白）。"""
    parts: list[str] = []
    for chunk in write_text_stream(prompt, max_tokens=6000):
        parts.append(chunk)
        yield ("delta", chunk)
    return "".join(parts).strip()


def _ngrams(text: str) -> set[str]:
    """正文切字符 n-gram 集合（忽略空白），供近重复检测。"""
    chars = [ch for ch in text if not ch.isspace()]
    n = SIMILARITY_NGRAM
    return {"".join(chars[i : i + n]) for i in range(len(chars) - n + 1)}


def _split_paragraphs(text: str) -> list[str]:
    """按换行切出正文段落（与前端展示口径一致），去掉空白段。"""
    return [p.strip() for p in text.split("\n") if p.strip()]


def _containment(ga: set[str], gb: set[str]) -> float:
    """较小集合在较大集合中的包含比例（0~1），捕捉「抄了一段」这种长短不一的相似。"""
    if not ga or not gb:
        return 0.0
    return len(ga & gb) / min(len(ga), len(gb))


def similarity(a: str, b: str) -> float:
    """两段正文的相似度（字符 n-gram Jaccard，0~1），越高越像照搬。"""
    ga, gb = _ngrams(a), _ngrams(b)
    if not ga or not gb:
        return 0.0
    return len(ga & gb) / len(ga | gb)


def paragraph_similarity(a: str, b: str) -> float:
    """两个段落的相似度（字符 n-gram 包含度，0~1）：任一段大半来自另一段即接近 1。"""
    return _containment(_ngrams(a), _ngrams(b))


def most_similar(text: str, prevs: list[tuple[int, str]]) -> tuple[int, float] | None:
    """整章级：找与正文最相似的前面章节，相似度达到上限返回 (章号, 相似度)，否则 None。"""
    hit: tuple[int, float] | None = None
    for no, prev in prevs:
        score = similarity(text, prev)
        if score >= SIMILARITY_LIMIT and (hit is None or score > hit[1]):
            hit = (no, score)
    return hit


def most_similar_paragraph(
    text: str, prevs: list[tuple[int, str]]
) -> tuple[int, int, float] | None:
    """段落级：本章每个段落与前面章节的段落两两比对，命中返回 (前文章号, 本章段号, 相似度)。

    两侧段落都只比对不少于 PARAGRAPH_MIN_CHARS 字的长段落，短句重复不算高度相似。
    """
    cur = [
        (i, _ngrams(p))
        for i, p in enumerate(_split_paragraphs(text), 1)
        if count_chars(p) >= PARAGRAPH_MIN_CHARS
    ]
    hit: tuple[int, int, float] | None = None
    for no, prev in prevs:
        for q in _split_paragraphs(prev):
            if count_chars(q) < PARAGRAPH_MIN_CHARS:
                continue
            gq = _ngrams(q)
            for i, gp in cur:
                score = _containment(gp, gq)
                if score >= SIMILARITY_LIMIT and (hit is None or score > hit[2]):
                    hit = (no, i, score)
    return hit


def _similarity_hit(text: str, prevs: list[tuple[int, str]]) -> str | None:
    """整章与段落两级相似度检测：命中返回可读原因（含章号/段号/相似度），未命中返回 None。"""
    hit = most_similar(text, prevs)
    if hit is not None:
        return f"与已写好的第{hit[0]}章高度相似（相似度 {hit[1]:.0%}）"
    para = most_similar_paragraph(text, prevs)
    if para is not None:
        return f"第{para[1]}段与已写好的第{para[0]}章段落高度相似（相似度 {para[2]:.0%}）"
    return None


def _fit_length(text: str, lo: int, hi: int):
    """把正文压进目标字数范围：超限先压缩（最多两次），过短先扩写一次，仍不合规抛 ValueError。

    yield ("reset", 原因) 事件与 generate_chapter_stream 同步对外透出。
    """
    for _ in range(2):  # 模型偶尔压线超写，最多压缩两次，超出容忍范围才作废
        if count_chars(text) <= CHAPTER_MAX_CHARS:
            break
        yield ("reset", "压缩")
        text = yield from _run_pass(
            f"把下面的小说正文压缩到 {lo}~{hi} 字，保留主要情节和人物，"
            f"绝不能超过 {CHAPTER_MAX_CHARS} 字，只输出正文，不要任何说明:\n\n{text}"
        )
    if not text:
        raise ValueError("模型返回的正文为空，已作废")
    if count_chars(text) < lo:  # 过短先自动扩写一次（与超限压缩对称），仍过短才作废
        yield ("reset", "扩写")
        text = yield from _run_pass(
            f"把下面的小说正文扩写到 {lo}~{hi} 字，保留主要情节和人物，不要改变情节走向，"
            f"只输出正文，不要任何说明:\n\n{text}"
        )
        if not text:
            raise ValueError("扩写后的正文为空，已作废")
    chars = count_chars(text)
    if chars < lo:
        raise ValueError(f"生成的正文 {chars} 字，不足目标下限 {lo} 字，已作废")
    overflow = chars - CHAPTER_MAX_CHARS
    if overflow >= CHAPTER_OVERFLOW_TOLERANCE:
        raise ValueError(
            f"生成的正文 {chars} 字，超出参考上限 {overflow} 字"
            f"（超出达 {CHAPTER_OVERFLOW_TOLERANCE} 字即作废），已作废"
        )
    return text


def generate_chapter_stream(
    novel_id: str,
    chapter_no: int,
    title: str,
    summary: str,
    synopsis: str = "",
    genre: str = "",
    network: dict | None = None,
):
    """流式生成一章正文，逐段产出事件供调用方实时展示：

    - ("delta", 增量)：追加一段正文
    - ("reset", 原因)：上一轮正文作废（压缩/扩写/与前文高度相似重写），清空已展示内容后再收增量
    - ("done", 完整正文)：通过全部校验并已存入向量库，本轮内容为最终正文

    校验失败抛 ValueError，生成内容作废；输入数据不受影响。
    """
    context = "\n\n".join(recall(f"{title} {summary}", novel_id, before_chapter=chapter_no))
    network_text = format_network(network) if network else "（暂无）"
    lo, hi = CHAPTER_TARGET_CHARS
    prompt = f"""小说设定:
- 题材: {genre}
- 故事梗概: {synopsis}

人物关系网（全书贯穿，写作前请通读，人名与人物关系必须严格一致）:
{network_text}

前文相关情节:
{context or "（本章是开篇，无前文）"}

本章任务:
- 章节: 第{chapter_no}章 {title}
- 情节概要: {summary}

写作要求:
1. 正文控制在 {lo}~{hi} 字（目标范围，参考上限 {CHAPTER_MAX_CHARS} 字）
2. 人物关系网中的人物关系必须全书贯彻，从小说开头到结尾保持一致，不得中途改变关系设定
3. 可以出现只在一两章登场的边缘人物（如路人、店主、喽啰），他们不需要进入人物关系网，
   但言行不得与关系网中的设定矛盾，也不要给边缘人物强加主线关系
4. 不得照搬前文：整章和每个段落都不能与前面章节的内容高度相似
5. 只输出正文，不要标题、作者注或其它说明"""
    text = yield from _run_pass(prompt)
    text = yield from _fit_length(text, lo, hi)
    # 整章与段落两级相似度检测：高度相似按作废处理，带对照信息重写，最多两次，仍相似直接作废
    prevs = load_prev_chapters(novel_id, before_chapter=chapter_no)
    reason = _similarity_hit(text, prevs)
    for _ in range(2):
        if reason is None:
            break
        yield ("reset", "重写")
        text = yield from _run_pass(
            f"下面的小说正文{reason}，疑似照搬前文。"
            f"请重新创作第{chapter_no}章 {title} 的正文，情节走向不变，"
            f"但不得照搬已有章节的句子、段落和场景描写，整章和每个段落都不得与前文高度相似，"
            f"只输出正文，不要任何说明:\n\n{text}"
        )
        text = yield from _fit_length(text, lo, hi)
        reason = _similarity_hit(text, prevs)
    if reason is not None:
        raise ValueError(f"生成的正文{reason}，已作废")
    save_chapter(novel_id, chapter_no, title, text)
    yield ("done", text)


def generate_chapter(
    novel_id: str,
    chapter_no: int,
    title: str,
    summary: str,
    synopsis: str = "",
    genre: str = "",
    network: dict | None = None,
) -> str:
    """生成一章正文（非流式）：内部走 generate_chapter_stream，返回最终正文。

    字数校验与作废规则同流式版；成功后已存入向量库。
    """
    text = ""
    for kind, payload in generate_chapter_stream(
        novel_id=novel_id,
        chapter_no=chapter_no,
        title=title,
        summary=summary,
        synopsis=synopsis,
        genre=genre,
        network=network,
    ):
        if kind == "delta":
            text += payload
        elif kind == "reset":
            text = ""
        else:  # done
            text = payload
    return text
