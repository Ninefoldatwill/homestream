# ICP消息解析 Prompt — 设计说明

## 职责
将自然语言消息解析为ICP v1.1协议结构化消息。

## 输入输出
- 输入：用户自然语言消息（含@提及、ICP标签等）
- 输出：`{"type": "...", "content": "...", "priority": "...", "mentions": [...]}`

## ICP v1.1 消息类型
| 类型 | 用途 | 优先级默认 |
|:-----|:-----|:-----------|
| INFO | 信息通知 | low |
| ASK | 询问请求 | normal |
| TASK | 任务分配 | high |
| UPD | 状态更新 | normal |
| DONE | 完成通知 | normal |
| WARN | 警告告警 | urgent |
| ACK | 确认收到 | low |
| PING | 心跳检测 | low |
| LOG | 日志记录 | low |

## 已知限制
- 不支持嵌套ICP消息
- 单条消息 <= 500字符
- @提及必须匹配已知Agent名称
