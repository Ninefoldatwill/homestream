"""
书阁代理客户端 —— V8 ←→ 九重书阁(3460) 知识桥梁
==================================================
双模式运行：
- team/private (默认): httpx异步调用3460书阁API，实时50本书·9阁·120标签
- opensource: 静态JSON快照读取，15本公开知识·物理隔离·零网络依赖

设计原则：
- 物理隔离：开源版不连3460，核心知识存于九重本地·无API可连接
- 零依赖降级：开源模式不依赖书阁服务/Runtime/任何外部端口
- 透明切换：环境变量 OPENBRIDGE_MODE 控制，向外暴露相同API签名
"""

import json
import os
from typing import Any, Dict, List, Optional

import httpx
import structlog

logger = structlog.get_logger("bookhouse.client")

# ─── 运行模式 ────────────────────────────────────────────
# 环境变量 OPENBRIDGE_MODE:
#   "opensource" → 静态快照·物理隔离·15本公开知识
#   "team" (默认) → 实时3460书阁API·50本完整知识
#   "private"     → 同team·保留扩展空间
OPENBRIDGE_MODE = os.getenv("OPENBRIDGE_MODE", "team").lower()

# 书阁服务地址（team/private模式）
BOOKHOUSE_BASE_URL = "http://127.0.0.1:3460"
TIMEOUT = 5.0  # 秒

# 开源模式静态知识库
PUBLIC_KB_PATH = os.path.join(os.path.dirname(__file__), "open_knowledge", "public_knowledge.json")


# ─── 静态知识库缓存 ──────────────────────────────────────

_public_cache: dict | None = None


def _load_public_cache() -> dict:
    """懒加载开源知识库快照"""
    global _public_cache
    if _public_cache is None:
        try:
            with open(PUBLIC_KB_PATH, encoding="utf-8") as f:
                _public_cache = json.load(f)
            logger.info(
                "bookhouse_opensource_loaded", books=_public_cache["_meta"].get("total_books", 0)
            )
        except FileNotFoundError:
            logger.warning("bookhouse_opensource_missing", path=PUBLIC_KB_PATH)
            _public_cache = {
                "_meta": {"total_books": 0, "note": "知识库文件未找到"},
                "stats": {"total_books": 0, "total_tags": 0, "buildings": []},
                "tags": [],
                "books": [],
            }
    return _public_cache


def is_opensource() -> bool:
    return OPENBRIDGE_MODE == "opensource"


# ─── 内部工具 ────────────────────────────────────────────


async def _fetch(
    endpoint: str, method: str = "GET", json_body: dict = None, params: dict = None
) -> dict:
    """通用异步请求，带超时和降级（仅team/private模式）"""
    url = f"{BOOKHOUSE_BASE_URL}{endpoint}"
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            if method == "GET":
                resp = await client.get(url, params=params)
            elif method == "POST":
                resp = await client.post(url, json=json_body, params=params)
            else:
                return {"status": "error", "message": f"Unsupported method: {method}"}
            resp.raise_for_status()
            return resp.json()
    except httpx.TimeoutException:
        logger.warning("bookhouse_timeout", endpoint=endpoint)
        return {"status": "degraded", "message": f"书阁超时({TIMEOUT}s)", "results": []}
    except httpx.ConnectError:
        logger.warning("bookhouse_unreachable", endpoint=endpoint)
        return {"status": "degraded", "message": "书阁服务不可用", "results": []}
    except Exception as e:
        logger.error("bookhouse_error", endpoint=endpoint, error=str(e))
        return {"status": "error", "message": str(e), "results": []}


def _opensource_search(query: str) -> dict:
    """开源模式：本地内存搜索（简单子串匹配）"""
    cache = _load_public_cache()
    books = cache.get("books", [])
    q = query.lower().strip()
    results = []
    for b in books:
        text = f"{b.get('title', '')} {b.get('summary', '')} {b.get('tags', '')} {b.get('author', '')}".lower()
        if q in text:
            results.append(b)
    return {
        "status": "ok",
        "count": len(results),
        "results": results,
        "_source": "本地静态知识库·物理隔离",
    }


def _opensource_book(book_id: int) -> dict:
    """开源模式：按ID查书"""
    cache = _load_public_cache()
    for b in cache.get("books", []):
        if b.get("id") == book_id:
            return {"status": "ok", "book": b, "_source": "本地静态知识库·物理隔离"}
    return {"status": "not_found", "message": f"公开知识库中未找到ID={book_id}的书籍", "book": None}


def _opensource_tags() -> dict:
    """开源模式：标签列表"""
    cache = _load_public_cache()
    return {
        "status": "ok",
        "count": len(cache.get("tags", [])),
        "tags": cache.get("tags", []),
        "_source": "本地静态知识库·物理隔离",
    }


def _opensource_stats() -> dict:
    """开源模式：统计信息"""
    cache = _load_public_cache()
    stats = cache.get("stats", {})
    return {
        "status": "ok",
        "total_books": stats.get("total_books", 0),
        "total_tags": stats.get("total_tags", 0),
        "buildings": stats.get("buildings", []),
        "_source": "本地静态知识库·物理隔离",
        "success": True,
    }


# ─── 公开API ─────────────────────────────────────────────


async def search(query: str) -> dict:
    """全文搜索

    - 开源模式：本地内存子串匹配（15本公开知识）
    - team/private模式：书阁3460 FTS5全文搜索（50本完整知识）
    """
    if not query or not query.strip():
        return {"status": "error", "message": "搜索关键词不能为空", "results": []}
    if is_opensource():
        return _opensource_search(query)
    return await _fetch("/api/library/search", params={"q": query})


async def get_book(book_id: int) -> dict:
    """获取单本书详情"""
    if is_opensource():
        return _opensource_book(book_id)
    return await _fetch(f"/api/library/book/{book_id}")


async def get_building(name: str) -> dict:
    """获取某阁楼下所有藏书"""
    if is_opensource():
        cache = _load_public_cache()
        books = [b for b in cache.get("books", []) if b.get("building", "") == name]
        return {
            "status": "ok",
            "building": name,
            "count": len(books),
            "results": books,
            "_source": "本地静态知识库·物理隔离",
        }
    return await _fetch(f"/api/library/building/{name}")


async def list_tags() -> dict:
    """列出所有标签"""
    if is_opensource():
        return _opensource_tags()
    return await _fetch("/api/library/tags")


async def get_stats() -> dict:
    """获取书阁统计"""
    if is_opensource():
        return _opensource_stats()
    return await _fetch("/api/library/stats")


async def add_book(
    title: str,
    building: str,
    token: str,
    category: str = "",
    author: str = "",
    source: str = "",
    file_path: str = "",
    summary: str = "",
    tags: str = "",
) -> dict:
    """新增书籍

    开源模式：拒绝写入（只读知识库）
    """
    if is_opensource():
        return {
            "status": "read_only",
            "message": "开源版为只读知识库，不支持写入。如需贡献知识请提交PR。",
            "success": False,
        }
    body = {
        "title": title,
        "building": building,
        "token": token,
        "category": category,
        "author": author,
        "source": source,
        "file_path": file_path,
        "summary": summary,
        "tags": tags,
    }
    return await _fetch("/api/library/add", method="POST", json_body=body)


async def health_check() -> dict:
    """检查知识库健康状态

    - 开源模式：始终返回ok（静态快照无需网络）
    - team/private模式：检查书阁3460连通性
    """
    if is_opensource():
        cache = _load_public_cache()
        return {
            "status": "ok",
            "mode": "opensource",
            "source": "本地静态知识库·物理隔离",
            "books": cache.get("stats", {}).get("total_books", 0),
            "message": "物理隔离模式——核心知识存于九重本地，无API可连接",
        }
    return await _fetch("/health")
