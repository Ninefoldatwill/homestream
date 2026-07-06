---
name: code-reviewer
description: |
  代码审查专家 — 当你想review PR/commits/changes时触发。
  做什么：自动检查代码质量、安全漏洞、最佳实践偏离。
  何时用：pull request review / pre-commit check / 周期性审计。
version: 1.2.0
license: MIT
compatibility: openbridge>=5.0.0, claude, gemini, codex
allowed-tools: read_file list_files
metadata:
  tags: [code-review, security, quality]
  capabilities: [static-analysis, security-scan, best-practices]
  author: HomeStream Contributors
  homepage: https://github.com/Ninefoldatwill/homestream/skills/code-reviewer
---

# Code Reviewer

## 用途

自动审查代码变更，识别：

- 安全漏洞（注入/凭据泄露/危险操作）
- 代码质量问题（重复/复杂度/坏味道）
- 最佳实践偏离（命名/注释/结构）

## 触发条件

- pull request 创建/更新
- pre-commit hook
- 手动调用 `homestream skills run code-reviewer`

## 快速开始

```bash
# Review 一个 PR
curl -X POST http://localhost:3458/api/skills/code-reviewer/execute \
  -H "Authorization: Bearer $AGENT_TOKEN" \
  -d '{"target": "pull/42", "focus": "security"}'
```

## 输出示例

```json
{
  "verdict": "approve_with_comments",
  "severity": "medium",
  "issues": [
    {"line": 42, "type": "injection", "message": "未过滤的用户输入直接拼接到SQL"}
  ]
}
```

## 错误处理

- 文件读取失败时降级为"仅分析diff"
- 超时(10s)自动返回部分结果
- retry机制：网络失败重试3次

## 参考引用

- OWASP Top 10: https://owasp.org/Top10/
- arXiv 2026 "SkillsBench: Evaluating Agent Skills"

## FAQ

**Q: 支持哪些语言？**
A: Python / TypeScript / Go / Rust

## 变更日志

- v1.2.0: 新增 Go 语言支持
- v1.1.0: 增加安全扫描子模块
- v1.0.0: 初始版本
