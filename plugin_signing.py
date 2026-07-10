"""
插件签名验证 — Ed25519签名体系 + 可信发布者模式。

融优来源：
  Microsoft Agent Governance Toolkit (Ed25519 + trusted_keys)
  + A2A AgentCard JWS签名 (RFC 7515)

签名体系：
  每个发布者拥有 Ed25519 密钥对
  trusted_keys 字典将作者映射到公钥
  安装时自动验证签名，不匹配拒绝安装

安全原则：
  签名优于信任 · 验证优于假设 · 拒绝优于放过
"""

import base64
import hashlib
import json
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

import structlog

logger = structlog.get_logger("bridge_v7.plugin_signing")


# ============================================================
# 签名结果数据结构
# ============================================================


@dataclass
class SignatureResult:
    """签名操作结果。"""

    is_valid: bool = False
    signer_id: str = ""
    signature_base64: str = ""
    error: str | None = None
    verified_at: float = field(default_factory=time.time)


# ============================================================
# 可信发布者公钥库
# ============================================================

# 可信发布者映射：author → Ed25519公钥(Base64)
# 生产环境应从配置文件加载，此处为示例
TRUSTED_KEYS: dict[str, str] = {
    "jiuchong": "",  # 九重（主发布者·公钥待注册）
    "lanzhou": "",  # 澜舟（开发发布者·公钥待注册）
    "openbridge-official": "",  # OpenBridge官方（公钥待注册）
}


# ============================================================
# Ed25519 签名器（依赖nacl库，fallback为HMAC）
# ============================================================


class PluginSigner:
    """插件签名器 — Ed25519签名 + HMAC fallback。

    优先使用 PyNaCl (nacl) 库进行 Ed25519 签名。
    如不可用，退化为 HMAC-SHA256 签名（安全性降低但仍可用）。
    """

    def __init__(self):
        self._use_ed25519 = False
        self._signing_key = None
        self._verify_key = None
        self._hmac_key = b"openbridge_default_signing_key_change_in_production"

        try:
            from nacl.signing import SigningKey

            self._signing_key = SigningKey.generate()
            self._verify_key = self._signing_key.verify_key
            self._use_ed25519 = True
            logger.info("plugin_signing.ed25519_available")
        except ImportError:
            logger.info("plugin_signing.hmac_fallback", reason="PyNaCl不可用·退化为HMAC-SHA256")

    def sign_manifest(self, manifest_data: dict[str, Any], signer_id: str = "system") -> str:
        """对Manifest数据签名，返回Base64编码签名。"""
        # 确定性JSON序列化（按key排序）
        canonical = json.dumps(manifest_data, sort_keys=True, ensure_ascii=False)
        message = canonical.encode("utf-8")

        if self._use_ed25519 and self._signing_key:
            from nacl.signing import SignedMessage

            signed = self._signing_key.sign(message)
            signature = base64.b64encode(signed.signature).decode("ascii")
            logger.info(
                "plugin_signing.ed25519_signed", signer_id=signer_id, sig_len=len(signature)
            )
            return signature

        else:
            # HMAC-SHA256 fallback
            import hmac

            sig = hmac.new(self._hmac_key, message, hashlib.sha256).digest()
            signature = base64.b64encode(sig).decode("ascii")
            logger.info("plugin_signing.hmac_signed", signer_id=signer_id, sig_len=len(signature))
            return signature

    def verify_signature(
        self, manifest_data: dict[str, Any], signature_base64: str, signer_id: str = ""
    ) -> SignatureResult:
        """验证Manifest签名。"""
        try:
            signature_bytes = base64.b64decode(signature_base64)
        except Exception as e:
            return SignatureResult(
                is_valid=False,
                error=f"签名Base64解码失败: {e}",
            )

        # 确定性JSON序列化
        canonical = json.dumps(manifest_data, sort_keys=True, ensure_ascii=False)
        message = canonical.encode("utf-8")

        if self._use_ed25519 and self._verify_key:
            # Ed25519 验证
            try:
                self._verify_key.verify(message, signature_bytes)
                return SignatureResult(
                    is_valid=True,
                    signer_id=signer_id,
                    signature_base64=signature_base64,
                )
            except Exception as e:
                # 尝试从 trusted_keys 获取发布者公钥验证
                if signer_id and signer_id in TRUSTED_KEYS:
                    pub_key_b64 = TRUSTED_KEYS[signer_id]
                    if pub_key_b64:
                        try:
                            from nacl.signing import VerifyKey

                            pub_key = VerifyKey(base64.b64decode(pub_key_b64))
                            pub_key.verify(message, signature_bytes)
                            return SignatureResult(
                                is_valid=True,
                                signer_id=signer_id,
                                signature_base64=signature_base64,
                            )
                        except Exception:
                            pass

                return SignatureResult(
                    is_valid=False,
                    signer_id=signer_id,
                    error=f"Ed25519验证失败: {e}",
                )

        else:
            # HMAC-SHA256 fallback 验证
            import hmac

            expected = hmac.new(self._hmac_key, message, hashlib.sha256).digest()
            is_valid = hmac.compare_digest(signature_bytes, expected)

            return SignatureResult(
                is_valid=is_valid,
                signer_id=signer_id,
                signature_base64=signature_base64,
                error=None if is_valid else "HMAC签名不匹配",
            )

    def generate_key_pair(self) -> tuple[str, str]:
        """生成Ed25519密钥对，返回(signing_key_b64, verify_key_b64)。"""
        if self._use_ed25519:
            from nacl.signing import SigningKey

            sk = SigningKey.generate()
            vk = sk.verify_key
            return (
                base64.b64encode(bytes(sk)).decode("ascii"),
                base64.b64encode(bytes(vk)).decode("ascii"),
            )
        else:
            # HMAC fallback：返回固定key的base64
            return (
                base64.b64encode(self._hmac_key).decode("ascii"),
                "",  # HMAC无公钥概念
            )


# ============================================================
# 模块级默认签名器（单例）
# ============================================================

# 关键设计：sign 和 verify 必须共用同一个签名器实例，
# 否则 Ed25519 每次生成不同密钥对，验证必然失败。
# 生产环境应通过环境变量或配置文件注入持久化密钥。
_default_signer: PluginSigner | None = None


def get_default_signer() -> PluginSigner:
    """获取模块级默认签名器（单例模式）。

    第一次调用时创建 PluginSigner 实例并生成 Ed25519 密钥对，
    后续调用返回同一实例，确保 sign/verify 使用同一密钥对。
    """
    global _default_signer
    if _default_signer is None:
        _default_signer = PluginSigner()
    return _default_signer


# ============================================================
# PluginManifest签名验证桥接
# ============================================================


def sign_plugin_manifest(manifest: "PluginManifest", signer_id: str = "system") -> str:
    """对PluginManifest签名。"""
    signer = get_default_signer()
    # 排除signature字段本身（避免签名套签名）
    manifest_data = manifest.model_dump(exclude={"signature"})
    return signer.sign_manifest(manifest_data, signer_id)


def verify_plugin_signature(manifest: "PluginManifest") -> tuple[bool, str]:
    """验证PluginManifest签名。"""
    if not manifest.signature:
        return False, "无签名"

    signer = get_default_signer()
    manifest_data = manifest.model_dump(exclude={"signature"})
    result = signer.verify_signature(
        manifest_data,
        manifest.signature,
        manifest.author,
    )

    if result.is_valid:
        return True, f"签名验证通过（签名者: {result.signer_id})"
    return False, result.error or "签名验证失败"


# ============================================================
# 可信发布者管理
# ============================================================


def add_trusted_publisher(author: str, public_key_b64: str) -> bool:
    """添加可信发布者公钥。"""
    TRUSTED_KEYS[author] = public_key_b64
    logger.info("plugin_signing.trusted_publisher_added", author=author)
    return True


def remove_trusted_publisher(author: str) -> bool:
    """移除可信发布者。"""
    if author in TRUSTED_KEYS:
        del TRUSTED_KEYS[author]
        logger.info("plugin_signing.trusted_publisher_removed", author=author)
        return True
    return False


def list_trusted_publishers() -> dict[str, str]:
    """列出所有可信发布者。"""
    return dict(TRUSTED_KEYS)
