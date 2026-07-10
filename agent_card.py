"""
agent_card.py — A2A AgentCard 自动发现模块
=============================================
对齐 A2A 协议 v1.1，生成标准的 AgentCard JSON，
支持 JWS 签名和 .well-known/agent-card.json 发现。

九重生态 · 澜舟开发 · 2026-07-02
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, Field, field_validator

# ─── 数据模型 ──────────────────────────────────────────────────


class AgentInterface(BaseModel):
    """Agent 通信端点"""

    url: str
    protocolBinding: str = "a2a/v1"
    authentication: dict[str, Any] | None = None


class AgentCapabilities(BaseModel):
    """Agent 能力声明"""

    streaming: bool = False
    pushNotifications: bool = False
    stateTransitionHistory: bool = False
    extendedAgentCard: bool = False


class AgentSkill(BaseModel):
    """Agent 暴露的技能"""

    id: str
    name: str
    description: str
    tags: list[str] = Field(default_factory=list)
    examples: list[str] = Field(default_factory=list)
    inputModes: list[str] = Field(default_factory=lambda: ["text/plain"])
    outputModes: list[str] = Field(default_factory=lambda: ["text/plain"])


class AgentProvider(BaseModel):
    """Agent 提供商信息"""

    name: str
    url: str | None = None


class AgentCard(BaseModel):
    """A2A 标准 AgentCard（13核心字段）"""

    protocolVersions: list[str] = Field(default_factory=lambda: ["v1"])
    name: str
    description: str
    supportedInterfaces: list[AgentInterface]
    version: str
    capabilities: AgentCapabilities
    skills: list[AgentSkill]
    defaultInputModes: list[str] = Field(default_factory=lambda: ["text/plain"])
    defaultOutputModes: list[str] = Field(default_factory=lambda: ["text/plain"])
    provider: AgentProvider | None = None
    securitySchemes: dict[str, Any] | None = None
    security: list[dict[str, Any]] | None = None
    signatures: list[dict[str, Any]] | None = None

    @field_validator("protocolVersions")
    @classmethod
    def _check_versions(cls, v: list[str]) -> list[str]:
        if not v:
            raise ValueError("protocolVersions 不能为空")
        return v


# ─── 自动生成 AgentCard ────────────────────────────────────────


@dataclass
class AgentCardBuilder:
    """从 OpenBridge 路由/模块元数据自动生成 AgentCard"""

    server_version: str = "8.0.0"
    base_url: str = "http://localhost:3458"
    agent_name: str = "OpenBridge"
    description: str = (
        "九重生态 · AI 协作操作系统 —— 支持 Agent 群聊、三层模型路由、"
        "Skill 调度、EventStream 因果链与书阁知识库。"
    )
    provider_name: str = "九重工作室 JiuChong Studio"
    provider_url: str | None = "https://github.com/Ninefoldatwill/OpenBridge"

    def build(
        self,
        routes: list[dict[str, Any]] | None = None,
        skills: list[AgentSkill] | None = None,
    ) -> AgentCard:
        """构建 AgentCard"""
        if skills is None:
            skills = self._default_skills()

        return AgentCard(
            protocolVersions=["v1"],
            name=self.agent_name,
            description=self.description,
            supportedInterfaces=[
                AgentInterface(
                    url=f"{self.base_url}/api/v8/a2a",
                    protocolBinding="a2a/v1",
                )
            ],
            version=self.server_version,
            capabilities=AgentCapabilities(
                streaming=True,
                pushNotifications=True,
                stateTransitionHistory=True,
                extendedAgentCard=False,
            ),
            skills=skills,
            defaultInputModes=["text/plain", "application/json"],
            defaultOutputModes=["text/plain", "application/json"],
            provider=AgentProvider(name=self.provider_name, url=self.provider_url),
            securitySchemes={
                "ApiKeyAuth": {"type": "apiKey", "in": "header", "name": "X-Agent-Token"}
            },
            security=[{"ApiKeyAuth": []}],
        )

    def _default_skills(self) -> list[AgentSkill]:
        """OpenBridge 默认技能清单"""
        return [
            AgentSkill(
                id="chat",
                name="Agent 群聊",
                description="多 Agent 在频道中进行 ICP v1.1 协议通讯",
                tags=["chat", "icp", "collaboration"],
                examples=["@澜舟 帮我检查一下今天的任务进度"],
            ),
            AgentSkill(
                id="research",
                name="深度调研",
                description="联网搜索并整理多维度技术报告",
                tags=["research", "web", "analysis"],
                examples=["调研 AI Agent 记忆系统最新进展"],
            ),
            AgentSkill(
                id="knowledge",
                name="书阁知识检索",
                description="检索九重书阁公开的 L1 知识库",
                tags=["knowledge", "rag", "bookhouse"],
                examples=["书阁里有没有关于 ICP 协议的资料？"],
            ),
            AgentSkill(
                id="memory",
                name="记忆演化",
                description="长期记忆的遗忘、合并、重构与混合召回",
                tags=["memory", "evolution", "recall"],
                examples=["总结一下我过去一周关注的技术方向"],
            ),
            AgentSkill(
                id="workflow",
                name="工作流执行",
                description="按 DAG 拓扑执行可视化工作流 DSL",
                tags=["workflow", "dag", "automation"],
                examples=["执行一个数据清洗工作流"],
            ),
        ]


# ─── JWS 签名（简化版，使用 Ed25519）────────────────────────────


class AgentCardSigner:
    """
    使用 Ed25519 对 AgentCard 进行 JWS 签名。
    依赖 PyNaCl（可选），未安装时跳过签名。
    """

    def __init__(self, private_key_b64: str | None = None):
        self._has_nacl = False
        try:
            import nacl.encoding
            import nacl.signing

            self._has_nacl = True
            self._nacl_signing = nacl.signing
            self._nacl_encoding = nacl.encoding
        except ImportError:
            pass

        self._private_key = None
        if private_key_b64 and self._has_nacl:
            seed = self._nacl_encoding.Base64Encoder.decode(private_key_b64)
            self._private_key = self._nacl_signing.SigningKey(seed)

    def sign(self, card: AgentCard) -> str | None:
        """返回 Base64Url 编码的 JWS，未安装 PyNaCl 返回 None"""
        if not self._has_nacl or self._private_key is None:
            return None

        header = {"alg": "EdDSA", "crv": "Ed25519", "typ": "JWT"}
        payload = json.loads(card.model_dump_json())
        payload["iat"] = int(time.time())

        header_b64 = self._b64url(json.dumps(header, separators=(",", ":")).encode())
        payload_b64 = self._b64url(json.dumps(payload, separators=(",", ":")).encode())
        signing_input = f"{header_b64}.{payload_b64}".encode()

        signed = self._private_key.sign(signing_input)
        signature_b64 = self._b64url(signed.signature)

        return f"{header_b64}.{payload_b64}.{signature_b64}"

    @staticmethod
    def _b64url(data: bytes) -> str:
        import base64

        return base64.urlsafe_b64encode(data).decode().rstrip("=")

    def verify(self, jws: str, public_key_b64: str | None = None) -> bool:
        """验证 JWS 签名"""
        if not self._has_nacl:
            return False
        if public_key_b64 is None and self._private_key is None:
            return False

        try:
            parts = jws.split(".")
            if len(parts) != 3:
                return False

            if public_key_b64:
                verify_key = self._nacl_signing.VerifyKey(
                    public_key_b64, encoder=self._nacl_encoding.Base64Encoder
                )
            else:
                verify_key = self._private_key.verify_key

            signing_input = f"{parts[0]}.{parts[1]}".encode()
            import base64

            signature = base64.urlsafe_b64decode(parts[2] + "==")
            verify_key.verify(signing_input, signature)
            return True
        except Exception:
            return False


# ─── 便捷函数 ──────────────────────────────────────────────────


def generate_agent_card(
    base_url: str = "http://localhost:3458",
    server_version: str = "8.0.0",
    routes: list[dict[str, Any]] | None = None,
    skills: list[AgentSkill] | None = None,
) -> AgentCard:
    """便捷函数：生成默认 AgentCard"""
    return AgentCardBuilder(
        base_url=base_url,
        server_version=server_version,
    ).build(routes=routes, skills=skills)


def generate_well_known_card(base_url: str = "http://localhost:3458") -> str:
    """生成 .well-known/agent-card.json 的内容"""
    card = generate_agent_card(base_url=base_url)
    return card.model_dump_json(indent=2)


# ─── CLI ───────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys

    base_url = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:3458"
    print(generate_well_known_card(base_url))
