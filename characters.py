"""人物关系网：正文输出前构建一次，之后每次生成前遍历注入上下文。

人物结构: {"name": 姓名, "gender": 性别, "identity": 身份, "personality": 性格}
关系结构: {"from": 人物A, "to": 人物B, "relation": 关系描述}
"""

from llm import LLMOutputError, chat_json, outline

# 人物少于该数量视为"过少"，自动补充
MIN_CHARACTERS = 3


def build_network(synopsis: str, genre: str, chapter_summaries: list[str]) -> dict:
    """从大纲构建人物关系网：R1 推理梳理 + V3 整理成 json。

    若大纲里提取不到任何人物（人物关系为空），自动原创一套人物关系兜底；
    人物过少时自动补充。模型输出不合格时重试一次，仍不合格抛 LLMOutputError。
    """
    error: LLMOutputError | None = None
    for _ in range(2):
        try:
            data = _build_once(synopsis, genre, chapter_summaries)
            if data["characters"]:
                if len(data["characters"]) < MIN_CHARACTERS:
                    return supplement_network(data, synopsis, genre)
                return data
            error = LLMOutputError("提取到的人物为空")
        except LLMOutputError as e:
            error = e
    # 人物关系为空 -> 自己编一份
    return invent_network(synopsis, genre)


def invent_network(synopsis: str, genre: str) -> dict:
    """人物关系为空时，为这部小说原创一套人物（姓名、性别、身份等）和关系。"""
    data = chat_json(
        "为这部小说原创一套人物关系。要求：\n"
        "1. 每个人物都要有完整的个人信息：姓名、性别、身份、性格\n"
        "2. 人物设定要贴合故事题材，人物之间的关系要合理（亲属/师徒/敌对/恩人等）\n"
        "3. 至少 3 个人物\n"
        "json 格式为 "
        '{"characters": [{"name": 姓名, "gender": 性别, "identity": 身份, '
        '"personality": 性格}], '
        '"relations": [{"from": 人物A, "to": 人物B, "relation": 关系描述}]}。\n\n'
        f"题材: {genre}\n故事梗概: {synopsis}",
        required_keys=("characters", "relations"),
        max_tokens=3000,
    )
    _validate(data)  # 原创模式：要求个人信息齐全，不做大纲来源校验
    if len(data["characters"]) < MIN_CHARACTERS:  # 原创人物过少，按规则自动补充
        return supplement_network(data, synopsis, genre)
    return data


def supplement_network(network: dict, synopsis: str, genre: str) -> dict:
    """人物过少时自动补充新人物，已有人物和关系保持不变。

    新人物带完整个人信息（姓名、性别、身份、性格），并与已有人物建立关系；
    失败重试一次，仍失败抛 LLMOutputError。
    """
    error: LLMOutputError | None = None
    for _ in range(2):
        try:
            data = chat_json(
                "下面的小说已有一个人物关系网，但人物偏少。请补充 2~3 位贴合故事的新人物"
                "（如后续章节会用到的配角），并给出新人物之间、新人物与已有主人物之间的关系。\n"
                "严格要求：禁止修改、重命名或重复已有主人物，只输出新增部分。\n"
                "json 格式为 "
                '{"characters": [{"name": 姓名, "gender": 性别, "identity": 身份, '
                '"personality": 性格}], '
                '"relations": [{"from": 人物A, "to": 人物B, "relation": 关系描述}]}。\n\n'
                f"题材: {genre}\n故事梗概: {synopsis}\n\n"
                f"已有关系网:\n{format_network(network)}",
                required_keys=("characters", "relations"),
                max_tokens=3000,
            )
            # 新人物要求个人信息齐全；关系允许连到已有人物（known_names）
            _validate(data, known_names={c["name"] for c in network["characters"]})
            merged = _merge(network, data)
            if len(merged["characters"]) < MIN_CHARACTERS:  # 补充后仍过少，作废重试
                raise LLMOutputError(f"补充后人物仍不足 {MIN_CHARACTERS} 人，已丢弃")
            return merged
        except LLMOutputError as e:
            error = e
    raise error  # type: ignore[misc]


def _merge(network: dict, new: dict) -> dict:
    """把新增人物/关系合并进已有关系网，已有条目原样保留。"""
    names = {c["name"] for c in network["characters"]}
    for c in new["characters"]:
        if c["name"] not in names:
            network["characters"].append(c)
            names.add(c["name"])
    seen = {(r["from"], r["to"], r["relation"]) for r in network["relations"]}
    for r in new["relations"]:
        if (r["from"], r["to"], r["relation"]) not in seen:
            network["relations"].append(r)
    return network


def _build_once(synopsis: str, genre: str, chapter_summaries: list[str]) -> dict:
    chapters_text = "\n".join(
        f"第{i}章: {s}" for i, s in enumerate(chapter_summaries, 1) if s
    )
    source = f"{genre}\n{synopsis}\n{chapters_text}"
    analysis = outline(
        f"小说题材: {genre}\n故事梗概: {synopsis}\n章节大纲:\n{chapters_text}\n\n"
        "请梳理这部小说的人物及人物之间的关系。严格要求：\n"
        "1. 人物只能来自上面的故事梗概和章节大纲，禁止添加大纲中没有的人物，"
        "禁止套用其它小说的现成设定\n"
        "2. 大纲中出现的每一个人物都要列出（主角、配角、只出场一次的路人也要，宁多勿漏），"
        "写明各自的姓名、性别、身份、性格\n"
        "3. 列出人物之间的关系（亲属/师徒/敌对/恩人/提醒/仇杀等）"
    )
    if len(analysis.strip()) < 50:
        raise LLMOutputError(f"人物分析为空或过短，已丢弃: {analysis[:50]}")
    data = chat_json(
        "把下面的人物分析整理成 json，格式为 "
        '{"characters": [{"name": 姓名, "gender": 性别, "identity": 身份, '
        '"personality": 性格}], '
        '"relations": [{"from": 人物A, "to": 人物B, "relation": 关系描述}]}。'
        "characters 必须覆盖分析中提到的每一个人物，relations 列出所有人物之间的关系。\n\n"
        f"{analysis}",
        required_keys=("characters", "relations"),
        max_tokens=3000,
    )
    _validate(data, source)
    return data


def _validate(
    data: dict, source: str | None = None, known_names: set[str] | None = None
) -> None:
    if not isinstance(data["characters"], list) or not isinstance(data["relations"], list):
        # 类型不对直接作废，走重试/兜底，避免 None 迭代等 TypeError 变成 500
        raise LLMOutputError(f"characters/relations 不是列表，已丢弃: {data}")
    for c in data["characters"]:
        if not isinstance(c, dict) or not isinstance(c.get("name"), str) or not c["name"].strip():
            raise LLMOutputError(f"characters 元素的 name 缺失或无效，已丢弃: {c}")
        if source is None and not {"gender", "identity"} <= c.keys():
            raise LLMOutputError(f"原创人物缺少性别/身份等个人信息，已丢弃: {c}")
    name_list = [c["name"] for c in data["characters"]]
    dupes = sorted({n for n in name_list if name_list.count(n) > 1})
    if dupes:
        raise LLMOutputError(f"人物重名，指向不明，已丢弃: {dupes}")
    names = set(name_list) | (known_names or set())
    for r in data["relations"]:
        if not isinstance(r, dict) or not {"from", "to", "relation"} <= r.keys():
            raise LLMOutputError(f"relations 元素结构不完整，已丢弃: {r}")
        for end in (r["from"], r["to"]):
            if not isinstance(end, str) or end not in names:
                raise LLMOutputError(f"关系中的「{end}」不在人物列表中，已丢弃: {r}")
    if source is not None and data["characters"]:
        # 提取模式：人物必须来自大纲，防止模型虚构其它小说的人物
        matched = sum(1 for c in data["characters"] if c["name"] in source)
        if matched * 2 < len(data["characters"]):
            names = [c["name"] for c in data["characters"]]
            raise LLMOutputError(f"多数人物不在大纲中，疑似虚构，已丢弃: {names}")


def format_network(network: dict) -> str:
    """把人物关系网完整遍历成文本，注入生成正文的上下文。"""
    lines = ["【人物】"]
    for c in network["characters"]:
        info = "，".join(
            p for p in (c.get("gender"), c.get("identity"), c.get("personality")) if p
        )
        lines.append(f"- {c['name']}: {info}")
    lines.append("【关系】")
    for r in network["relations"]:
        lines.append(f"- {r['from']} -> {r['to']}: {r['relation']}")
    return "\n".join(lines)
