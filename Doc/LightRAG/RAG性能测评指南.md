# LightRAG RAG 性能测评指南

**项目**：LightRAG · **版本**：1.5.5 · **日期**：2026-07-08 · **作者**：15531

> 本文档回答两个问题：**「借鉴这个项目能做出性能不错的 RAG 吗」** 与 **「不知道怎么测评怎么办」**。好消息：LightRAG **自带完整的评估框架**（RAGAS + 离线检索检查），你不用从零搭。

---

## 一、第一个问题：能做出性能不错的 RAG 吗？—— 能

### 1.1 项目自评结果（官方基线）

LightRAG 官方用自带的评估框架跑过，结果如下（来自 `lightrag/evaluation/README_EVALUASTION_RAGAS.md`）：

```
Average Faithfulness:      0.9053   ← 答案忠实于检索内容（不幻觉）
Average Answer Relevance:  0.8646   ← 答案切题
Average Context Recall:    1.0000   ← 相关信息全召回
Average Context Precision: 1.0000   ← 召回无噪声
Average RAGAS Score:       0.9425   ← 综合质量
```

> **0.80+ 即生产可用，0.94 属于优秀。** 这说明 LightRAG 的图谱增强检索范式确实有效。

### 1.2 它为什么性能不错（架构优势）

```mermaid
graph LR
    subgraph 传统RAG["传统向量 RAG 的短板"]
        T1["❌ 多跳推理弱<br/>A→B→C 查不全"]
        T2["❌ 全局综述差<br/>只看局部相似 chunk"]
        T3["❌ 实体关系丢失<br/>向量相似 ≠ 关系链"]
    end

    subgraph LightRAG["LightRAG 的解法"]
        L1["✅ 知识图谱<br/>实体+关系显式建模"]
        L2["✅ 双层检索<br/>local 细节 + global 综述"]
        L3["✅ mix 模式<br/>图+向量融合+rerank"]
        L4["✅ 四角色 LLM<br/>抽取/问答/关键词/VLM 各用最优"]
    end

    T1 -.->|"解决"| L1
    T2 -.->|"解决"| L2
    T3 -.->|"解决"| L3

    style 传统RAG fill:#ffcdd2
    style LightRAG fill:#c8e6c9
```

### 1.3 借鉴/编排的可行路径

```mermaid
graph TD
    GOAL["🎯 目标：做出性能不错的 RAG"]

    GOAL --> P1["路径 1：直接用<br/>━━━━━━━━━<br/>改 .env 配置<br/>直接跑"]

    GOAL --> P2["路径 2：库内嵌<br/>━━━━━━━━━<br/>import LightRAG<br/>嵌入新项目"]

    GOAL --> P3["路径 3：借鉴设计<br/>━━━━━━━━━<br/>学它的图谱增强<br/>双层检索思想"]

    P1 --> P1_OUT["最快，2小时跑通<br/>但定制性受限"]
    P2 --> P2_OUT["中等，可深度定制<br/>保留全部能力"]
    P3 --> P3_OUT["最灵活，自研<br/>但工作量最大"]

    P1_OUT --> VERIFY["无论哪条路<br/>都要测评验证"]
    P2_OUT --> VERIFY
    P3_OUT --> VERIFY

    style GOAL fill:#fffde7,stroke:#f57f17
    style VERIFY fill:#e8f5e9,stroke:#2e7d32
```

---

## 二、第二个问题：怎么测评？—— 项目自带两套工具

LightRAG 在 `lightrag/evaluation/` 下提供了**两层评估体系**：

```mermaid
graph TD
    EVAL["📊 评估体系"]

    EVAL --> L1["Layer 1：离线检索检查<br/>━━━━━━━━━━━━━━━━<br/>offline_retrieval_check.py<br/>无需 LLM/Embedding 调用<br/>纯词汇 Recall@K"]

    EVAL --> L2["Layer 2：RAGAS 端到端质量<br/>━━━━━━━━━━━━━━━━━━<br/>eval_rag_quality.py<br/>需 LLM+Embedding<br/>4 大指标全面评估"]

    L1 --> L1_USE["用途：快速验证<br/>召回有没有命中<br/>零成本、可进 CI"]
    L2 --> L2_USE["用途：全面评估<br/>答案质量<br/>生产上线门槛"]

    style L1 fill:#e3f2fd,stroke:#1565c0
    style L2 fill:#fff3e0,stroke:#e65100
```

---

## 三、Layer 1：离线检索检查（零成本，先跑这个）

### 3.1 它测什么

**不调 LLM、不调 Embedding、不调 LightRAG 服务**。用 TF-IDF 词汇匹配检查「问题能否检索到期望文档」，作为**前置健康检查**。

来自 `offline_retrieval_check.py` 的指标：
- **Recall@K**（前 K 个结果是否命中期望文档）
- **Reciprocal Rank**（期望文档的排名倒数）
- **full_recall_queries / no_hit_queries**（全命中/零命中计数）

### 3.2 怎么跑

```bash
# 项目自带 6 个样例问题 + 5 个样例文档
cd /d/Project/LightRAG
python lightrag/evaluation/offline_retrieval_check.py --strict
```

### 3.3 自定义你的数据

准备三个文件：

```mermaid
graph LR
    D["文档目录<br/>━━━━━━━━<br/>你的 .md/.txt 文件"]
    Q["问题集 dataset.json<br/>━━━━━━━━<br/>test_cases: 问题列表"]
    O["标准答案 oracle.json<br/>━━━━━━━━<br/>oracle: 问题→期望文档映射"]

    D --> CHECK["offline_retrieval_check.py"]
    Q --> CHECK
    O --> CHECK

    CHECK --> R["Recall@K 报告"]

    style CHECK fill:#c8e6c9,stroke:#2e7d32
```

文件格式：
```json
// dataset.json
{"test_cases": [{"question": "你的问题"}]}

// oracle.json
{"oracle": [{"question": "你的问题", "expected_documents": ["alpha.md"]}]}
```

---

## 四、Layer 2：RAGAS 端到端质量评估（核心）

### 4.1 它测什么（4 大指标）

```mermaid
graph TD
    RAGAS["🎯 RAGAS 4 大指标"]

    RAGAS --> F["Faithfulness 忠实度<br/>━━━━━━━━━━━━<br/>答案是否基于检索内容?<br/>检测幻觉<br/>>0.80 优秀"]
    RAGAS --> AR["Answer Relevance 答案切题度<br/>━━━━━━━━━━━━━━━━━━<br/>答案是否回答了问题?<br/>检测跑题<br/>>0.80 优秀"]
    RAGAS --> CR["Context Recall 上下文召回<br/>━━━━━━━━━━━━━━━━━━<br/>相关信息是否全召回?<br/>检测漏检<br/>>0.80 优秀"]
    RAGAS --> CP["Context Precision 上下文精度<br/>━━━━━━━━━━━━━━━━━━━<br/>召回是否无噪声?<br/>检测冗余<br/>>0.80 优秀"]

    F --> AVG["RAGAS Score<br/>= 四项平均<br/>>0.80 生产可用"]

    style F fill:#c8e6c9
    style AR fill:#c8e6c9
    style CR fill:#c8e6c9
    style CP fill:#c8e6c9
    style AVG fill:#fffde7,stroke:#f57f17
```

### 4.2 评分标准

| 区间 | 评级 | 含义 |
|---|---|---|
| 0.80–1.00 | ✅ 优秀 | 生产可用 |
| 0.60–0.80 | ⚠️ 良好 | 有改进空间 |
| 0.40–0.60 | ❌ 较差 | 需优化 |
| 0.00–0.40 | 🔴 严重 | 有重大问题 |

### 4.3 怎么跑

```bash
# 1. 安装评估依赖
pip install -e ".[evaluation]"
# 或：pip install ragas datasets

# 2. 启动 LightRAG 服务（必须，评估器要调它的 API）
PYTHONUTF8=1 uv run lightrag-server

# 3. 把测试文档入库（用 WebUI 或 API）

# 4. 跑评估（默认测自带 6 个问题）
python lightrag/evaluation/eval_rag_quality.py

# 或指定你的测试集 + 服务地址
python lightrag/evaluation/eval_rag_quality.py -d my_test.json -r http://localhost:9621
```

### 4.4 评估流程（内部做了什么）

```mermaid
graph TD
    START["开始评估"]

    START --> LOAD["加载测试集<br/>test_cases: 问题+标准答案"]

    LOAD --> LOOP["并发评估每个 case<br/>EVAL_MAX_CONCURRENT=2"]

    LOOP --> Q1["① 调 LightRAG API<br/>POST /query?mode=...<br/>拿回 answer + context"]

    Q1 --> Q2["② RAGAS 用 LLM 打分<br/>━━━━━━━━━━━━━<br/>Faithfulness: 答案 vs context<br/>AnswerRel: 答案 vs 问题<br/>ContextRecall: context vs 标准答案<br/>ContextPrec: 逐个 chunk 判断相关性"]

    Q2 --> COLLECT["收集指标"]

    COLLECT -->|"所有 case 完成"| REPORT["生成报告<br/>━━━━━━━━<br/>results_时间戳.json<br/>results_时间戳.csv<br/>终端表格 + 均值统计"]

    style START fill:#fffde7,stroke:#f57f17
    style Q2 fill:#fff3e0,stroke:#e65100
    style REPORT fill:#c8e6c9,stroke:#2e7d32
```

### 4.5 准备你自己的测试集

```json
{
  "test_cases": [
    {
      "question": "城乡居民养老保险的申领条件是什么？",
      "ground_truth": "年满60周岁、累计缴费满15年、未领取其他养老待遇...",
      "project": "社保政策问答"
    }
  ]
}
```

> **关键**：`ground_truth`（标准答案）必须有，RAGAS 靠它算 Context Recall。你自己手写或用 LLM 从文档生成。

### 4.6 配置评估用的模型（环境变量）

```env
# 评估用的 LLM（RAGAS 打分用，必须 OpenAI 兼容）
EVAL_LLM_MODEL=gpt-4o-mini
EVAL_LLM_BINDING_API_KEY=sk-xxx
# EVAL_LLM_BINDING_HOST=http://localhost:8000/v1  # 自部署可选

# 评估用的 Embedding
EVAL_EMBEDDING_MODEL=text-embedding-3-large
EVAL_EMBEDDING_BINDING_API_KEY=sk-xxx

# 并发与限流
EVAL_MAX_CONCURRENT=2        # 并发评估数（遇 429 就降到 1）
EVAL_QUERY_TOP_K=10          # 检索召回数
EVAL_LLM_MAX_RETRIES=5       # 重试次数
EVAL_LLM_TIMEOUT=180         # 超时秒数
```

> **注意**：评估模型可以和被评估的 LightRAG 用**不同的**模型。建议评估用**更强的模型**（如 gpt-4o）以保证打分公正。

---

## 五、从测评到优化（闭环）

测出分数后，按低分指标对症优化：

```mermaid
graph TD
    RESULT["📊 评估结果"]

    RESULT --> LOW_F{"Faithfulness 低?<br/>答案有幻觉"}
    LOW_F -->|"是"| FIX_F["优化：<br/>• 提升实体抽取质量（换更强 EXTRACT 模型）<br/>• 更好的分块策略（试 R/V/P）<br/>• 增大 MAX_TOTAL_TOKENS 限制"]

    RESULT --> LOW_AR{"Answer Relevance 低?<br/>答案跑题"}
    LOW_AR -->|"是"| FIX_AR["优化：<br/>• 改 QUERY 角色 prompt<br/>• 调高 COSINE_THRESHOLD 相似度阈值<br/>• 试不同 mode（mix vs hybrid）"]

    RESULT --> LOW_CR{"Context Recall 低?<br/>信息没召回"}
    LOW_CR -->|"是"| FIX_CR["优化：<br/>• 增大 TOP_K 召回数<br/>• 换更好的 Embedding 模型<br/>• 用 P 语义分块保留结构"]

    RESULT --> LOW_CP{"Context Precision 低?<br/>召回有噪声"}
    LOW_CP -->|"是"| FIX_CP["优化：<br/>• 开启 Rerank（ENABLE_RERANK=true）<br/>• 减小 chunk 大小<br/>• 调低 TOP_K"]

    FIX_F --> RE["重新评估"]
    FIX_AR --> RE
    FIX_CR --> RE
    FIX_CP --> RE

    RE --> RESULT

    style RESULT fill:#fffde7,stroke:#f57f17
    style RE fill:#e3f2fd,stroke:#1565c0
```

---

## 六、完整测评工作流（从零到报告）

```mermaid
graph TD
    S1["① 准备文档<br/>收集你的业务文档"]
    S2["② 准备测试集<br/>写问题 + 标准答案<br/>dataset.json"]
    S3["③ Layer 1 离线检查<br/>offline_retrieval_check.py<br/>零成本验证召回"]
    S4["④ 启动 LightRAG<br/>入库文档"]
    S5["⑤ Layer 2 RAGAS 评估<br/>eval_rag_quality.py<br/>全面打分"]
    S6["⑥ 分析报告<br/>哪个指标低?"]
    S7["⑦ 对症优化<br/>调参/换模型/改策略"]
    S8["⑧ 达标?<br/>RAGAS > 0.80"]

    S1 --> S2 --> S3 --> S4 --> S5 --> S6 --> S7
    S7 -->|"否"| S8
    S8 -->|"否"| S5
    S8 -->|"是"| DONE["✅ 生产可用"]

    style S3 fill:#e3f2fd,stroke:#1565c0
    style S5 fill:#fff3e0,stroke:#e65100
    style DONE fill:#c8e6c9,stroke:#2e7d32
```

### 最少步骤版（快速验证）

```bash
# 1. 装依赖
pip install -e ".[evaluation]"

# 2. 启服务 + 入库（用自带样例文档）
PYTHONUTF8=1 uv run lightrag-server
# 在 WebUI 上传 lightrag/evaluation/sample_documents/ 里的 5 个 md

# 3. 跑评估
python lightrag/evaluation/eval_rag_quality.py

# 4. 看 results/ 下的报告
```

---

## 七、除了 RAGAS，还能怎么测

```mermaid
graph TD
    AUTO["项目自带（自动化）"]
    MANUAL["补充手段（人工/半自动）"]

    AUTO --> A1["RAGAS 4 指标<br/>━━━━━━━━━<br/>忠实/切题/召回/精度"]
    AUTO --> A2["离线 Recall@K<br/>━━━━━━━━━<br/>词汇级命中检查"]
    AUTO --> A3["Langfuse 追踪<br/>━━━━━━━━━<br/>LLM 调用链监控<br/>cost/latency/错误率"]

    MANUAL --> M1["人工盲评<br/>━━━━━━━━━<br/>领域专家打分<br/>最贴近真实质量"]
    MANUAL --> M2["A/B 对比<br/>━━━━━━━━━<br/>不同 mode/参数对比<br/>mix vs hybrid 等"]
    MANUAL --> M3["Bad case 收集<br/>━━━━━━━━━<br/>上线后收集失败案例<br/>迭代测试集"]
    MANUAL --> M4["延迟/吞吐<br/>━━━━━━━━━<br/>query P50/P99 延迟<br/>并发吞吐量"]

    style AUTO fill:#e8f5e9,stroke:#2e7d32
    style MANUAL fill:#fff3e0,stroke:#e65100
```

---

## 八、源码索引

| 能力 | 代码位置 |
|---|---|
| RAGAS 评估主脚本 | `lightrag/evaluation/eval_rag_quality.py` |
| 离线检索检查 | `lightrag/evaluation/offline_retrieval_check.py` |
| 样例测试集 | `lightrag/evaluation/sample_dataset.json` |
| 样例文档 | `lightrag/evaluation/sample_documents/` |
| 样例 oracle | `lightrag/evaluation/sample_retrieval_oracle.json` |
| 评估 README | `lightrag/evaluation/README_EVALUASTION_RAGAS.md` |
| ragas 依赖声明 | `pyproject.toml` 的 `[evaluation]` extra |
| 测试用例 | `tests/evaluation/test_evaluation_offline_retrieval_check.py` |

---

## 九、建议的起步动作

1. **今天**：跑 Layer 1 离线检查（零成本，确认召回逻辑没毛病）
2. **明天**：跑 Layer 2 RAGAS（用自带样例，确认评估流程跑通）
3. **本周**：换成你的业务文档 + 业务问题，建你的测试集
4. **下周**：迭代优化，把 RAGAS 刷到 0.80+
5. **上线前**：补人工盲评 + Langfuse 监控 + Bad case 池

> **核心建议**：**评估先行**。不要先优化再评估，而要先建立评估基线，让每次优化都有数据支撑。项目自带的两套工具让你不用从零搭评估体系，这是借鉴这个项目最大的隐藏价值之一。

---

## 相关文档

- 解析流水线全流程详解：`解析流水线全流程详解.md`
- 技术栈与能力全景：`技术栈与能力全景.md`
- 作为 RAG 基座融合指南：`作为RAG基座与MCP工具的融合指南.md`
- 文档解析能力与输出格式对照：`文档解析能力与输出格式对照.md`
