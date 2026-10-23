"""逐章生成小说正文：遍历人物关系网 -> 召回上文情节 -> 流式生成本章 -> 存入向量库。"""

from characters import format_network
from config import CHAPTER_MAX_CHARS, CHAPTER_OVERFLOW_TOLERANCE, CHAPTER_TARGET_CHARS
from llm import write_text_stream
from memory import recall, save_chapter


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
    - ("reset", 原因)：上一轮正文作废（压缩/扩写重写），清空已展示内容后再收增量
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
4. 只输出正文，不要标题、作者注或其它说明"""
    text = yield from _run_pass(prompt)
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
