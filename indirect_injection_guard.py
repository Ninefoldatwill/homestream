"""
间接注入防护模块 — 5向量纵深防御。

参考：Microsoft三层纵深(2025.7) + AI-FENCE流式网关(2025.10)
设计：不依赖阻止所有注入，确保注入成功也不产生安全影响。

注入向量覆盖：
  EventStream事件 · 群聊消息 · Kanban回调 · 书阁知识 · 外部API响应
"""

import base64
import json
import re
import unicodedata
from enum import Enum
from typing import Any

import structlog

logger = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Severity
# ---------------------------------------------------------------------------


class InjectionSeverity(str, Enum):
    CRITICAL = "critical"  # 完全阻断
    HIGH = "high"  # 剥离可疑片段
    MEDIUM = "medium"  # 标记+告警
    LOW = "low"  # 记录+放行
    NONE = "none"  # 安全


# ---------------------------------------------------------------------------
# Known injection patterns (OWASP LLM01 + 中文覆盖)
# ---------------------------------------------------------------------------

ROLE_CONFUSION_EN = [
    r"ignore\s+(previous|above|all)\s+instructions",
    r"you\s+are\s+(now\s+)?a\s+",
    r"system\s+prompt",
    r"forget\s+(everything|all\s+previous)",
    r"disregard\s+(all|previous)",
    r"reveal\s+(your|the)\s+(system\s+)?prompt",
    r"jailbreak",
    r"DAN\s+mode",
    r"act\s+as\s+(if\s+you\s+were|a\s+different)",
    r"new\s+instructions?\s*:",
    r"override\s+(all\s+)?previous",
    r"\[INST\].*\[/INST\]",
    r"<\|im_start\|>",
    r"<\|im_end\|>",
]

ROLE_CONFUSION_ZH = [
    r"忽略\s*(以上|上面|所有|全部)\s*(指令|指示|命令|提示|规则)",
    r"无视\s*(以上|上面|所有|全部)\s*(指令|指示|命令|提示|规则)",
    r"忘记\s*(以上|上面|所有|全部)\s*(指令|指示|命令|提示|规则)",
    r"不要\s*(遵守|遵循|执行)\s*(以上|上面|所有|全部)\s*(指令|指示|命令|提示|规则)",
    r"透露\s*(你的|系统)\s*(提示|指令|规则|秘密)",
    r"显示\s*(你的|系统)\s*(提示|指令|规则|秘密)",
    r"系统\s*提示",
    r"越狱|逃逸|突破\s*(限制|约束|安全|规则)",
    r"现在\s*(你|你的身份)\s*是",
    r"新\s*指令\s*[：:]",
]

ROLE_CONFUSION_PATTERNS = [
    re.compile(p, re.IGNORECASE) for p in ROLE_CONFUSION_EN + ROLE_CONFUSION_ZH
]

# Structure anomalies: nested XML/JSON/分隔符
STRUCT_ANOMALY_PATTERNS = [
    re.compile(r"(<\|[^|]*\|>){3,}"),
    re.compile(r"```(system|instruction|prompt)"),
    re.compile(r"\{\s*\"role\"\s*:\s*\"system\"\s*,"),
]

# Zero-width chars
ZERO_WIDTH_CHARS = {
    0x200B,
    0x200C,
    0x200D,
    0x200E,
    0x200F,
    0xFEFF,
    0x00AD,
    0x061C,
    0x2060,
    0x2061,
    0x2062,
    0x2063,
    0x2064,
}


# ---------------------------------------------------------------------------
# Input sanitizer
# ---------------------------------------------------------------------------


def unicode_normalize(text: str, form: str = "NFKC") -> str:
    """Unicode 归一化，消除同形异码攻击。"""
    return unicodedata.normalize(form, text)


def strip_zero_width(text: str) -> str:
    """移除零宽字符。"""
    return "".join(ch for ch in text if ord(ch) not in ZERO_WIDTH_CHARS)


def decode_nested(text: str, max_depth: int = 3) -> str:
    """递归解码 Base64 / Hex 嵌套编码。"""
    result = text
    for _ in range(max_depth):
        decoded = None
        try:
            decoded = base64.b64decode(result, validate=True).decode("utf-8")
        except Exception:
            pass
        if decoded is None:
            try:
                decoded = bytes.fromhex(result).decode("utf-8")
            except Exception:
                pass
        if decoded is None:
            break
        result = decoded
    return result


def sanitize_raw_input(text: str) -> tuple[str, bool]:
    """输入净化：归一→去零宽→解码嵌套→检查变化。

    Returns:
        (sanitized_text, was_modified)
    """
    original = text
    text = unicode_normalize(text)
    text = strip_zero_width(text)
    text = decode_nested(text)
    return text, (text != original)


# ---------------------------------------------------------------------------
# Classifier
# ---------------------------------------------------------------------------


def _count_role_confusion(text: str) -> int:
    """检测角色混淆模式命中数。"""
    return sum(1 for p in ROLE_CONFUSION_PATTERNS if p.search(text))


def _has_structure_anomaly(text: str) -> bool:
    """检测结构异常（嵌套指令/非对称分隔符）。"""
    for p in STRUCT_ANOMALY_PATTERNS:
        if p.search(text):
            return True
    # 非对称分隔符：``` 数量为奇数
    if text.count("```") % 2 != 0:
        return True
    return False


def classify(text: str) -> tuple[InjectionSeverity, list[str]]:
    """注入分类器（无外部API依赖）。

    Returns:
        (severity, reasons)
    """
    reasons: list[str] = []
    role_hits = _count_role_confusion(text)
    struct_anom = _has_structure_anomaly(text)

    if role_hits >= 3:
        reasons.append(f"role_confusion_hits={role_hits}")
        return InjectionSeverity.CRITICAL, reasons
    if role_hits >= 1 and struct_anom:
        reasons.append(f"role={role_hits}+struct_anomaly")
        return InjectionSeverity.HIGH, reasons
    if role_hits >= 1:
        reasons.append(f"role_confusion_hits={role_hits}")
        return InjectionSeverity.MEDIUM, reasons
    if struct_anom:
        reasons.append("structure_anomaly")
        return InjectionSeverity.LOW, reasons
    return InjectionSeverity.NONE, reasons


# ---------------------------------------------------------------------------
# Central guard function
# ---------------------------------------------------------------------------


def _sanitize_marked(text: str, marker: str) -> str:
    """剥离匹配的注入片段，用标记替换。"""
    for p in ROLE_CONFUSION_PATTERNS:
        text = p.sub(f"[FILTERED_{marker}]", text)
    return text


def inspect(content: str, vector: str = "unknown") -> tuple[str, bool, str | None]:
    """统一安检入口。

    Args:
        content: 待检测文本
        vector: 注入向量标识 (event_stream/channel_message/kanban_callback/bookhouse/external_response)

    Returns:
        (safe_content, was_blocked, alert_message)
    """
    if not content:
        return content, False, None

    # Step 1: input sanitization
    clean, modified = sanitize_raw_input(content)
    if modified:
        logger.info(
            "indirect_injection.sanitize_modified",
            vector=vector,
            original_length=len(content),
            clean_length=len(clean),
        )

    # Step 2: classification
    severity, reasons = classify(clean)

    if severity == InjectionSeverity.NONE:
        return clean, False, None

    # Step 3: triage
    if severity == InjectionSeverity.CRITICAL:
        logger.warning(
            "indirect_injection.blocked", vector=vector, severity="CRITICAL", reasons=reasons
        )
        return "", True, f"INDIRECT_INJECTION_BLOCKED:{vector}:" + ",".join(reasons)

    if severity == InjectionSeverity.HIGH:
        safe = _sanitize_marked(clean, vector)
        logger.warning(
            "indirect_injection.sanitized",
            vector=vector,
            severity="HIGH",
            reasons=reasons,
            chars_removed=len(clean) - len(safe),
        )
        return safe, False, f"INDIRECT_INJECTION_SANITIZED:{vector}:" + ",".join(reasons)

    # MEDIUM / LOW: flag only
    logger.info("indirect_injection.flagged", vector=vector, severity=severity, reasons=reasons)
    return clean, False, f"INDIRECT_INJECTION_FLAGGED:{vector}:" + ",".join(reasons)


# ---------------------------------------------------------------------------
# 5 vector hooks — convenience wrappers
# ---------------------------------------------------------------------------


def guard_event(event: dict) -> dict:
    """保护 EventStream 事件。检测 content 字段。"""
    if isinstance(event, dict) and "content" in event:
        safe, blocked, alert = inspect(event["content"], "event_stream")
        if blocked:
            event["content"] = "[BLOCKED] indirect injection detected"
            event["_security_alert"] = alert
        elif alert:
            event["content"] = safe
            event["_security_alert"] = alert
    return event


def guard_message(msg: dict) -> dict:
    """保护群聊消息。"""
    if isinstance(msg, dict) and "content" in msg:
        safe, blocked, alert = inspect(msg["content"], "channel_message")
        if blocked:
            msg["content"] = "[BLOCKED] indirect injection detected"
            msg["_security_alert"] = alert
        elif alert:
            msg["content"] = safe
            msg["_security_alert"] = alert
    return msg


def guard_kanban(data: dict) -> tuple[dict, bool]:
    """保护 Kanban 回调。JSON Schema 校验 + 内容安检。

    Returns:
        (safe_data, is_rejected)
    """
    if not isinstance(data, dict):
        return data, True
    # Reject if suspicious payload
    raw = json.dumps(data, ensure_ascii=False)
    _, blocked, alert = inspect(raw, "kanban_callback")
    if blocked:
        logger.warning("indirect_injection.kanban_rejected", detail=alert)
        return {}, True
    return data, False


def guard_bookhouse(items: list) -> list:
    """保护书阁知识检索结果。过滤注入后的知识条目。"""
    if not isinstance(items, list):
        return items
    safe_items = []
    for item in items:
        if isinstance(item, dict):
            text_fields = " ".join(str(v) for v in item.values())
            _, blocked, alert = inspect(text_fields, "bookhouse")
            if blocked:
                logger.warning("indirect_injection.bookhouse_filtered", item_id=item.get("id", "?"))
                continue
        safe_items.append(item)
    return safe_items


def guard_external(
    content: str, source_domain: str, allowed_domains: list[str] | None = None
) -> tuple[str, bool]:
    """保护外部API响应。域白名单 + 内容安检。

    Returns:
        (safe_content, is_blocked)
    """
    if allowed_domains and source_domain not in allowed_domains:
        logger.warning(
            "indirect_injection.domain_blocked", domain=source_domain, allowed=allowed_domains
        )
        return "", True
    safe, blocked, alert = inspect(content, "external_response")
    return safe, blocked


# ---------------------------------------------------------------------------
# Bulk audit for batch analysis
# ---------------------------------------------------------------------------


def audit_batch(entries: list[dict[str, Any]]) -> dict:
    """批量审计：在非关键路径上全量扫描注入风险。

    Returns:
        {total, blocked, sanitized, flagged, clean, alerts: [...]}
    """
    result = {
        "total": len(entries),
        "blocked": 0,
        "sanitized": 0,
        "flagged": 0,
        "clean": 0,
        "alerts": [],
    }
    for entry in entries:
        content = entry.get("content", "") if isinstance(entry, dict) else str(entry)
        severity, reasons = classify(sanitize_raw_input(content)[0])
        if severity == InjectionSeverity.CRITICAL:
            result["blocked"] += 1
            result["alerts"].append(
                {"entry": str(entry)[:80], "severity": "CRITICAL", "reasons": reasons}
            )
        elif severity == InjectionSeverity.HIGH:
            result["sanitized"] += 1
        elif severity in (InjectionSeverity.MEDIUM, InjectionSeverity.LOW):
            result["flagged"] += 1
        else:
            result["clean"] += 1
    return result
