"""
桥v7 日志脱敏过滤器 — 防止敏感信息泄露到日志。

来源：OWASP A02加密失败防护 + 融优实践配置模板
"""

import re

SENSITIVE_PATTERNS = {
    "token": re.compile(
        r'(token|api_key|secret|password)(["\']?\s*[:=]\s*["\']?)([a-zA-Z0-9_\-]{8,})',
        re.IGNORECASE,
    ),
    "bearer": re.compile(r"(Bearer\s+)([a-zA-Z0-9_\-\.]+)", re.IGNORECASE),
}


def redact_sensitive_data(_, __, event_dict):
    """structlog处理器：脱敏日志中的敏感信息。

    放在JSONRenderer之前，自动过滤token/api_key/password/Bearer。
    """

    def _redact(obj):
        if isinstance(obj, str):
            for name, pattern in SENSITIVE_PATTERNS.items():
                if name == "bearer":
                    obj = pattern.sub(r"\1***", obj)
                else:
                    obj = pattern.sub(
                        lambda m: f"{m.group(1)}{m.group(2)}{m.group(3)[:4]}***",
                        obj,
                    )
            return obj
        elif isinstance(obj, dict):
            return {k: _redact(v) for k, v in obj.items()}
        elif isinstance(obj, list):
            return [_redact(item) for item in obj]
        return obj

    return _redact(event_dict)
