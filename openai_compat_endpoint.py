"""
OpenAI 兼容 API 端点

让外部应用（LibreChat / OpenWebUI / ChatBox / 任何 OpenAI 兼容客户端）
通过标准 OpenAI API 格式接入 HomeStream 的三层模型路由。

三维立体化架构中的"交互维度"：
  - 模型维度 (OllamaProvider)  → AI 能"想"（生成回答）
  - 执行维度 (ToolBridge)      → AI 能"做"（执行操作）
  - 交互维度 (OpenAI兼容API)   → 外部能"接入"  ← 本模块

支持的端点：
  POST /v1/chat/completions  — 聊天补全（兼容 OpenAI API）
  GET  /v1/models            — 模型列表
  GET  /v1/health            — 健康检查

使用方式：
  from openai_compat_endpoint import create_openai_router
  app.include_router(create_openai_router(model_router))

认证：
  可选。设置环境变量 OPENAI_COMPAT_API_KEY 后，
  请求需携带 Authorization: Bearer <key> 头。
  未设置则无需认证（开源默认无认证）。

兼容性：
  - 请求格式兼容 OpenAI Chat Completions API
  - 响应格式兼容 OpenAI Chat Completions API
  - 支持 streaming（SSE 格式）
  - 支持 model 参数指定 Provider / Tier / 模型名
  - 支持 temperature / max_tokens 透传
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
import uuid
from typing import Any, Optional

from fastapi import APIRouter, Header, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from model_router import ModelRouter
from providers.base_provider import (
    ChatMessage,
    ChatResponse,
    ProviderError,
    ProviderTier,
)

logger = logging.getLogger(__name__)


# ==================== 请求/响应模型 ====================


class ChatCompletionMessage(BaseModel):
    """OpenAI 兼容消息格式"""

    role: str  # system / user / assistant
    content: str = ""


class ChatCompletionRequest(BaseModel):
    """OpenAI 兼容聊天补全请求"""

    model: str = ""  # 可选：指定 Provider / Tier / 模型名
    messages: list[ChatCompletionMessage]
    temperature: float | None = None
    max_tokens: int | None = None
    stream: bool = False
    top_p: float | None = None
    frequency_penalty: float | None = None
    presence_penalty: float | None = None
    stop: str | list[str] | None = None

    model_config = {"extra": "allow"}  # 允许额外字段（忽略不支持的参数）


# ==================== 辅助函数 ====================


def _check_auth(authorization: str | None) -> None:
    """检查 API Key 认证（可选）

    如果设置了 OPENAI_COMPAT_API_KEY 环境变量，
    则要求请求携带正确的 Authorization 头。
    """
    expected_key = os.getenv("OPENAI_COMPAT_API_KEY", "")
    if not expected_key:
        return  # 未设置 key，跳过认证

    if not authorization:
        raise HTTPException(status_code=401, detail="缺少 Authorization 头")

    # 解析 Bearer token
    parts = authorization.split(" ", 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        raise HTTPException(status_code=401, detail="Authorization 格式错误，应为: Bearer <key>")

    token = parts[1]
    if token != expected_key:
        raise HTTPException(status_code=401, detail="API Key 无效")


def _resolve_tier(model_name: str) -> ProviderTier | None:
    """将模型名解析为 Tier"""
    tier_map = {
        "l1": ProviderTier.L1,
        "l2": ProviderTier.L2,
        "l3": ProviderTier.L3,
    }
    return tier_map.get(model_name.lower())


async def _call_model_router(
    model_router: ModelRouter,
    request: ChatCompletionRequest,
) -> ChatResponse:
    """调用 ModelRouter，根据 model 参数路由

    model 参数支持：
      1. "" 或 "auto" → 默认路由
      2. "L1" / "L2" / "L3" → 指定层级
      3. Provider name（如 "ollama_qwen2_5_3b"）→ 指定 Provider
      4. 模型名称（如 "qwen2.5:3b"）→ 模糊匹配
    """
    messages = [ChatMessage(role=m.role, content=m.content) for m in request.messages]
    model_name = request.model.strip()

    if not model_name or model_name.lower() == "auto":
        # 默认路由
        return await model_router.chat(
            messages,
            max_tokens=request.max_tokens,
            temperature=request.temperature,
        )

    # 尝试按 Tier 路由
    tier = _resolve_tier(model_name)
    if tier:
        return await model_router.chat(
            messages,
            max_tokens=request.max_tokens,
            temperature=request.temperature,
            prefer_tier=tier,
        )

    # 尝试按 Provider name 匹配
    provider = model_router.registry.get(model_name)
    if provider:
        try:
            return await provider.chat(
                messages,
                max_tokens=request.max_tokens,
                temperature=request.temperature,
            )
        except ProviderError as e:
            raise HTTPException(status_code=502, detail=f"Provider错误: {e}") from e

    # 尝试按模型名称模糊匹配
    for p in model_router.registry.get_available():
        if p.config.model_name == model_name or p.config.model_name.startswith(model_name):
            try:
                return await p.chat(
                    messages,
                    max_tokens=request.max_tokens,
                    temperature=request.temperature,
                )
            except ProviderError as e:
                raise HTTPException(status_code=502, detail=f"Provider错误: {e}") from e

    # 找不到匹配的模型，使用默认路由
    logger.info(f"模型 '{model_name}' 未找到匹配的Provider，使用默认路由")
    return await model_router.chat(
        messages,
        max_tokens=request.max_tokens,
        temperature=request.temperature,
    )


def _make_completion_response(
    response: ChatResponse,
    model_name: str,
) -> dict[str, Any]:
    """构造 OpenAI 兼容的非流式响应"""
    return {
        "id": f"chatcmpl-{uuid.uuid4().hex[:12]}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model_name,
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": response.content,
                },
                "finish_reason": "stop",
            }
        ],
        "usage": {
            "prompt_tokens": response.tokens_in,
            "completion_tokens": response.tokens_out,
            "total_tokens": response.tokens_in + response.tokens_out,
        },
        # HomeStream 扩展字段
        "provider": response.provider,
        "tier": response.tier.value,
        "latency_ms": round(response.latency_ms, 1),
        "cost_estimate": response.cost_estimate,
    }


async def _stream_completion(
    response: ChatResponse,
    model_name: str,
) -> Any:
    """生成 OpenAI 兼容的 SSE 流式响应

    由于当前 Provider 不支持真正的流式输出，
    这里将完整响应分成多个 chunk 模拟流式效果。
    后续版本可通过 Provider 层面的流式支持实现真正流式。
    """
    response_id = f"chatcmpl-{uuid.uuid4().hex[:12]}"
    created = int(time.time())

    def chunk(delta: dict, finish_reason: str | None = None) -> str:
        """构造一个 SSE chunk"""
        data = {
            "id": response_id,
            "object": "chat.completion.chunk",
            "created": created,
            "model": model_name,
            "choices": [
                {
                    "index": 0,
                    "delta": delta,
                    "finish_reason": finish_reason,
                }
            ],
        }
        return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"

    # 1. 第一个 chunk：role
    yield chunk({"role": "assistant"})

    # 2. 内容 chunks（按段落/句子分割，模拟流式效果）
    content = response.content
    chunk_size = 20  # 每 20 字符一个 chunk
    for i in range(0, len(content), chunk_size):
        text = content[i : i + chunk_size]
        yield chunk({"content": text})
        await asyncio.sleep(0.02)  # 模拟生成延迟

    # 3. 最后一个 chunk：finish_reason
    yield chunk({}, finish_reason="stop")

    # 4. 结束标记
    yield "data: [DONE]\n\n"


# ==================== Router 创建 ====================


def create_openai_router(model_router: ModelRouter) -> APIRouter:
    """创建 OpenAI 兼容 API Router

    Args:
        model_router: ModelRouter 实例

    Returns:
        APIRouter: 可被 FastAPI app 挂载的路由器

    使用方式：
        from openai_compat_endpoint import create_openai_router
        app.include_router(create_openai_router(model_router))
    """
    router = APIRouter(prefix="/v1", tags=["OpenAI Compatible API"])

    @router.post("/chat/completions")
    async def chat_completions(
        request: ChatCompletionRequest,
        authorization: str | None = Header(None),
    ):
        """聊天补全 — 兼容 OpenAI POST /v1/chat/completions

        支持：
          - 非流式响应（stream=false，默认）
          - 流式响应（stream=true，SSE 格式）
          - model 参数指定 Provider / Tier / 模型名
          - temperature / max_tokens 透传
        """
        _check_auth(authorization)

        # 确保 ModelRouter 已初始化
        if not model_router._initialized:
            model_router.auto_init_from_env()

        # 调用 ModelRouter
        try:
            response = await _call_model_router(model_router, request)
        except HTTPException:
            raise
        except ProviderError as e:
            raise HTTPException(status_code=502, detail=f"所有Provider均失败: {e}") from e
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"内部错误: {e}") from e

        model_name = request.model or response.model or "homestream"

        # 流式响应
        if request.stream:
            return StreamingResponse(
                _stream_completion(response, model_name),
                media_type="text/event-stream",
                headers={
                    "Cache-Control": "no-cache",
                    "Connection": "keep-alive",
                    "X-Accel-Buffering": "no",  # 禁用 Nginx 缓冲
                },
            )

        # 非流式响应
        return _make_completion_response(response, model_name)

    @router.get("/models")
    async def list_models(authorization: str | None = Header(None)):
        """模型列表 — 兼容 OpenAI GET /v1/models

        返回所有已注册的 Provider 对应的模型。
        """
        _check_auth(authorization)

        if not model_router._initialized:
            model_router.auto_init_from_env()

        providers = model_router.registry.get_all()
        models = []
        for name, provider in providers.items():
            models.append(
                {
                    "id": name,  # Provider name 作为 model id
                    "object": "model",
                    "created": int(time.time()),
                    "owned_by": "homestream",
                    # HomeStream 扩展字段
                    "display_name": provider.config.display_name,
                    "tier": provider.config.tier.value,
                    "type": provider.config.provider_type.value,
                    "model_name": provider.config.model_name,
                    "status": provider.status.value,
                }
            )

        return {"object": "list", "data": models}

    @router.get("/health")
    async def health():
        """健康检查 — HomeStream 扩展端点

        返回 ModelRouter 和所有 Provider 的健康状态。
        """
        if not model_router._initialized:
            model_router.auto_init_from_env()

        health_status = await model_router.health_check_all()

        all_healthy = all(health_status.values()) if health_status else False

        return {
            "status": "healthy" if all_healthy else "degraded",
            "timestamp": int(time.time()),
            "providers": health_status,
            "total_providers": len(health_status),
            "healthy_providers": sum(1 for v in health_status.values() if v),
        }

    @router.get("/")
    async def api_info():
        """API 信息 — HomeStream 扩展端点

        返回 API 基本信息，方便客户端自动发现。
        """
        return {
            "name": "HomeStream OpenAI Compatible API",
            "version": "5.1.0",
            "description": "免费托底每个人通往AI世界的第一扇门",
            "endpoints": [
                "POST /v1/chat/completions",
                "GET /v1/models",
                "GET /v1/health",
            ],
            "auth_required": bool(os.getenv("OPENAI_COMPAT_API_KEY", "")),
        }

    return router


# ==================== 独立运行入口 ====================


def create_standalone_app(
    model_router: ModelRouter | None = None,
    host: str = "0.0.0.0",
    port: int = 8080,
) -> Any:
    """创建独立的 OpenAI 兼容 API 服务

    可以不依赖 bridge_v7_server.py 独立运行，
    只提供 OpenAI 兼容 API 端点。

    Args:
        model_router: ModelRouter 实例（默认自动创建）
        host: 监听地址
        port: 监听端口

    Returns:
        FastAPI app 实例
    """
    from fastapi import FastAPI
    from fastapi.middleware.cors import CORSMiddleware

    if model_router is None:
        model_router = ModelRouter()

    app = FastAPI(
        title="HomeStream OpenAI Compatible API",
        description="免费托底每个人通往AI世界的第一扇门",
        version="5.1.0",
    )

    # CORS 支持（让浏览器客户端能直接调用）
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # 挂载 OpenAI 兼容路由
    app.include_router(create_openai_router(model_router))

    return app
