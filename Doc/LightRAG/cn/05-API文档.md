# API 文档

**项目名称**：LightRAG
**作者**：15531
**日期**：2026-07-05
**版本**：1.26.705.8600（API 版本 0315）

## 变更日志

| 版本 | 日期 | 作者 | 变更内容 |
|------|------|------|----------|
| 1.26.705.8600 | 2026-07-05 | 15531 | 初始版本，依据 `lightrag/api/routers` 源码整理 RESTful 接口 |

---

## 一、接口总览

API 服务由 FastAPI 实现，入口 `lightrag/api/lightrag_server.py:create_app()`，挂载四组路由：

| 路由组 | 前缀 | 工厂函数 | 职责 |
|--------|------|----------|------|
| 文档管理 | `/documents` | `create_document_routes` | 摄入、删除、状态、流水线控制 |
| 查询问答 | `/query` | `create_query_routes` | 标准查询、流式查询、纯检索 |
| 知识图谱 | `/graph`、`/graphs` | `create_graph_routes` | 实体/关系/标签 CRUD 与合并 |
| Ollama 兼容 | `/api` | `ollama_api.router` | Ollama 协议兼容 |
| 鉴权 | `/` | 主 app | 登录、鉴权状态 |

**统一鉴权**：所有受保护接口通过 `Depends(combined_auth)` 注入。多用户模式需 JWT；未配置 `LIGHTRAG_API_KEY` 时签发访客令牌。

### 关键接口一览

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/login` | 登录获取 JWT |
| GET | `/auth-status` | 查询鉴权模式 |
| POST | `/documents/text` | 插入单段文本 |
| POST | `/documents/texts` | 批量插入文本 |
| POST | `/documents/upload` | 上传文件 |
| POST | `/documents/scan` | 扫描目录入库 |
| GET | `/documents` | 文档状态列表 |
| POST | `/documents/paginated` | 分页文档列表 |
| GET | `/documents/status_counts` | 状态计数 |
| GET | `/documents/pipeline_status` | 流水线状态 |
| GET | `/documents/track_status/{track_id}` | 按追踪 ID 查状态 |
| DELETE | `/documents/delete_document` | 删除单文档 |
| POST | `/documents/reprocess_failed` | 重处理失败文档 |
| POST | `/documents/cancel_pipeline` | 取消流水线 |
| POST | `/documents/clear_cache` | 清理 LLM 缓存 |
| DELETE | `/documents` | 清空全部存储 |
| POST | `/query` | 标准查询 |
| POST | `/query/stream` | 流式查询（SSE） |
| POST | `/query/data` | 仅检索（不生成） |
| GET | `/graph/label/list` | 实体类型列表 |
| GET | `/graph/label/popular` | 热门类型 |
| GET | `/graph/label/search` | 类型搜索 |
| GET | `/graphs` | 完整图谱（可视化） |
| GET | `/graph/entity/exists` | 实体存在性 |
| POST | `/graph/entity/create` | 创建实体 |
| POST | `/graph/entity/edit` | 编辑实体 |
| POST | `/graph/entities/merge` | 合并实体 |
| DELETE | `/graph/entity` | 删除实体 |
| POST | `/graph/relation/create` | 创建关系 |
| POST | `/graph/relation/edit` | 编辑关系 |
| DELETE | `/graph/relation` | 删除关系 |
| GET | `/api/version` | Ollama 版本 |
| GET | `/api/tags` | 模型列表 |
| POST | `/api/chat` | Ollama 兼容对话 |
| POST | `/api/generate` | Ollama 兼容生成 |

---

## 二、接口详情

### 2.1 POST `/query` — 标准查询

**方法签名**（路由处理）：
```python
async def query_query(request: QueryRequest, rag: LightRAG, auth_dep) -> QueryResponse
```

**请求体 `QueryRequest` 关键字段**：

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| query | string | 是 | 用户问题 |
| mode | string | 否 | `local/global/hybrid/naive/mix/bypass`，默认 `mix` |
| stream | bool | 否 | 是否流式（此接口建议用 `/query/stream`） |
| only_need_context | bool | 否 | 仅返回上下文不生成 |
| history_turns | int | 否 | 历史对话轮数 |
| top_k | int | 否 | 召回数量 |
| user_prompt | string | 否 | 自定义系统提示词 |

**返回值**：生成结果文本 + 检索上下文（实体/关系/chunk）+ 引用。

### 2.2 POST `/query/stream` — 流式查询
**返回**：SSE 流，逐 token 输出。请求体同 `/query`。

### 2.3 POST `/query/data` — 仅检索
**返回**：`{entities, relationships, chunks}`，不调用生成 LLM，适合评估与可视化。

### 2.4 POST `/documents/text` — 插入文本

**请求体**：

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| text | string | 是 | 文本内容 |
| ids | list[string] | 否 | 自定义文档 ID |
| chunking_strategy | string | 否 | `chunking/by_token_size` 等 |
| process_options | object | 否 | 分块策略选项（F/R/V/P） |
| track_id | string | 否 | 追踪 ID |

**返回**：`track_id`，用于后续 `/documents/track_status/{track_id}` 轮询。

**调用示例**：
```bash
curl -X POST http://localhost:9621/documents/text \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer <JWT>" \
  -d '{"text":"LightRAG 是一个轻量级 RAG 框架","track_id":"t-001"}'
```

### 2.5 POST `/documents/upload` — 文件上传
**multipart/form-data**：`file` 字段。支持 PDF/DOCX/PPTX/XLSX/MD/TXT 等。

### 2.6 POST `/documents/scan` — 目录扫描入库
**请求体**：`input_dir` 等参数，扫描目录内新文件并入队。

### 2.7 GET `/documents/track_status/{track_id}`
**返回**：该摄入任务的当前状态（PENDING/PROCESSING/PROCESSED/FAILED）。

### 2.8 GET `/graphs`
**返回**：知识图谱节点与边数据，供 Web UI Sigma.js 可视化。

### 2.9 POST `/graph/entity/create` | `/graph/entity/edit` | `/graph/entities/merge`
**请求体**：实体名、类型、描述、source_id。合并接口接收 `source_name` 与 `target_name`。

### 2.10 DELETE `/documents/delete_document`
**请求体**：`doc_id`。删除文档并自动重连受影响关系、清理向量。

---

## 三、Python SDK 调用示例

### 3.1 摄入与查询
```python
import asyncio
from lightrag import LightRAG, QueryParam

async def main():
    rag = LightRAG(working_dir="./rag_storage")
    await rag.initialize_storages()

    # 摄入（默认固定 token 分块）
    await rag.ainsert("LightRAG 将知识图谱与 LLM 结合，支持双层检索。")

    # 查询
    result = await rag.aquery("LightRAG 的检索范式是什么？", param=QueryParam(mode="hybrid"))
    print(result)

asyncio.run(main())
```

### 3.2 自定义分块策略与服务端路径
服务端 `apipeline_enqueue_documents` 支持 `process_options`（F/R/V/P 四策略）：
```python
await rag.apipeline_enqueue_documents(
    input=docs,
    process_options={"chunking_strategy": "V"},  # 语义向量分块
)
```

### 3.3 自定义知识图谱注入
```python
await rag.ainsert_custom_kg(
    entities=[{"entity_name": "LightRAG", "entity_type": "framework", "description": "..."}],
    relationships=[{"src_id": "LightRAG", "tgt_id": "HKUDS", "description": "developed by"}],
)
```

---

## 四、异常场景

| 场景 | 返回结果 | 说明 |
|------|----------|------|
| 未携带/无效 JWT | `401 Unauthorized` | 多用户模式强制鉴权 |
| 请求体校验失败 | `422 Validation Error` | Pydantic 字段校验 |
| LLM 调用超时 | `504 / 错误体` | 由 `default_llm_timeout` 控制，含重试 |
| 文档重复摄入 | 返回已有 `track_id` | 幂等控制 |
| 流水线已取消 | 状态置回 + 提示 | `/cancel_pipeline` 后再操作 |
| 删除不存在的实体 | `404 / 错误体` | 图存储未命中 |
| LLM 输出非法 JSON | 内部 `json_repair` 修复 | 失败则重试 |
| 存储后端连接失败 | `500 / 错误体` | 检查对应连接环境变量 |

---

## 五、CLI 入口

| 命令 | 用途 |
|------|------|
| `lightrag-server` | 启动 API 服务（uvicorn） |
| `lightrag-gunicorn` | 以 Gunicorn 启动（生产） |
| `lightrag-hash-password` | 生成 bcrypt 密码哈希 |
| `lightrag-download-cache` | 下载离线缓存 |
| `lightrag-clean-llmqc` | 清理 LLM 查询缓存 |
| `lightrag-rebuild-vdb` | 重建向量库 |
