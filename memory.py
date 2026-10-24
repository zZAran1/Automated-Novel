"""章节向量记忆：保存已写章节，供后续章节生成时召回相关情节。"""

from pathlib import Path

import chromadb

# 用绝对路径：换工作目录启动也不会把向量库建到别处
DB_PATH = str(Path(__file__).resolve().parent / "db")
COLLECTION = "chapters"
CHUNK_SIZE = 500  # 每个文本块最大字符数

_client = None


def _collection():
    global _client
    if _client is None:
        _client = chromadb.PersistentClient(path=DB_PATH)
    return _client.get_or_create_collection(COLLECTION, metadata={"hnsw:space": "cosine"})


def _chunk(text: str) -> list[str]:
    """按段落累积切块，每块不超过 CHUNK_SIZE 字符；超长段落按 CHUNK_SIZE 强制切分。"""
    chunks: list[str] = []
    buf = ""
    for para in text.split("\n"):
        para = para.strip()
        if not para:
            continue
        for i in range(0, len(para), CHUNK_SIZE):  # 单段超长时硬切，避免出现超大块
            piece = para[i : i + CHUNK_SIZE]
            if buf and len(buf) + len(piece) > CHUNK_SIZE:
                chunks.append(buf)
                buf = piece
            else:
                buf = f"{buf}\n{piece}" if buf else piece
    if buf:
        chunks.append(buf)
    return chunks


def save_chapter(novel_id: str, chapter_no: int, title: str, content: str) -> int:
    """把写好的一章切块存入向量库，返回存入的块数。

    同一章重复保存会先清掉旧数据，避免重复召回。
    """
    col = _collection()
    col.delete(
        where={
            "$and": [
                {"novel_id": {"$eq": novel_id}},
                {"chapter_no": {"$eq": chapter_no}},
            ]
        }
    )
    chunks = _chunk(content)
    col.add(
        ids=[f"{novel_id}-{chapter_no}-{i}" for i in range(len(chunks))],
        documents=chunks,
        metadatas=[
            {"novel_id": novel_id, "chapter_no": chapter_no, "title": title} for _ in chunks
        ],
    )
    return len(chunks)


def recall(query: str, novel_id: str, before_chapter: int, k: int = 4) -> list[str]:
    """召回该小说中 chapter_no < before_chapter 的相关片段（按相似度排序）。"""
    col = _collection()
    if col.count() == 0:
        return []
    res = col.query(
        query_texts=[query],
        n_results=k,
        where={
            "$and": [
                {"novel_id": {"$eq": novel_id}},
                {"chapter_no": {"$lt": before_chapter}},
            ]
        },
    )
    return res["documents"][0]


def load_prev_chapters(novel_id: str, before_chapter: int) -> list[tuple[int, str]]:
    """取出该小说 chapter_no < before_chapter 的章节全文（切块按序重组），供相似度检测比对。"""
    col = _collection()
    if col.count() == 0:
        return []
    res = col.get(
        where={
            "$and": [
                {"novel_id": {"$eq": novel_id}},
                {"chapter_no": {"$lt": before_chapter}},
            ]
        },
        include=["documents", "metadatas"],
    )
    # 切块 id 形如 {novel_id}-{chapter_no}-{块序号}，按块序号重组保证章内顺序
    grouped: dict[int, list[tuple[int, str]]] = {}
    for doc, meta, id_ in zip(res["documents"], res["metadatas"], res["ids"]):
        grouped.setdefault(meta["chapter_no"], []).append((int(id_.rsplit("-", 1)[-1]), doc))
    return [
        (no, "\n".join(d for _, d in sorted(parts)))
        for no, parts in sorted(grouped.items())
    ]


def delete_chapter(novel_id: str, chapter_no: int) -> None:
    """删除该小说某一章的全部向量块（删除某章正文时调用）。"""
    _collection().delete(
        where={
            "$and": [
                {"novel_id": {"$eq": novel_id}},
                {"chapter_no": {"$eq": chapter_no}},
            ]
        }
    )


def delete_novel(novel_id: str) -> None:
    """删除该小说的全部向量块（删除小说时调用）。"""
    _collection().delete(where={"novel_id": {"$eq": novel_id}})
