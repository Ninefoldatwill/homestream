# Qwen Provider IP合规评估报告

> 评估对象：`providers/qwen_provider.py` — 通义千问API客户端适配器
> 评估日期：2026-07-09
> 评估目的：确认开源到GitHub外网是否存在知识产权（IP）风险
> 评估结论：**✅ 无IP风险 — 可安全开源**

---

## 一、评估背景

九重提出关键担忧：新增的通义千问三档（turbo/plus/max）国产备选方案，
开源到外网GitHub和内网平台是否会涉及产权问题？

本报告逐条分析Qwen许可证条款与`qwen_provider.py`的关系，给出明确结论。

---

## 二、Qwen许可证关键条款分析

Qwen模型采用自定义许可证（非Apache 2.0），以下是关键条款及其对本项目的影响：

### 条款1：定义 — "Materials"的范围

> "Qwen" shall mean the large language models, and software and algorithms,
> consisting of trained model weights, parameters (including optimizer states),
> machine-learning model code, inference-enabling code, training-enabling code,
> fine-tuning enabling code and other elements of the foregoing distributed by us.
>
> "Materials" shall mean, collectively, Alibaba Cloud's proprietary Qwen and
> Documentation (and any portion thereof) made available under this Agreement.

**分析**：Materials = 模型权重 + 参数 + 模型代码 + 推理代码 + 训练代码 + 微调代码 + 文档。
我们的`qwen_provider.py`**不包含上述任何内容**——它是一个通过HTTP调用API的客户端适配器，
不含任何模型权重或源代码。因此，**qwen_provider.py不是"Materials"**。

### 条款2：商标使用（第6.b条）— 最关键条款

> No trademark license is granted to use the trade names, trademarks, service marks,
> or product names of us, **except as required to fulfill notice requirements under
> this Agreement or as required for reasonable and customary use in describing and
> redistributing the Materials.**

**分析**：未授予商标许可，但有两个例外：
1. 满足通知要求所必需
2. **描述和再分发Materials时的"合理和惯常使用"（reasonable and customary use）**

我们在代码和文档中使用"通义千问"/"Qwen"名称，是在**描述**这个Provider连接的是
哪个API服务——这正是"描述性合理使用"（descriptive fair use）。我们没有：
- 将"Qwen"作为我们自己的产品名称
- 暗示阿里巴巴集团赞助或背书本项目
- 将"Qwen"用于域名或品牌标识

### 条款3：再分发（第3条）

> You may distribute copies or make the Materials, or derivative works thereof,
> available as part of a product or service...

**分析**：再分发条款仅适用于"Materials"。我们不分发Materials
（不含模型权重/源代码），因此本条**不适用**。

### 条款4：100M MAU限制（第4条）

> If you are commercially using the Materials, and your product or service has
> more than 100 million monthly active users, you shall request a license from us.

**分析**：两个前提条件——①商业使用Materials ②月活超过1亿。
HomeStream是开源项目（非商业），且远未达到1亿月活。**不适用**。

### 条款5：使用规则（第5条）

> If you use the Materials or any outputs or results therefrom to create, train,
> fine-tune, or improve an AI model... you shall prominently display "Built with Qwen"

**分析**：仅当使用Materials的输出来训练其他AI模型时触发。HomeStream不涉及
用API输出训练模型。**不适用**。

---

## 三、行业先例

以下主流开源项目均封装了通义千问/DashScope API，且以各自开源许可证发布：

| 项目         | 许可证     | Qwen封装方式              | GitHub Stars |
|:-------------|:-----------|:--------------------------|:-------------|
| LangChain    | MIT        | Community Provider集成    | 98K+         |
| AutoGen      | MIT/CC-BY  | 模型客户端适配器           | 60K+         |
| CrewAI       | MIT        | LLM Wrapper               | 55K+         |
| Dify         | Apache 2.0 | Model Provider插件         | 148K+        |
| vLLM         | Apache 2.0 | 直接推理（含模型权重加载） | 30K+         |

**结论**：API客户端适配器模式是AI行业的通行实践，被广泛接受。
HomeStream的`qwen_provider.py`与LangChain/CrewAI的做法完全一致。

---

## 四、qwen_provider.py 代码特征分析

| 特征                 | qwen_provider.py | 是否构成IP风险 |
|:---------------------|:-----------------|:---------------|
| 包含模型权重         | ❌ 不包含        | 无风险         |
| 包含模型源代码       | ❌ 不包含        | 无风险         |
| 包含推理/训练代码    | ❌ 不包含        | 无风险         |
| 复制Qwen官方SDK代码  | ❌ 独立开发      | 无风险         |
| 使用"通义千问"商标   | ✅ 描述性使用    | 合理使用       |
| 暗示阿里云背书       | ❌ 明确声明无关联| 无风险         |
| 调用DashScope API    | ✅ HTTP客户端    | 无风险         |
| 100M+月活商业使用    | ❌ 开源非商业    | 不适用         |

---

## 五、已执行的合规优化

### 5.1 商标声明注释

在`qwen_provider.py`、`deepseek_provider.py`、`glm_provider.py`三个Provider
的docstring中统一添加了商标声明，明确标注：

```
商标声明：
  "通义千问"和"Qwen"是阿里巴巴集团（Alibaba Cloud）的商标。
  本文件是独立开发的API客户端适配器，不包含任何Qwen模型权重、
  参数或源代码（非Qwen许可证定义的"Materials"），仅通过HTTP
  调用DashScope API。本项目与阿里巴巴集团无关联、未获背书。
```

### 5.2 NOTICE文件

创建了`NOTICE`文件，集中声明所有第三方商标归属：
- 通义千问/Qwen → 阿里巴巴集团
- DeepSeek/深度求索 → DeepSeek公司
- GLM/智谱 → 北京智谱华章科技有限公司
- Ollama → Ollama Inc.
- Hugging Face → Hugging Face Inc.
- ECharts → Apache Software Foundation (Apache 2.0)

### 5.3 API客户端适配器声明

在NOTICE文件中明确声明Provider的四个特征：
1. 不包含模型权重
2. 不包含模型源代码
3. 纯HTTP客户端
4. 原创实现

---

## 六、与其他IP评估报告的关系

HomeStream已建立完整的IP合规体系：

```
IP_RISK_ASSESSMENT.md          — arch_visualizer + data_guardian 评估
IP_PRE_DEV_ASSESSMENT.md       — theme_a11y + A2A_PROTOCOL 预排雷
IP_QWEN_PROVIDER_ASSESSMENT.md — Qwen Provider IP合规评估（本报告）
TECH_SOVEREIGNTY_ASSESSMENT.md — 技术主权保障评估
NOTICE                         — 第三方商标与服务声明
```

---

## 七、最终结论

**✅ qwen_provider.py 无IP风险，可安全开源到GitHub外网和内网平台。**

核心理由：
1. **不是Materials**：qwen_provider.py是独立开发的API客户端适配器，
   不含任何Qwen模型权重或源代码，不受Qwen许可证约束
2. **商标合理使用**：使用"通义千问"/"Qwen"名称属于描述性合理使用，
   符合Qwen许可证第6.b条例外条款
3. **行业通行实践**：LangChain、CrewAI、Dify等主流开源项目均采用相同模式
4. **合规优化已完成**：商标声明注释 + NOTICE文件 + API客户端适配器声明

**铸钥匠哲学**：我们不铸造别人的锁（不复制模型权重），也不盗用别人的
钥匙标记（不将商标据为己有）——我们只是打造一把能插入别人锁孔的钥匙柄，
钥匙的形状（API协议）是公开标准，锁和锁芯（模型）仍归原主所有。

---

*评估人：澜舟（AI开发工程师） | 审核人：九重（项目总规划）*
