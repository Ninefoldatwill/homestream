# 条件验证 Prompt — 设计说明

## 职责
验证任务完成条件是否满足，支持多种条件类型。

## 支持的条件类型
| 类型 | 说明 | 示例 |
|:-----|:-----|:-----|
| file_exists | 文件存在 | artifact.py 存在 |
| test_pass | 测试通过 | pytest 全绿 |
| api_response | API响应 | HTTP 200 |
| text_contains | 文本包含 | 包含"完成" |
| count_match | 数量匹配 | 测试数 >= 10 |

## 输入输出
- 输入：任务定义 + 条件列表 + 当前状态
- 输出：`{"all_passed": true, "results": [...]}`

## 验证流程
1. 逐一检查每个条件
2. 记录通过/失败状态和原因
3. 全部通过 → all_passed=true
4. 任一失败 → all_passed=false + 失败原因
