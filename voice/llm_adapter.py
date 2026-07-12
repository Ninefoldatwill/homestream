"""
HomeStreamLLM - 自定义 LLM 适配器

将 LiveKit Agents 的 LLM 请求路由到 HomeStream 三层模型路由器 (ModelRouter)。
这是 VoiceBridge 与三层路由 (L1本地/L2免费API/L3付费API) 的核心接入点。

设计理念:
  - 不依赖任何外部 LLM 账号 (铸钥匠"自托底")
  - 语音场景默认 SPEED_FIRST 策略 (低延迟优先)
  - 双保障降级: 主线(L1+L2)失败 → 复线(L3) 自动切换
  - 可独立测试 (不依赖 LiveKit SDK)

用法:
  from voice.llm_adapter import HomeStreamLLM
  llm = HomeStreamLLM()
  response_text = await llm.route("你好")
"""

from __future__ import annotations

import logging
from typing import Any, AsyncIterator

logger = logging.getLogger("homestream.voice.llm")

# --- HomeStream 三层路由导入 ---
# ModelRouter 是项目的核心路由器, chat()/chat_simple() 是公开 API
try:
    from model_router import ModelRouter, RouterStrategy
    from providers.base_provider import ChatMessage, ChatResponse

    _ROUTER_AVAILABLE = True
except ImportError:
    _ROUTER_AVAILABLE = False
    ModelRouter = None  # type: ignore
    RouterStrategy = None  # type: ignore
    ChatMessage = None  # type: ignore
    ChatResponse = None  # type: ignore

# --- LiveKit LLM 接口 (可选, 仅在 Agent 运行时需要) ---
try:
    from livekit.agents import llm as lk_llm

    _LIVEKIT_AVAILABLE = True
except ImportError:
    _LIVEKIT_AVAILABLE = False
    lk_llm = None  # type: ignore


# 策略映射
_STRATEGY_MAP = {
    "COST_FIRST": "COST_FIRST",
    "QUALITY_FIRST": "QUALITY_FIRST",
    "SPEED_FIRST": "SPEED_FIRST",
    "TIER_SPECIFIED": "TIER_SPECIFIED",
    "SMART": "SMART",
}


class HomeStreamLLM:
    """
    自定义 LLM 适配器, 包装 HomeStream ModelRouter 三层路由。

    核心方法:
      - route(prompt, system, max_tokens) → str: 简化入口, 返回纯文本
      - route_messages(messages) → ChatResponse: 完整入口, 返回路由响应
      - llm_node(chat_ctx, tools, model_settings) → AsyncIterator: LiveKit 节点覆写
    """

    def __init__(
        self,
        router: Any | None = None,
        strategy: str = "SPEED_FIRST",
    ):
        """
        Args:
            router: 已初始化的 ModelRouter 实例 (None 则自动从 .env 初始化)
            strategy: 路由策略 (SPEED_FIRST 适合语音低延迟)
        """
        self._router = router
        self._strategy_name = strategy
        self._initialized = False

    def _ensure_router(self) -> Any:
        """延迟初始化 ModelRouter (首次调用时)"""
        if self._router is not None and self._initialized:
            return self._router

        if not _ROUTER_AVAILABLE:
            raise RuntimeError(
                "ModelRouter 不可用 - 请在 HomeStream 项目根目录运行, "
                "或确保 model_router.py 和 providers/ 在 Python 路径中"
            )

        strategy_enum = getattr(RouterStrategy, self._strategy_name, None)
        if strategy_enum is None:
            logger.warning("未知策略 %s, 降级为 SPEED_FIRST", self._strategy_name)
            strategy_enum = RouterStrategy.SPEED_FIRST

        self._router = ModelRouter(strategy=strategy_enum)
        if not getattr(self._router, "_initialized", False):
            self._router.auto_init_from_env()
        self._initialized = True

        available = self._router.get_available_tiers()
        logger.info(
            "HomeStreamLLM 初始化完成, 策略=%s, 可用层级=%s", self._strategy_name, available
        )
        return self._router

    # ========== 核心路由方法 ==========

    async def route(
        self,
        prompt: str,
        system: str = "",
        max_tokens: int = 512,
    ) -> str:
        """
        简化路由入口 - 传字符串, 返回纯文本。

        这是 VoiceBridge 最常用的调用方式:
          1. STT 转写出用户文本
          2. 调用 route() 走三层路由获取回复
          3. TTS 合成语音回复
        """
        router = self._ensure_router()
        return await router.chat_simple(
            prompt=prompt,
            system=system,
            max_tokens=max_tokens,
        )

    async def route_messages(
        self,
        messages: list[dict[str, str]],
        max_tokens: int | None = None,
        temperature: float | None = None,
        prefer_tier: str | None = None,
    ) -> Any:
        """
        完整路由入口 - 传消息列表, 返回 ChatResponse。

        Args:
            messages: [{"role": "user"/"assistant"/"system", "content": "..."}]
            prefer_tier: "L1"/"L2"/"L3" 指定层级 (None = 自动路由)
        """
        router = self._ensure_router()

        # 转换为 HomeStream ChatMessage
        hs_messages = [ChatMessage(role=m["role"], content=m["content"]) for m in messages]

        # 指定层级
        tier = None
        if prefer_tier:
            from providers.base_provider import ProviderTier

            tier = getattr(ProviderTier, prefer_tier, None)

        return await router.chat(
            messages=hs_messages,
            max_tokens=max_tokens,
            temperature=temperature,
            prefer_tier=tier,
        )

    # ========== LiveKit Agent 节点覆写 ==========

    async def llm_node(
        self,
        chat_ctx: Any,
        tools: list | None = None,
        model_settings: Any | None = None,
    ) -> AsyncIterator[Any]:
        """
        LiveKit Agent llm_node 覆写 - 将对话路由到三层 ModelRouter。

        用法 (在 Agent 子类中):
            class VoiceBridgeAgent(Agent):
                def __init__(self):
                    super().__init__(instructions="...")
                    self._llm = HomeStreamLLM()

                async def llm_node(self, chat_ctx, tools, model_settings):
                    async for chunk in self._llm.llm_node(chat_ctx, tools, model_settings):
                        yield chunk
        """
        # 转换 LiveKit ChatContext → HomeStream ChatMessage 列表
        messages = self._convert_chat_ctx(chat_ctx)

        logger.debug("llm_node 收到 %d 条消息, 路由中...", len(messages))

        # 调用三层路由 (非流式, 一次性返回)
        response = await self.route_messages(messages=messages, max_tokens=512)

        content = getattr(response, "content", str(response))
        tier = getattr(response, "tier", "?")
        model = getattr(response, "model", "?")
        latency = getattr(response, "latency_ms", 0)

        logger.info("路由完成: tier=%s model=%s latency=%sms", tier, model, latency)

        # 以 ChatChunk 形式 yield (兼容 LiveKit 管线)
        if _LIVEKIT_AVAILABLE:
            try:
                # 尝试用完整参数 (新版 LiveKit SDK 需要 id)
                import uuid

                yield lk_llm.ChatChunk(id=str(uuid.uuid4()), content=content)
            except (TypeError, ValueError):
                # 降级: 老版本只需 content
                yield lk_llm.ChatChunk(content=content)
        else:
            # 无 LiveKit SDK 时, 直接 yield 文本 (用于独立测试)
            yield content

    def _convert_chat_ctx(self, chat_ctx: Any) -> list[dict[str, str]]:
        """将 LiveKit ChatContext 转换为 HomeStream 消息列表"""
        messages: list[dict[str, str]] = []

        if chat_ctx is None:
            return messages

        # LiveKit ChatContext 有 messages 属性
        ctx_messages = getattr(chat_ctx, "messages", None)
        if ctx_messages is None:
            # 可能是列表直接传入
            if isinstance(chat_ctx, list):
                ctx_messages = chat_ctx
            else:
                logger.warning("无法解析 chat_ctx 类型: %s", type(chat_ctx))
                return messages

        for msg in ctx_messages:
            # dict 格式: {"role": "user", "content": "..."}
            if isinstance(msg, dict):
                role = msg.get("role", "user")
                content = msg.get("content", "")
            else:
                # 对象格式: msg.role, msg.content
                role = getattr(msg, "role", "user")
                content = getattr(msg, "content", "")

            # role 可能是枚举或 "ChatRole.USER" 字符串, 统一处理
            if not isinstance(role, str):
                role = str(role)
            # 去掉枚举前缀 (如 "ChatRole.USER" → "USER")
            if "." in role:
                role = role.split(".")[-1]
            role = role.lower().strip()

            # 映射到 HomeStream 支持的 role
            if role in ("user", "human"):
                role = "user"
            elif role in ("assistant", "ai"):
                role = "assistant"
            elif role in ("system",):
                role = "system"

            # content 可能是列表 (多模态), 取文本部分
            if isinstance(content, list):
                text_parts = []
                for part in content:
                    if isinstance(part, str):
                        text_parts.append(part)
                    elif isinstance(part, dict):
                        text_parts.append(part.get("text", ""))
                content = " ".join(text_parts)
            elif not isinstance(content, str):
                content = str(content)

            messages.append({"role": role, "content": content})

        return messages

    # ========== 状态查询 ==========

    async def health_check(self) -> dict[str, Any]:
        """检查三层路由各 Provider 健康状态"""
        if not self._initialized:
            self._ensure_router()
        if self._router is None:
            return {"status": "not_initialized"}
        try:
            return await self._router.health_check_all()
        except Exception as e:
            return {"status": "error", "error": str(e)}

    def get_status(self) -> dict[str, Any]:
        """获取路由器状态"""
        if not self._initialized or self._router is None:
            return {"status": "not_initialized", "strategy": self._strategy_name}
        return self._router.get_status()
