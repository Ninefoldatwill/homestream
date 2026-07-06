# Prompt管理规范

## 目录结构

```
prompts/
├── icp_parser/           # ICP消息解析Prompt
│   ├── v1_0.md           # 版本化Prompt文件
│   └── README.md         # 设计意图+已知限制
├── skill_router/         # 技能路由Prompt
│   ├── v1_0.md
│   └── README.md
├── condition_verifier/   # 条件验证Prompt
│   ├── v1_0.md
│   └── README.md
└── README.md             # 本文件
```

## 版本管理

- 每次修改Prompt创建新版本文件（v1_0.md -> v1_1.md）
- current.md软链接指向当前生产版本
- 变更历史记录在Prompt文件的变更历史表中

## Prompt文件标准结构

1. 版本信息（版本号/创建时间/最后更新）
2. 变更历史表
3. 设计意图（输入/输出描述）
4. Prompt正文（实际模板）
5. 参数表
6. 已知限制
7. 黄金集路径

## 评估

- L1单元测试：固定输入 -> 期望输出
- L2黄金集评估：50条标注数据，LLM-as-Judge评分
- L3多轮模拟（V8阶段）
