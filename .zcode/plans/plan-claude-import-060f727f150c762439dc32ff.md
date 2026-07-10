# LightRAG Demo 方案：PG + Neo4j + Ollama + Docker Compose

## 核心结论：改造量极小

> **几乎零代码改造**。LightRAG 原生支持 PG（KV/向量/状态）+ Neo4j（图）+ Ollama（模型），全部通过 `.env` 配置切换。内置 WebUI 零改造。实际工作量 = **写一个精简 compose 文件 + 改 .env + 准备 Ollama 模型**，约半天。

---

## 一、需要部署的东西（4 个容器）

| 容器 | 镜像 | 端口 | 作用 |
|---|---|---|---|
| **PostgreSQL** | `pgvector/pgvector:pg16` | 5432 | KV/向量/状态存储（pgvector 扩展） |
| **Neo4j** | `neo4j:5-community` | 7687 | 知识图谱存储 |
| **Ollama** | `ollama/ollama:latest` | 11434 | 本地 LLM + Embedding |
| **LightRAG** | `ghcr.io/hkuds/lightrag:latest` | 9621 | API 服务 + 内置 WebUI |

> PG 用 `pg16` 而非 compose-full 里的 `pg18`——更稳定且兼容 pgvector。图用 Neo4j 所以 PG 不需要 Apache AGE 扩展。

---

## 二、存储分工

```
PostgreSQL (pgvector):
  ├── KV_STORAGE        = PGKVStorage       (文档/块/实体/关系/缓存)
  ├── VECTOR_STORAGE    = PGVectorStorage    (实体/关系/chunk 向量)
  └── DOC_STATUS_STORAGE= PGDocStatusStorage (文档状态)

Neo4j:
  └── GRAPH_STORAGE     = Neo4JStorage       (知识图谱节点+边)

Ollama:
  ├── LLM               = qwen2.5:7b         (抽取/问答/关键词)
  └── Embedding         = bge-m3:567m        (1024维向量)
```

---

## 三、.env 配置（核心改动，全部在此）

```env
# ─── 存储绑定 ───
LIGHTRAG_KV_STORAGE=PGKVStorage
LIGHTRAG_VECTOR_STORAGE=PGVectorStorage
LIGHTRAG_GRAPH_STORAGE=Neo4JStorage
LIGHTRAG_DOC_STATUS_STORAGE=PGDocStatusStorage

# ─── PostgreSQL ───
POSTGRES_HOST=postgres
POSTGRES_PORT=5432
POSTGRES_USER=rag
POSTGRES_PASSWORD=rag123
POSTGRES_DATABASE=rag
POSTGRES_VECTOR_INDEX_TYPE=HNSW

# ─── Neo4j ───
NEO4J_URI=bolt://neo4j:7687
NEO4J_USERNAME=neo4j
NEO4J_PASSWORD=neo4j123

# ─── Ollama LLM ───
LLM_BINDING=ollama
LLM_BINDING_HOST=http://ollama:11434
LLM_MODEL=qwen2.5:7b
LLM_BINDING_API_KEY=ollama

# ─── Ollama Embedding ───
EMBEDDING_BINDING=ollama
EMBEDDING_BINDING_HOST=http://ollama:11434
EMBEDDING_MODEL=bge-m3:567m
EMBEDDING_DIM=1024
EMBEDDING_BINDING_API_KEY=ollama

# ─── 认证（Demo 可选）───
# AUTH_ACCOUNTS=admin:admin123
# TOKEN_SECRET=your-secret-key-change-me

# ─── 其他 ───
WORKSPACE=demo
HOST=0.0.0.0
PORT=9621
```

---

## 四、docker-compose 文件结构

新增一个精简的 `docker-compose-demo.yml`（不复用 compose-full，那个带 Milvus+vLLM 太重）：

```yaml
services:
  postgres:        # pgvector/pgvector:pg16, 初始化 rag 库 + vector 扩展
  neo4j:           # neo4j:5-community, 设密码
  ollama:          # ollama/ollama:latest, 需 GPU（或 CPU 慢跑）
  lightrag:        # ghcr.io/hkuds/lightrag:latest, env_file 指向 .env
    depends_on: [postgres, neo4j, ollama]
    ports: ["9621:9621"]
```

**关键细节**：
- PG 初始化脚本自动 `CREATE EXTENSION vector`
- Neo4j 设 `NEO4J_AUTH=neo4j/neo4j123`
- Ollama 需要 volume 挂载模型存储 + 预拉取模型（`ollama pull qwen2.5:7b` + `ollama pull bge-m3`）
- LightRAG `depends_on` 三个服务的 healthcheck

---

## 五、Ollama 模型准备（一次性）

```bash
# 启动 ollama 容器后，拉取两个模型（约 6-7GB 下载）
docker exec -it demo-ollama ollama pull qwen2.5:7b     # ~4.7GB
docker exec -it demo-ollama ollama pull bge-m3          # ~1.2GB
```

> 如果没 GPU，qwen2.5:7b 在 CPU 上也能跑但慢。Demo 验证流水线足够。

---

## 六、启动与验证流程

```bash
# 1. 启动全部服务
docker compose -f docker-compose-demo.yml up -d

# 2. 等 Ollama 拉模型（首次）
docker exec -it demo-ollama ollama pull qwen2.5:7b
docker exec -it demo-ollama ollama pull bge-m3

# 3. 验证 LightRAG 健康
curl http://localhost:9621/health
# → {"status":"healthy"}

# 4. 浏览器打开 WebUI
# http://localhost:9621/webui/

# 5. 上传一份文档测试全流水线
#    WebUI → 文档 → 上传 → 等待 PROCESSED

# 6. 验证数据落库
docker exec demo-postgres psql -U rag -d rag -c "\dt"    # 应有 LIGHTRAG_* 表
docker exec demo-neo4j cypher-shell -u neo4j -p neo4j123 \
  "MATCH (n) RETURN count(n)"                              # 应有节点

# 7. 测试检索
curl -X POST http://localhost:9621/query \
  -H "Content-Type: application/json" \
  -d '{"query":"你的问题","mode":"mix"}'
```

---

## 七、改造量评估

| 工作 | 代码改动？ | 耗时 | 说明 |
|---|---|---|---|
| 写 docker-compose-demo.yml | ❌ 新文件非改代码 | 15min | 4 个 service + healthcheck |
| 改 .env | ❌ 纯配置 | 10min | 改存储绑定 + Ollama + PG/Neo4j 连接 |
| 准备 Ollama 模型 | ❌ 命令 | 20min | 拉两个模型（看网速） |
| 验证全流水线 | ❌ | 30min | 上传文档 → 检索 → 查库 |
| **总计** | **零代码改动** | **~1.5h** | |

> 项目已原生支持这套组合（PG 四合一存储 + Neo4j 图 + Ollama 模型），`docker-compose-full.yml` 就是参考。我们只是去掉 Milvus/vLLM（用 Ollama 替代），写个精简版。

---

## 八、潜在风险与注意点

| 风险 | 说明 | 应对 |
|---|---|---|
| **Ollama 无 GPU 慢** | qwen2.5:7b CPU 推理每次几秒~十几秒 | Demo 可接受；或换更小模型 `qwen2.5:3b` |
| **Ollama 模型未预拉** | 首次查询会触发拉取，超时 | 容器启动后先 `ollama pull` |
| **PG vector 扩展** | 需要在 rag 库手动 `CREATE EXTENSION vector` | compose 里用 init SQL 自动建 |
| **Neo4j 密码** | 首次必须改密码 | compose 设 `NEO4J_AUTH` |
| **WebUI 内嵌路径** | 内置 WebUI 在 `/webui/` 不是根路径 | 浏览器访问 `:9621/webui/` |
| **embedding 维度** | bge-m3=1024，换模型必须清库重建 | EMBEDDING_DIM=1024 要匹配 |

---

## 九、交付物清单

执行阶段产出：
1. **`docker-compose-demo.yml`** — 4 容器编排
2. **`.env`** — 更新后的配置（PG + Neo4j + Ollama 绑定）
3. **`init.sql`**（可选）— PG 初始化 vector 扩展
4. **启动/验证脚本**（可选）— 一键 pull 模型 + 健康检查
