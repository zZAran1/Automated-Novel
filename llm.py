"""DeepSeek 大模型 API 封装。

分工: outline() 用 R1 做大纲/推理，write_text() 用 V3 写正文。

用法:
    from llm import outline, write_text
    plan = outline("为玄幻小说设计三章大纲")
    text = write_text("根据以下大纲写正文: ...")
"""

import json
import os

from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

BASE_URL = "https://api.deepseek.com"
# R1 擅长逻辑推理/大纲规划，V3 擅长文笔输出/写正文
OUTLINE_MODEL = os.getenv("DEEPSEEK_OUTLINE_MODEL", "deepseek-reasoner")
TEXT_MODEL = os.getenv("DEEPSEEK_TEXT_MODEL", "deepseek-chat")

_client = None


def get_client() -> OpenAI:
    global _client
    if _client is None:
        api_key = os.getenv("DEEPSEEK_API_KEY")
        if not api_key or api_key.startswith("sk-在这里"):
            raise RuntimeError("请先在 .env 文件中填入 DEEPSEEK_API_KEY")
        # 显式配置超时与重试：偶发超时/限流自动重试，仍失败由调用方转 502
        _client = OpenAI(api_key=api_key, base_url=BASE_URL, timeout=300.0, max_retries=2)
    return _client


class LLMOutputError(Exception):
    """模型输出不符合 JSON 格式或结构要求，生成内容已作废。"""


def chat(
    prompt: str,
    system: str = "你是一个专业的小说创作助手。",
    model: str = TEXT_MODEL,
    temperature: float = 0.8,
    max_tokens: int = 2000,
    json_mode: bool = False,
) -> str:
    """单轮对话，返回模型文本。json_mode=True 时强制模型输出 JSON。"""
    if json_mode and "json" not in prompt.lower():
        # DeepSeek JSON 模式要求 prompt 中出现 "json" 字样
        prompt += "\n\n请以 json 格式输出。"
    response = get_client().chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": prompt},
        ],
        temperature=temperature,
        max_tokens=max_tokens,
        response_format={"type": "json_object"} if json_mode else None,
    )
    return response.choices[0].message.content or ""


def chat_json(
    prompt: str,
    required_keys: tuple[str, ...] = (),
    system: str = "你是一个专业的小说创作助手。",
    model: str = TEXT_MODEL,
    temperature: float = 0.8,
    max_tokens: int = 2000,
) -> dict:
    """强制 JSON 输出，返回前校验；不合格抛 LLMOutputError。

    校验规则: 必须是合法 JSON 对象，且包含 required_keys 中的全部字段。
    校验失败时生成内容直接丢弃，调用方的输入数据不受影响。
    """
    raw = chat(
        prompt,
        system=system,
        model=model,
        temperature=temperature,
        max_tokens=max_tokens,
        json_mode=True,
    )
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        raise LLMOutputError(f"模型输出不是合法 json，已丢弃: {raw[:200]}") from e
    if not isinstance(data, dict):
        raise LLMOutputError(f"模型输出不是 json 对象，已丢弃: {raw[:200]}")
    missing = [k for k in required_keys if k not in data]
    if missing:
        raise LLMOutputError(f"json 缺少必需字段 {missing}，已丢弃")
    return data


def outline(prompt: str, **kwargs) -> str:
    """大纲/推理任务：用 R1，temperature 固定 0.3 保证逻辑稳定。"""
    kwargs.setdefault("system", "你是一个资深的小说责编，擅长故事结构设计与逻辑推演。")
    kwargs.setdefault("temperature", 0.3)
    kwargs.setdefault("max_tokens", 8000)  # R1 的思考过程也占输出预算，给足防止正文被截断
    return chat(prompt, model=OUTLINE_MODEL, **kwargs)


def write_text(prompt: str, **kwargs) -> str:
    """正文写作任务：用 V3，temperature 0.9 让文笔更生动。"""
    kwargs.setdefault("system", "你是一个专业的网文作者，文笔生动，擅长场景描写与对话。")
    kwargs.setdefault("temperature", 0.9)
    return chat(prompt, model=TEXT_MODEL, **kwargs)


if __name__ == "__main__":
    print(f"大纲模型: {OUTLINE_MODEL} | 正文模型: {TEXT_MODEL}")
    print("outline:", outline("用一句话构思一个玄幻小说的开篇钩子。"))
    print("write_text:", write_text("用一句话介绍你自己。"))
