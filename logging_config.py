"""
桥v7 结构化日志配置 — structlog生产级配置。

来源：structlog官方 + SkillJect可观测性模式 + 融优实践配置模板
特性：JSON输出 + contextvars请求上下文 + 日志脱敏
"""

import logging

import structlog

from log_sanitizer import redact_sensitive_data


def configure_logging(log_level: str = "INFO"):
    """生产级structlog配置。

    Args:
        log_level: 日志级别，默认INFO。DEBUG时用Console彩色输出。
    """
    shared_processors = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        redact_sensitive_data,
    ]

    is_dev = log_level.upper() == "DEBUG"

    structlog.configure(
        processors=shared_processors
        + [
            structlog.dev.ConsoleRenderer(colors=True)
            if is_dev
            else structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(getattr(logging, log_level.upper())),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )

    # 桥接标准logging到structlog
    logging.basicConfig(
        level=getattr(logging, log_level.upper()),
        format="%(message)s",
        handlers=[logging.StreamHandler()],
    )

    return structlog.get_logger("bridge_v7")


# 全局logger实例
logger = configure_logging()
