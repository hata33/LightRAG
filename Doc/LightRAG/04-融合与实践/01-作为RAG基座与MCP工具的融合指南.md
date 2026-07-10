# LightRAG 作为 RAG 基座 / MCP 工具的融合指南

**项目**：LightRAG · **版本**：1.5.5 · **日期**：2026-07-08 · **作者**：15531

> 本文档回答：**如何把 LightRAG 作为 RAG 基座融合到一个新项目**，或**把它封装成 MCP 工具**供 AI Agent 调用。覆盖**全生命周期**：解析 → 分块 → 抽取 → 入库（实体/关系/chunk）→ 检索（local/global/hybrid/mix/naive）→ 维护。

---

## 一、先理解 LightRAG 的「契约面」

LightRAG 是个 `@dataclass`，公开 API 都是异步的。这是你能依赖的全部稳定接口（来自 `lightrag.py` 源码）：

| 方法 | 签名要点 | 生命周期环节 |
|---|---|---|
| `__init__` | dataclass 字段：`working_dir`、`kv_storage`、`vector_storage`、`graph_storage`、`doc_status_storage`、`workspace`、`llm_model_func`、`embedding_func` 等 | 实例化 |
| `await initialize_storages()` | **必须**在实例化后调用，否则 `AttributeError: __aenter__` / `KeyError: 'history_messages'` | 启动 |
| `await ainsert(input, ids=, ...)` | SDK 入口，**只用 F（固定 token）分块**；str 或 list[str] | 入库（SDK） |
| `await apipeline_enqueue_documents(..., process_options=...)` | **服务端入口**，支持 F/R/V/P 四种分块策略 | 入库（高级） |
| `await aquery(query, param=QueryParam(mode=...))` | 返回 str 或流式迭代器 | 检索 |
| `await aquery_llm(query, param, ...)` | 返回完整结构化结果（含引用、上下文） | 检索（带溯源） |
| `await ainsert_custom_kg(custom_kg, ...)` | 直接导入已有三元组，跳过抽取 | 入库（自定义KG） |
| `await adelete_by_doc_id(doc_id)` | 按文档删除，级联清理图谱/向量 | 维护 |
| `await aclear_cache()` | 清理 LLM 查询缓存 | 维护 |
| `await finalize_storages()` | 关闭连接池 | 关闭 |

**检索模式**（`QueryParam.mode`）：`local` / `global` / `hybrid` / `naive` / `mix`（默认）/ `bypass`。

**⚠️ 关键陷阱**：
1. `ainsert` 只做 F 分块；要 R/V/P 必须走 `apipeline_enqueue_documents` + `process_options`。
2. 换 embedding 模型后**必须清数据目录**（旧向量空间不匹配），可用 `lightrag-rebuild-vdb`。
3. `embedding_func` 用 `@wrap_embedding_func_with_attrs` 装饰；包装已装饰函数时要访问 `.func`。

---

## 二、存储落库：PostgreSQL + pgvector（你的需求）

LightRAG 的四类存储可各配各的。全 PostgreSQL 方案（**一个 PG 实例用 pgvector + Apache AGE 承载全部四类**）：

### 2.1 准备 PostgreSQL

```bash
# 需要 extension：pgvector（向量）+ Apache AGE（图）
docker run -d --name lightrag-pg \
  -e POSTGRES_USER=rag -e POSTGRES_PASSWORD=rag -e POSTGRES_DB=rag \
  -p 5432:5432 \
  age/pgvector-age  # 带好 pgvector + AGE 的镜像
```

库内启用扩展（首次）：
```sql
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS age;
LOAD 'age';
SET search_path = ag_catalog, "$user", public;
```

### 2.2 两种配置方式（二选一）

**方式 A：环境变量（推荐，容器/CI 友好）** —— 写进 `.env`：

```env
# 四类存储全部指向 PostgreSQL
LIGHTRAG_KV_STORAGE=PGKVStorage
LIGHTRAG_VECTOR_STORAGE=PGVectorStorage
LIGHTRAG_GRAPH_STORAGE=PGGraphStorage
LIGHTRAG_DOC_STATUS_STORAGE=PGDocStatusStorage

# 连接
POSTGRES_HOST=localhost
POSTGRES_PORT=5432
POSTGRES_USER=rag
POSTGRES_PASSWORD=rag
POSTGRES_DATABASE=rag

# pgvector 索引（HNSW 最常用）
POSTGRES_VECTOR_INDEX_TYPE=HNSW
POSTGRES_HNSW_M=16
POSTGRES_HNSW_EF=200

# 模型（嵌入维度必须与 EMBEDDING_MODEL 匹配）
LLM_BINDING=openai
LLM_MODEL=gpt-4o-mini
LLM_BINDING_API_KEY=sk-xxx
EMBEDDING_BINDING=openai
EMBEDDING_MODEL=text-embedding-3-small
EMBEDDING_DIM=1536
EMBEDDING_BINDING_API_KEY=sk-xxx

# 多租户/项目隔离
WORKSPACE=my_project_a
```

**方式 B：构造参数（SDK 内嵌场景）** —— 见第四节代码。

> 也可混搭：例如图用 Neo4j、向量用 Milvus、其余用 PG。只要 4 个 `*_storage` 字段各指各的即可。

### 2.3 数据落在哪里（PG schema 速览）

PG 后端会自动建表，对应生命周期产物：

| PG 表/结构 | 内容 | 生命周期环节 |
|---|---|---|
| `LIGHTRAG_VDB_*` | 实体/关系/chunk 的向量 + 元数据 | 抽取→入库 |
| `LIGHTRAG_DOC_STATUS` | 文档处理状态（PENDING/PROCESSING/PROCESSED/FAILED） | 入库跟踪 |
| `LIGHTRAG_DOC_FULL` | 原文 + 解析产物 | 解析 |
| `LIGHTRAG_CHUNKS` | 分块后的文本块 | 分块→入库 |
| `LIGHTRAG_KV_CACHE` | LLM 响应缓存 | 抽取/检索（省成本） |
| AGE 图（`ag_catalog`） | 实体节点 + 关系边 | 抽取→入库 |

---

## 三、全生命周期：数据怎么流动

把你的问题串起来，对应 LightRAG 内部流程：

```
【你的数据】
     │
     ▼
1. 解析 (parser)        PDF/DOCX/PPTX/XLSX/MD/HTML → 纯文本/Markdown
     │   引擎：legacy / native / mineru / docling
     ▼
2. 分块 (chunker)       长文本 → chunk[]
     │   策略：F token-size / R recursive / V semantic-vector / P paragraph-semantic
     ▼
3. 抽取 (operate)       chunk → 实体/关系/声明（LLM 驱动，EXTRACT 角色）
     │   并发 + gleaning（多轮补抽）
     ▼
4. 入库 (storage)       ┌─ 向量库：实体/关系/chunk 各自 embedding → PG pgvector
                        ├─ 图谱库：节点+边 → PG Apache AGE
                        └─ KV/状态库：原文/块/状态/缓存 → PG 表
     │
     ▼
5. 检索 (operate)       query → 关键词抽取 → 选模式检索
     │   ┌ local   ：实体级
     │   ├ global  ：主题级
     │   ├ hybrid  ：local+global
     │   ├ mix     ：KG + 向量融合（默认）
     │   └ naive   ：纯向量（不走图）
     ▼
6. 生成 (LLM, QUERY角色)  context + query → 答案（可流式、可带引用）
```

**两种调用入口都覆盖这 6 步，区别在分块能力**：
- `ainsert()`：自动跑完 1→4，但**只支持 F 分块**。
- 服务端 `apipeline_enqueue_documents(process_options=...)`：**支持 F/R/V/P**，可逐文档指定。

---

## 四、融合方式一：作为 Python 库嵌入新项目（进程内）

最轻量。你的项目直接 `import` LightRAG，共享进程。

### 4.1 最小可用骨架

```python
# your_project/rag_service.py
import asyncio
from lightrag import LightRAG, QueryParam
from lightrag.llm.openai import gpt_4o_mini_complete, openai_embed
from lightrag.utils import EmbeddingFunc, wrap_embedding_func_with_attrs
import numpy as np

# 自定义嵌入：务必装饰，否则维度/token 信息丢失
@wrap_embedding_func_with_attrs(embedding_dim=1536, max_token_size=8192)
async def my_embed(texts: list[str]) -> np.ndarray:
    return await openai_embed(texts, model="text-embedding-3-small")

async def main():
    rag = LightRAG(
        working_dir="./rag_storage",
        # —— 存储：全 PostgreSQL ——
        kv_storage="PGKVStorage",
        vector_storage="PGVectorStorage",
        graph_storage="PGGraphStorage",
        doc_status_storage="PGDocStatusStorage",
        workspace="my_project_a",          # 数据隔离
        # —— 模型 ——
        llm_model_func=gpt_4o_mini_complete,
        embedding_func=my_embed,
        # —— 检索调参（可选）——
        top_k=60,
        chunk_top_k=20,
    )

    await rag.initialize_storages()        # 必须调用

    # 1) 入库（F 分块）
    await rag.ainsert("你的文档内容...", ids=["doc-1"])

    # 2) 检索（五种模式任选）
    answer = await rag.aquery(
        "你的问题",
        param=QueryParam(mode="hybrid"),
    )
    print(answer)

    await rag.finalize_storages()          # 优雅关闭

asyncio.run(main())
```

连接参数通过 `.env`（推荐）或环境变量传入。构造参数优先级：**构造参数 > 环境变量 > 默认值**。

### 4.2 嵌入到 Web 框架（FastAPI / Django / 等）

**关键：单例 + 生命周期钩子**。LightRAG 实例重，不要每请求新建。

```python
# your_project/app.py  （FastAPI 示例）
from contextlib import asynccontextmanager
from fastapi import FastAPI
from lightrag import LightRAG, QueryParam

rag: LightRAG | None = None

@asynccontextmanager
async def lifespan(app: FastAPI):
    global rag
    rag = LightRAG(working_dir="./rag_storage", /* ... */)
    await rag.initialize_storages()
    yield
    await rag.finalize_storages()      # 应用关闭时释放连接池

app = FastAPI(lifespan=lifespan)

@app.post("/ask")
async def ask(q: str, mode: str = "hybrid"):
    return {"answer": await rag.aquery(q, param=QueryParam(mode=mode))}
```

> **并发约束**：LightRAG 的锁绑定在 `initialize_storages()` 所在的事件循环。若你在多进程/多线程里跑，必须保证调用都在**同一个 event loop** 上，否则会报 `Lock bound to a different loop`。多 worker 用 Gunicorn 时每个 worker 各持一个 LightRAG 实例。

### 4.3 想用高级分块（R/V/P）？走 pipeline

```python
from lightrag.parser import ProcessOptions  # 分块策略在此指定

await rag.apipeline_enqueue_documents(
    file_paths=["paper.pdf"],
    process_options=ProcessOptions(chunk_strategy="V"),  # V=语义向量
)
```

---

## 五、融合方式二：作为独立服务（HTTP API 调用）

你的项目**不引入 LightRAG 依赖**，只起一个 `lightrag-server`，通过 REST 调用。适合异构语言 / 微服务。

```bash
# 1. 配好 .env（见 2.2）
# 2. 启动
PYTHONUTF8=1 uv run lightrag-server        # 或 docker compose up
```

你的项目任意语言调用：

```python
import httpx

BASE = "http://localhost:9621"
TOKEN = "<JWT>"

async with httpx.AsyncClient(timeout=120) as c:
    # 入库
    await c.post(f"{BASE}/documents/text",
        headers={"Authorization": f"Bearer {TOKEN}"},
        json={"text": "...", "track_id": "t1"})

    # 检索（流式）
    async with c.stream("POST", f"{BASE}/query/stream",
        headers={"Authorization": f"Bearer {TOKEN}"},
        json={"query": "...", "mode": "hybrid"}) as r:
        async for line in r.aiter_lines():
            print(line)
```

**REST 端点速查**：

| 端点 | 作用 |
|---|---|
| `POST /documents/text` `POST /documents/texts` `POST /documents/upload` | 入库（文本/批量/文件） |
| `POST /documents/scan` | 扫描目录自动入库 |
| `POST /query` `POST /query/stream` `POST /query/data` | 检索（普通/流式/仅数据） |
| `GET/POST /documents/*` | 文档管理（列表/删除/状态） |
| `/graph/*` | 知识图谱 CRUD |
| `/api/chat` (Ollama 兼容) | 第三方工具可直接对接 |

> **方式一 vs 方式二取舍**：进程内嵌入延迟最低、调用最顺，但绑定 Python + 共享 GIL；HTTP 解耦最彻底，适合多语言/独立扩缩容，代价是多一跳网络和 JWT 管理。

---

## 六、融合方式三：封装为 MCP 工具（给 AI Agent 用）

LightRAG **目前没有官方 MCP server**（仅测试中提到 Ollama API）。但它的接口干净，封装成 MCP 工具很直接。

### 6.1 MCP 是什么 / 为什么合适

MCP（Model Context Protocol）让 AI Agent（Claude Desktop、Cursor 等）通过标准协议调用外部工具。把 LightRAG 封装成 MCP，Agent 就能「读文档进知识库」「按模式提问」「删文档」——把 RAG 当成 Agent 的一个能力插件。

### 6.2 最小 MCP Server（Python，官方 SDK）

```python
# mcp_lightrag/server.py
# pip install "mcp[cli]"
import os
from contextlib import asynccontextmanager
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent
import anyio
from lightrag import LightRAG, QueryParam

server = Server("lightrag-rag")
rag: LightRAG | None = None
_rag_lock = anyio.Lock()

@server.list_tools()
async def list_tools() -> list[Tool]:
    return [
        Tool(
            name="rag_insert",
            description="把文档/文本写入知识图谱（解析→分块→抽取→入库）",
            inputSchema={
                "type": "object",
                "properties": {
                    "text": {"type": "string", "description": "要入库的文本内容"},
                    "doc_id": {"type": "string", "description": "可选，自定义文档ID"}
                },
                "required": ["text"],
            },
        ),
        Tool(
            name="rag_query",
            description="对知识图谱检索问答。mode: local细节|global综述|hybrid兼顾|mix图+向量(默认)|naive纯向量",
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "mode": {
                        "type": "string",
                        "enum": ["local","global","hybrid","mix","naive"],
                        "default": "mix",
                    },
                },
                "required": ["query"],
            },
        ),
        Tool(
            name="rag_delete_doc",
            description="按文档ID删除知识（级联清理图谱+向量）",
            inputSchema={
                "type": "object",
                "properties": {"doc_id": {"type": "string"}},
                "required": ["doc_id"],
            },
        ),
    ]

@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    global rag
    async with _rag_lock:
        if rag is None:
            rag = LightRAG(working_dir=os.getenv("RAG_DIR", "./rag_storage"))
            await rag.initialize_storages()

    if name == "rag_insert":
        doc_id = arguments.get("doc_id")
        await rag.ainsert(arguments["text"], ids=[doc_id] if doc_id else None)
        return [TextContent(type="text", text=f"已入库: {doc_id or '自动ID')}")]

    if name == "rag_query":
        mode = arguments.get("mode", "mix")
        answer = await rag.aquery(arguments["query"],
                                  param=QueryParam(mode=mode))
        return [TextContent(type="text", text=str(answer))]

    if name == "rag_delete_doc":
        await rag.adelete_by_doc_id(arguments["doc_id"])
        return [TextContent(type="text", text=f"已删除: {arguments['doc_id']}")]

    raise ValueError(f"未知工具: {name}")

async def main():
    async with stdio_server() as (read, write):
        await server.run(read, write, server.create_initialization_options())

if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
```

### 6.3 在客户端注册（Claude Desktop 示例）

`claude_desktop_config.json`：

```json
{
  "mcpServers": {
    "lightrag-rag": {
      "command": "python",
      "args": ["-m", "mcp_lightrag.server"],
      "env": {
        "RAG_DIR": "/path/to/rag_storage",
        "WORKSPACE": "agent_kb",
        "LLM_BINDING_API_KEY": "sk-xxx",
        "POSTGRES_HOST": "localhost",
        "POSTGRES_USER": "rag",
        "POSTGRES_PASSWORD": "rag",
        "POSTGRES_DATABASE": "rag",
        "LIGHTRAG_KV_STORAGE": "PGKVStorage",
        "LIGHTRAG_VECTOR_STORAGE": "PGVectorStorage",
        "LIGHTRAG_GRAPH_STORAGE": "PGGraphStorage",
        "LIGHTRAG_DOC_STATUS_STORAGE": "PGDocStatusStorage"
      }
    }
  }
}
```

注册后 Agent 自动获得三个工具：`rag_insert` / `rag_query` / `rag_delete_doc`。

### 6.4 MCP vs 进程内 vs HTTP —— 怎么选

| 场景 | 推荐 |
|---|---|
| 你自己写的应用，要极致性能、同进程 | **进程内嵌入**（方式一） |
| 异构技术栈 / 多语言 / 微服务 | **HTTP API**（方式二） |
| 让 AI Agent（Claude/Cursor）把 LightRAG 当工具用 | **MCP**（方式三） |
| 多个不同 AI 客户端复用同一知识库 | **MCP server 常驻**（用 HTTP transport 而非 stdio） |

### 6.5 进阶：更丰富的 MCP 工具集

可继续封装的能力（都有现成 API）：

| 工具 | 底层 API | 用途 |
|---|---|---|
| `rag_insert_custom_kg` | `ainsert_custom_kg` | 导入已抽取的三元组（跳过 LLM 抽取，省钱） |
| `rag_query_with_refs` | `aquery_llm` | 返回带引用来源的答案 |
| `rag_get_knowledge_graph` | `/graph/*` | 取子图给 Agent 可视化/推理 |
| `rag_clear_cache` | `aclear_cache` | 运维 |
| `rag_batch_insert` | `ainsert(list)` | 批量入库 |

> **MCP 封装最佳实践**：① 用锁保护共享 `rag` 实例；② `initialize_storages()` 只调一次；③ 工具描述写清楚 mode 区别（Agent 据此选模式）；④ 长任务（大批入库）考虑加进度提示或超时。

---

## 七、多租户 / 多项目隔离

用 `workspace` 字段实现，**同一套存储、不同逻辑分区**：

```python
# 项目A
rag_a = LightRAG(..., workspace="project_a")
# 项目B（同一个PG，数据互不干扰）
rag_b = LightRAG(..., workspace="project_b")
```

隔离机制因后端而异（PG：workspace 列过滤；Qdrant：payload 分区；文件型：子目录）。**切 workspace 不影响已有数据**。

---

## 八、维护与运维（生命周期闭环）

| 诉求 | 做法 |
|---|---|
| 切换/升级 embedding 模型 | 清数据目录（保留 `kv_store_llm_response_cache.json` 省钱）→ 改模型 → 重新入库 |
| 重建向量库 | `lightrag-rebuild-vdb` |
| 清 LLM 缓存 | `lightrag-clean-llmqc` |
| 按文档清理 | `adelete_by_doc_id` |
| 数据迁移 | `storage_migrations.py` 自动处理历史格式 |
| 监控 LLM 调用 | 装 langfuse，配 `[observability]` extra |
| 评估检索质量 | ragas + `[evaluation]` extra |

---

## 九、选型决策树

```
你要怎么用 LightRAG？
│
├─ 作为应用内部组件（自己代码调用）
│   ├─ 同进程、要最低延迟 ──→ 方式一：Python 库嵌入
│   └─ 多语言/独立部署    ──→ 方式二：HTTP API
│
├─ 给 AI Agent 当工具
│   └─ Claude/Cursor 等 MCP 客户端 ──→ 方式三：MCP Server
│
└─ 数据存哪？
    ├─ 要生产级/事务/SQL ──→ PostgreSQL（pgvector + AGE，全合一）
    ├─ 超大规模图        ──→ Neo4j
    ├─ 超大规模向量      ──→ Milvus / Qdrant
    └─ 原型/单机         ──→ JSON + NanoVectorDB + NetworkX（零配置）
```

---

## 十、起步清单（3 步跑通）

1. **配存储**：复制 `env.example → .env`，按 2.2 节改 4 个 `LIGHTRAG_*_STORAGE` + `POSTGRES_*` + 模型 key。
2. **选融合方式**：方式一/二/三，抄对应骨架代码。
3. **验证全链路**：`ainsert("测试文本")` → `aquery("测试问题", mode="hybrid")` 出答案 → PG 里查到向量/节点/边。

跑通这三步，整个生命周期（解析→分块→抽取→入库→检索→维护）即全部贯通。

---

## 相关文档

- 技术栈与能力全景：`技术栈与能力全景.md`
- 核心执行链路与架构（源码视角）：`核心执行链路与架构速览文档.md`
- 能力与使用指南（使用视角）：`能力与使用指南.md`
- 完整配置与部署：`cn/07-部署手册与用户手册.md`、`配置.md`、`启动.md`
