"""小说创作 API 服务。

启动: .venv\\Scripts\\python.exe -m uvicorn main:app --reload
接口文档: http://127.0.0.1:8000/docs
"""

import json
import os
import threading
import uuid
from pathlib import Path
from typing import Annotated, Any, Literal

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from openai import OpenAIError
from pydantic import BaseModel, BeforeValidator, Field, ValidationError, model_validator

from characters import MIN_CHARACTERS, build_network, supplement_network
from config import CHAPTER_TARGET_CHARS, LENGTH_PRESETS, SUMMARY_MAX_CHARS
from generator import count_chars, generate_chapter, generate_chapter_stream
from llm import LLMOutputError
from memory import delete_chapter, delete_novel
from planner import plan_novel_stream

app = FastAPI(title="Automated Novel API")


@app.middleware("http")
async def _auth(request: Request, call_next):
    """可选鉴权：.env 设置 APP_TOKEN 后，业务接口需携带 Authorization: Bearer <token>。"""
    token = os.getenv("APP_TOKEN")
    if token and request.url.path.startswith("/outline"):
        if request.headers.get("Authorization") != f"Bearer {token}":
            return JSONResponse({"detail": "未授权，请在链接后携带 ?token=你的APP_TOKEN"}, status_code=401)
    return await call_next(request)


@app.middleware("http")
async def _no_cache_static(request: Request, call_next):
    """页面与静态资源不走强缓存（仍可用 ETag 协商），前端改版后刷新页面即生效。"""
    response = await call_next(request)
    if request.url.path == "/" or request.url.path.startswith("/static"):
        response.headers["Cache-Control"] = "no-cache"
    return response


def _strip_str(v: Any) -> Any:
    """入参字符串去掉首尾空白（含全角空格），纯空白变空串后由 min_length=1 拒绝。"""
    return v.strip() if isinstance(v, str) else v


TrimmedStr = Annotated[str, BeforeValidator(_strip_str)]


class ChapterOutline(BaseModel):
    """单章大纲。"""

    title: TrimmedStr = Field(..., min_length=1, max_length=100, description="章节标题")
    summary: TrimmedStr = Field(
        "",
        max_length=SUMMARY_MAX_CHARS,
        description=f"本章情节概要，不超过 {SUMMARY_MAX_CHARS} 字",
    )


class CharacterIn(BaseModel):
    """前端可选提交的人物（留空则自动生成）。"""

    name: TrimmedStr = Field(..., min_length=1, max_length=20, description="姓名")
    gender: TrimmedStr = Field("", max_length=10, description="性别")
    identity: TrimmedStr = Field("", max_length=100, description="身份，如：少年剑客/镖师")
    personality: TrimmedStr = Field("", max_length=200, description="性格")


class RelationIn(BaseModel):
    """前端可选提交的人物关系。"""

    source: TrimmedStr = Field(..., min_length=1, max_length=20, description="人物A姓名")
    target: TrimmedStr = Field(..., min_length=1, max_length=20, description="人物B姓名")
    relation: TrimmedStr = Field(..., min_length=1, max_length=200, description="关系描述")


class OutlineIn(BaseModel):
    """小说大纲输入。"""

    title: TrimmedStr = Field(..., min_length=1, max_length=100, description="小说名")
    genre: TrimmedStr = Field("", max_length=50, description="题材类型，如：玄幻/都市/科幻")
    synopsis: TrimmedStr = Field(..., min_length=1, max_length=5000, description="故事整体梗概")
    chapters: list[ChapterOutline] = Field(
        ..., min_length=1, description="章节大纲列表（至少一章）"
    )
    length: Literal["短篇", "中篇", "长篇"] = Field(
        "短篇", description="篇幅档位：短篇约2万字/中篇约30万字/长篇约110万字"
    )
    total_chars: int | None = Field(
        None, gt=0, description="规定全文字数，留空则按篇幅档位换算"
    )
    characters: list[CharacterIn] = Field(
        default_factory=list, description="人物列表，留空则自动生成"
    )
    relations: list[RelationIn] = Field(
        default_factory=list, description="人物关系，留空则自动生成"
    )

    @model_validator(mode="after")
    def _check_characters(self) -> "OutlineIn":
        """人物名不重复，且人物关系的两端必须在人物列表中，防止指向歧义/关系断链。"""
        names = [c.name for c in self.characters]
        dupes = sorted({n for n in names if names.count(n) > 1})
        if dupes:
            raise ValueError(f"人物重名，请合并为一个：{'、'.join(dupes)}")
        name_set = set(names)
        for r in self.relations:
            for end in (r.source, r.target):
                if end not in name_set:
                    raise ValueError(f"人物关系中的「{end}」不在人物列表中")
        return self

    @model_validator(mode="after")
    def _check_plan_chars(self) -> "OutlineIn":
        """计划总字数与章数匹配：按每章目标下限估算，计划太少会让进度爆表。"""
        total = self.total_chars or LENGTH_PRESETS[self.length]
        least = len(self.chapters) * CHAPTER_TARGET_CHARS[0]
        if total < least:
            raise ValueError(
                f"计划全书 {total} 字与 {len(self.chapters)} 章不匹配："
                f"每章至少约 {CHAPTER_TARGET_CHARS[0]} 字，{len(self.chapters)} 章至少需要约 {least} 字，"
                "请增加总字数或减少章数"
            )
        return self


class PlanIn(BaseModel):
    """小说描述输入：篇幅由模型根据描述判断，生成完整写作计划。"""

    description: TrimmedStr = Field(..., min_length=1, max_length=10000, description="小说描述")


class OutlineOut(BaseModel):
    id: int
    outline: OutlineIn


class ChapterOut(BaseModel):
    """已生成的章节正文。"""

    outline_id: int
    chapter_no: int
    title: str
    content: str
    char_count: int
    stale_chapters: list[int] = Field(
        default_factory=list,
        description="本次生成时已存在的后续章节：内容基于旧前文，建议重新生成",
    )


class ChapterStat(BaseModel):
    """单章字数记录。"""

    chapter_no: int
    title: str
    char_count: int


class StatsOut(BaseModel):
    """全书字数统计。"""

    outline_id: int
    length: str
    total_chars: int = Field(..., description="计划全文字数（指定值或档位换算值）")
    written_chars: int = Field(..., description="已生成的总字数")
    chapters: list[ChapterStat] = Field(default_factory=list, description="每章字数记录")


class OutlineSummary(BaseModel):
    """小说列表条目。"""

    id: int
    title: str
    genre: str
    chapter_count: int
    written_chars: int


_store: dict[int, OutlineIn] = {}
_chapters: dict[tuple[int, int], ChapterOut] = {}
_networks: dict[int, dict] = {}
# 每本小说使用独立 UUID 作为向量库唯一标识，避免 novel_id 复用导致跨小说内容召回串书
_novel_keys: dict[int, str] = {}

STATIC_DIR = Path(__file__).parent / "static"
# 业务数据落盘文件：大纲、章节正文、人物关系网重启后从这里恢复
DATA_FILE = Path(__file__).parent / "data" / "novels.json"
_save_lock = threading.Lock()


def _save() -> None:
    """把全部业务数据原子写入 JSON（先写临时文件再替换，避免写一半损坏）。"""
    payload = {
        "novels": {
            str(oid): {
                "outline": outline.model_dump(),
                "novel_key": _novel_keys[oid],
                "network": _networks.get(oid),
                "chapters": {
                    str(no): ch.model_dump()
                    for (o, no), ch in _chapters.items()
                    if o == oid
                },
            }
            for oid, outline in _store.items()
        }
    }
    with _save_lock:
        DATA_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = DATA_FILE.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, DATA_FILE)


def _load() -> None:
    """启动时从 JSON 恢复业务数据，文件不存在则从空库开始。"""
    if not DATA_FILE.exists():
        return
    raw = json.loads(DATA_FILE.read_text(encoding="utf-8"))
    for oid_str, rec in raw["novels"].items():
        oid = int(oid_str)
        _store[oid] = OutlineIn.model_validate(rec["outline"])
        _novel_keys[oid] = rec["novel_key"]
        if rec["network"] is not None:
            _networks[oid] = rec["network"]
        for no_str, ch in rec["chapters"].items():
            _chapters[(oid, int(no_str))] = ChapterOut.model_validate(ch)


_load()


def _get_network(outline_id: int, outline: OutlineIn) -> dict:
    """获取人物关系网：前端自带 > 大纲提取 > 自动原创/补充，构建一次后缓存。"""
    network = _networks.get(outline_id)
    if network is not None:
        return network
    try:
        if outline.characters:  # 前端已提供人物关系
            network = {
                "characters": [c.model_dump() for c in outline.characters],
                "relations": [
                    {"from": r.source, "to": r.target, "relation": r.relation}
                    for r in outline.relations
                ],
            }
            if len(network["characters"]) < MIN_CHARACTERS:  # 人物过少，自动补充
                network = supplement_network(network, outline.synopsis, outline.genre)
        else:  # 前端人物为空：从大纲提取，提取不到则自动原创一套
            network = build_network(
                outline.synopsis,
                outline.genre,
                [c.summary for c in outline.chapters],
            )
    except LLMOutputError as e:  # 关系网构建/补充失败，作废
        raise HTTPException(status_code=502, detail=f"人物关系网构建失败: {e}")
    except OpenAIError as e:  # 超时/限流等服务异常转 502，不暴露 500
        raise HTTPException(status_code=502, detail=f"DeepSeek 服务调用失败: {e}")
    _networks[outline_id] = network
    _save()
    return network


@app.post("/outline", response_model=OutlineOut, summary="接收小说大纲")
def create_outline(outline: OutlineIn) -> OutlineOut:
    outline_id = max(_store, default=0) + 1  # id 随数据持久化，重启后接着排不复用
    _store[outline_id] = outline
    _novel_keys[outline_id] = uuid.uuid4().hex
    _save()
    return OutlineOut(id=outline_id, outline=outline)


@app.post(
    "/outline/plan/stream",
    summary="由小说描述生成写作计划（SSE）",
    description=(
        "输入一段小说描述，由模型判断篇幅并生成题材、故事梗概、章节大纲、人物与人物关系，"
        "事件类型：progress（生成进度）/ done（生成完成，携带与 /outline 创建入参一致的计划）/"
        " error（生成失败，携带原因）"
    ),
)
def plan_stream_api(plan_in: PlanIn) -> StreamingResponse:
    def events():
        try:
            for kind, payload in plan_novel_stream(plan_in.description):
                if kind == "progress":
                    yield _sse({"type": "progress", "detail": payload})
                else:  # done：校验计划可直接提交创建
                    outline = OutlineIn.model_validate(payload)
                    yield _sse({"type": "done", "outline": outline.model_dump()})
        except LLMOutputError as e:  # 生成内容不合格即作废
            yield _sse({"type": "error", "detail": f"写作计划生成失败: {e}"})
        except ValidationError as e:  # 计划未通过创建入参校验，作废
            yield _sse({"type": "error", "detail": f"生成的写作计划未通过校验: {e}"})
        except OpenAIError as e:  # 超时/限流等服务异常，不暴露 500
            yield _sse({"type": "error", "detail": f"DeepSeek 服务调用失败: {e}"})

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/outline/{outline_id}", response_model=OutlineOut, summary="查询小说大纲")
def get_outline(outline_id: int) -> OutlineOut:
    if outline_id not in _store:
        raise HTTPException(status_code=404, detail=f"大纲 {outline_id} 不存在")
    return OutlineOut(id=outline_id, outline=_store[outline_id])


def _load_chapter(outline_id: int, chapter_no: int) -> tuple[OutlineIn, ChapterOutline]:
    """定位一章：大纲不存在返回 404，章节号超出范围返回 400。"""
    if outline_id not in _store:
        raise HTTPException(status_code=404, detail=f"大纲 {outline_id} 不存在")
    outline = _store[outline_id]
    if not 1 <= chapter_no <= len(outline.chapters):
        raise HTTPException(
            status_code=400,
            detail=f"章节号 {chapter_no} 超出范围（该大纲共 {len(outline.chapters)} 章）",
        )
    return outline, outline.chapters[chapter_no - 1]


def _store_chapter(outline_id: int, chapter_no: int, title: str, content: str) -> ChapterOut:
    """记录本章正文与字数，并把基于旧前文的后续章节标记为内容过时。"""
    out = ChapterOut(
        outline_id=outline_id,
        chapter_no=chapter_no,
        title=title,
        content=content,
        char_count=count_chars(content),
        # 已生成的后续章节是按旧前文写的，本次生成后内容过时
        stale_chapters=sorted(no for (oid, no) in _chapters if oid == outline_id and no > chapter_no),
    )
    _chapters[(outline_id, chapter_no)] = out
    _save()
    return out


@app.post(
    "/outline/{outline_id}/chapters/{chapter_no}/generate",
    response_model=ChapterOut,
    summary="生成一章正文",
)
def generate_chapter_api(outline_id: int, chapter_no: int) -> ChapterOut:
    outline, chapter = _load_chapter(outline_id, chapter_no)
    # 输出正文前先确保人物关系网已构建，每次生成都会完整遍历它
    network = _get_network(outline_id, outline)
    try:
        content = generate_chapter(
            novel_id=_novel_keys[outline_id],
            chapter_no=chapter_no,
            title=chapter.title,
            summary=chapter.summary,
            synopsis=outline.synopsis,
            genre=outline.genre,
            network=network,
        )
    except ValueError as e:  # 超字数等生成校验失败
        raise HTTPException(status_code=422, detail=str(e))
    except OpenAIError as e:  # 超时/限流等服务异常转 502，不暴露 500
        raise HTTPException(status_code=502, detail=f"DeepSeek 服务调用失败: {e}")
    return _store_chapter(outline_id, chapter_no, chapter.title, content)


def _sse(data: dict) -> str:
    """把事件序列化为 SSE 帧（data: {...}\\n\\n）。"""
    return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"


@app.post(
    "/outline/{outline_id}/chapters/{chapter_no}/generate/stream",
    summary="流式生成一章正文（SSE）",
    description=(
        "以 text/event-stream 逐段推送正文，事件类型："
        "delta（追加文本）/ reset（正文重写，清空后再收增量）/"
        " done（生成完成，携带章节数据）/ error（生成失败，携带原因）"
    ),
)
def generate_chapter_stream_api(outline_id: int, chapter_no: int) -> StreamingResponse:
    outline, chapter = _load_chapter(outline_id, chapter_no)
    # 校验与关系网构建失败在这里直接抛 HTTPException，不进入流
    network = _get_network(outline_id, outline)

    def events():
        try:
            for kind, payload in generate_chapter_stream(
                novel_id=_novel_keys[outline_id],
                chapter_no=chapter_no,
                title=chapter.title,
                summary=chapter.summary,
                synopsis=outline.synopsis,
                genre=outline.genre,
                network=network,
            ):
                if kind == "delta":
                    yield _sse({"type": "delta", "text": payload})
                elif kind == "reset":
                    yield _sse({"type": "reset", "reason": payload})
                else:  # done
                    out = _store_chapter(outline_id, chapter_no, chapter.title, payload)
                    yield _sse({"type": "done", "chapter": out.model_dump()})
        except ValueError as e:  # 超字数等生成校验失败
            yield _sse({"type": "error", "detail": str(e)})
        except OpenAIError as e:  # 超时/限流等服务异常，不暴露 500
            yield _sse({"type": "error", "detail": f"DeepSeek 服务调用失败: {e}"})

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def _written_chars(outline_id: int) -> int:
    """该小说已生成正文的累计字数。"""
    return sum(ch.char_count for (oid, _), ch in _chapters.items() if oid == outline_id)


def _stream_chapter_events(outline_id: int, outline: OutlineIn, no: int, network: dict):
    """产出一章的流式事件 dict（chapter_start/delta/reset/chapter_done），完成的章节会入库。"""
    chapter = outline.chapters[no - 1]
    yield {
        "type": "chapter_start",
        "chapter_no": no,
        "title": chapter.title,
        "total_chapters": len(outline.chapters),
    }
    for kind, payload in generate_chapter_stream(
        novel_id=_novel_keys[outline_id],
        chapter_no=no,
        title=chapter.title,
        summary=chapter.summary,
        synopsis=outline.synopsis,
        genre=outline.genre,
        network=network,
    ):
        if kind == "delta":
            yield {"type": "delta", "chapter_no": no, "text": payload}
        elif kind == "reset":
            yield {"type": "reset", "chapter_no": no, "reason": payload}
        else:  # done
            out = _store_chapter(outline_id, no, chapter.title, payload)
            yield {"type": "chapter_done", "chapter": out.model_dump()}


@app.post(
    "/outline/{outline_id}/chapters/generate/auto",
    summary="一键连续自动生成正文（SSE）",
    description=(
        "按章节顺序自动补入缺失章节的正文，已生成的章节跳过不重写；"
        "每章事件同单章流式接口（delta/reset 携带 chapter_no），"
        "另含 chapter_start（开始新一章）/ chapter_done（一章完成）/ done（本次运行结束，携带原因）。"
        "累计字数达到全书目标后不再开启新章节。"
    ),
)
def generate_auto_api(outline_id: int) -> StreamingResponse:
    if outline_id not in _store:
        raise HTTPException(status_code=404, detail=f"大纲 {outline_id} 不存在")
    outline = _store[outline_id]
    targets = [
        no for no in range(1, len(outline.chapters) + 1) if (outline_id, no) not in _chapters
    ]
    if not targets:
        raise HTTPException(status_code=400, detail="没有需要生成的章节，所有章节均已生成")
    # 连续生成全程共用同一份人物关系网，每章生成前都会完整遍历它
    network = _get_network(outline_id, outline)
    total_chars = outline.total_chars or LENGTH_PRESETS[outline.length]

    def events():
        generated: list[int] = []
        current_no = targets[0]
        try:
            for no in targets:
                current_no = no
                written = _written_chars(outline_id)
                if written >= total_chars:
                    # 已达全书目标字数，不再开启新章节
                    yield _sse({
                        "type": "done",
                        "generated": generated,
                        "written_chars": written,
                        "total_chars": total_chars,
                        "reason": f"累计字数已达全书目标 {total_chars} 字，剩余章节未生成",
                    })
                    return
                for ev in _stream_chapter_events(outline_id, outline, no, network):
                    yield _sse(ev)
                    if ev["type"] == "chapter_done":
                        generated.append(no)
            yield _sse({
                "type": "done",
                "generated": generated,
                "written_chars": _written_chars(outline_id),
                "total_chars": total_chars,
                "reason": "全部缺失章节生成完成",
            })
        except ValueError as e:  # 某章校验失败即停止本次连续生成，已完成章节保留
            yield _sse({"type": "error", "chapter_no": current_no, "detail": str(e)})
        except OpenAIError as e:
            yield _sse({
                "type": "error",
                "chapter_no": current_no,
                "detail": f"DeepSeek 服务调用失败: {e}",
            })

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.post(
    "/outline/{outline_id}/chapters/{chapter_no}/regenerate/auto",
    summary="从某章开始重新生成（SSE）",
    description=(
        "按章节顺序重写第 chapter_no 章到当前已写进度之间的所有章节："
        "已写的章节全部重写，中间缺失的章会补写；超出已写进度的章节不补，"
        "可用一键连续生成接着写。每章事件同一键连续生成接口，done 的 reason 说明本次结果。"
    ),
)
def regenerate_auto_api(outline_id: int, chapter_no: int) -> StreamingResponse:
    outline, _ = _load_chapter(outline_id, chapter_no)
    written_nos = sorted(no for (oid, no) in _chapters if oid == outline_id and no >= chapter_no)
    if not written_nos:
        raise HTTPException(
            status_code=400, detail=f"第 {chapter_no} 章及之后还没有已生成的章节，无法重新生成"
        )
    # 重写范围：该章起到当前已写进度（最大已写章号）为止，中间缺失的章节一并补写
    targets = list(range(chapter_no, written_nos[-1] + 1))
    network = _get_network(outline_id, outline)

    def events():
        regenerated: list[int] = []
        current_no = targets[0]
        try:
            for no in targets:
                current_no = no
                for ev in _stream_chapter_events(outline_id, outline, no, network):
                    yield _sse(ev)
                    if ev["type"] == "chapter_done":
                        regenerated.append(no)
            yield _sse({
                "type": "done",
                "generated": regenerated,
                "written_chars": _written_chars(outline_id),
                "total_chars": outline.total_chars or LENGTH_PRESETS[outline.length],
                "reason": f"第 {chapter_no} 章起到第 {written_nos[-1]} 章已全部重新生成",
            })
        except ValueError as e:  # 某章校验失败即停止，已完成章节保留
            yield _sse({"type": "error", "chapter_no": current_no, "detail": str(e)})
        except OpenAIError as e:
            yield _sse({
                "type": "error",
                "chapter_no": current_no,
                "detail": f"DeepSeek 服务调用失败: {e}",
            })

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get(
    "/outline/{outline_id}/chapters/{chapter_no}",
    response_model=ChapterOut,
    summary="查询已生成的章节正文",
)
def get_chapter(outline_id: int, chapter_no: int) -> ChapterOut:
    _load_chapter(outline_id, chapter_no)  # 先校验大纲存在、章节号在范围内
    if (outline_id, chapter_no) not in _chapters:
        raise HTTPException(
            status_code=404, detail=f"章节 {outline_id}/{chapter_no} 尚未生成"
        )
    return _chapters[(outline_id, chapter_no)]


@app.delete(
    "/outline/{outline_id}/chapters/{chapter_no}",
    summary="删除某章正文（大纲保留，后续已生成章节标记为内容过时）",
)
def delete_chapter_api(outline_id: int, chapter_no: int) -> dict:
    _load_chapter(outline_id, chapter_no)  # 先校验大纲存在、章节号在范围内
    if (outline_id, chapter_no) not in _chapters:
        raise HTTPException(
            status_code=404, detail=f"章节 {outline_id}/{chapter_no} 尚未生成"
        )
    del _chapters[(outline_id, chapter_no)]
    _save()
    delete_chapter(_novel_keys[outline_id], chapter_no)
    # 本章正文删除后，后面已写的章节基于旧前文生成，提示重新生成
    return {
        "deleted": chapter_no,
        "stale_chapters": sorted(
            no for (oid, no) in _chapters if oid == outline_id and no > chapter_no
        ),
    }


@app.get(
    "/outline/{outline_id}/stats",
    response_model=StatsOut,
    summary="全书字数统计（每章字数记录）",
)
def get_stats(outline_id: int) -> StatsOut:
    if outline_id not in _store:
        raise HTTPException(status_code=404, detail=f"大纲 {outline_id} 不存在")
    outline = _store[outline_id]
    chapters = [
        ChapterStat(chapter_no=no, title=ch.title, char_count=ch.char_count)
        for (oid, no), ch in sorted(_chapters.items())
        if oid == outline_id
    ]
    return StatsOut(
        outline_id=outline_id,
        length=outline.length,
        total_chars=outline.total_chars or LENGTH_PRESETS[outline.length],
        written_chars=sum(c.char_count for c in chapters),
        chapters=chapters,
    )


@app.get("/outline/{outline_id}/network", summary="查看人物关系网（未构建则自动构建）")
def get_network(outline_id: int) -> dict:
    if outline_id not in _store:
        raise HTTPException(status_code=404, detail=f"大纲 {outline_id} 不存在")
    return _get_network(outline_id, _store[outline_id])


@app.get("/outlines", response_model=list[OutlineSummary], summary="小说列表")
def list_outlines() -> list[OutlineSummary]:
    return [
        OutlineSummary(
            id=oid,
            title=o.title,
            genre=o.genre,
            chapter_count=len(o.chapters),
            written_chars=sum(ch.char_count for (i, _), ch in _chapters.items() if i == oid),
        )
        for oid, o in sorted(_store.items())
    ]


@app.delete("/outline/{outline_id}", summary="删除小说（正文、人物关系网与向量记忆一并删除）")
def delete_outline(outline_id: int) -> dict:
    if outline_id not in _store:
        raise HTTPException(status_code=404, detail=f"大纲 {outline_id} 不存在")
    del _store[outline_id]
    _networks.pop(outline_id, None)
    novel_key = _novel_keys.pop(outline_id)
    for key in [k for k in _chapters if k[0] == outline_id]:
        del _chapters[key]
    _save()
    delete_novel(novel_key)
    return {"deleted": outline_id}


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")
