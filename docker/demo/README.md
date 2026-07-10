# LightRAG Demo：Docker 数据库 + 本地前后端

> Docker 只启动 PostgreSQL + Neo4j 两个数据库，前后端代码本地手动启动。改代码即时生效，最灵活。

## 架构

```
┌──────────────────────────────────────────────────────┐
│                   本地手动启动                         │
│  ┌─────────────────┐    ┌────────────────────────┐  │
│  │ 后端 lightrag    │    │ 前端 pnpm dev           │  │
│  │ uv run server   │◄───│ Vite :5173              │  │
│  │ FastAPI :9621   │    │ → 代理 /api 到 :9621    │  │
│  └────┬──────┬─────┘    └────────────────────────┘  │
│       │      │                                       │
└───────┼──────┼───────────────────────────────────────┘
        │      │
   ┌────▼──┐ ┌─▼──────────────────────────────────┐
   │Docker │ │ 模型 API (192.168.1.161 内网)       │
   │       │ │ LLM:10001 Embed:10004 Rerank:10005 │
   │ ┌───┐ │ └────────────────────────────────────┘
   │ │PG │ │
   │ │:5432│
   │ └───┘ │
   │ ┌───┐ │
   │ │Neo│ │
   │ │:7474│
   │ │:7687│
   │ └───┘ │
   └───────┘
```

## 快速开始

### 第 1 步：启动数据库（Docker）

```bash
# 一键启动 PG + Neo4j（推荐）
bash docker/demo/start-demo.sh

# 或手动
cp .env.demo .env
docker compose -f docker-compose-demo.yml up -d
```

### 第 2 步：启动后端（终端 1）

```bash
# 在项目根目录
PYTHONUTF8=1 uv run lightrag-server
```

### 第 3 步：启动前端（终端 2）

```bash
cd lightrag_webui
pnpm dev
```

### 第 4 步：访问

| 地址 | 用途 |
|---|---|
| `http://localhost:5173` | 前端 WebUI（文档管理 + 图谱可视化 + 检索问答） |
| `http://localhost:9621/docs` | 后端 API 文档 |
| `http://localhost:7474` | Neo4j Browser（neo4j / neo4j123）查看知识图谱 |
| `localhost:5432` | PostgreSQL（rag / rag123 / rag），可用 DBeaver 连接 |

## 验证

```bash
# 1. 数据库健康
docker exec demo-postgres pg_isready -U rag -d rag
docker exec demo-neo4j cypher-shell -u neo4j -p neo4j123 "RETURN 1"

# 2. 后端健康
curl http://localhost:9621/health

# 3. 上传文档后查 PG 表
docker exec demo-postgres psql -U rag -d rag -c "\dt"

# 4. 查 Neo4j 图谱
docker exec demo-neo4j cypher-shell -u neo4j -p neo4j123 \
  "MATCH (n)-[r]->(m) RETURN n.entity_name, r.description, m.entity_name LIMIT 10"

# 5. 测试检索
curl -X POST http://localhost:9621/query \
  -H "Content-Type: application/json" \
  -d '{"query":"你的问题","mode":"mix"}'
```

## Neo4j Browser 图形化操作

浏览器打开 `http://localhost:7474`，登录 `neo4j` / `neo4j123`：

```cypher
-- 查看知识图谱（节点 + 关系网络）
MATCH (n)-[r]->(m) RETURN n, r, m LIMIT 100

-- 统计节点和边
MATCH (n) RETURN count(n) AS nodes
MATCH ()-[r]->() RETURN count(r) AS relationships

-- 查找某实体的所有关系
MATCH (n)-[r]-(m) WHERE n.entity_name CONTAINS '张三'
RETURN n, r, m

-- 查看节点属性
MATCH (n) RETURN n.entity_name, n.description, n.source_id LIMIT 20
```

## 停止

```bash
# 停数据库（保留数据）
docker compose -f docker-compose-demo.yml down

# 停数据库 + 删全部数据（重来）
docker compose -f docker-compose-demo.yml down -v

# 前端/后端: Ctrl+C 即可
```

## 常见问题

| 问题 | 原因 | 解决 |
|---|---|---|
| 后端连不上 PG/Neo4j | .env 用了容器名 | 确认 `POSTGRES_HOST=localhost`、`NEO4J_URI=bolt://localhost:7687` |
| 后端报 GBK 编码错误 | Windows 中文控制台 | 用 `PYTHONUTF8=1` 前缀启动 |
| 查询很慢 | LLM API 慢 | 开流式输出 + 查询缓存 |
| Neo4j 连接失败 | 密码不对 | 确认 `NEO4J_PASSWORD=neo4j123` |
| 换了 embedding 模型 | 向量维度不匹配 | `docker compose down -v` 清库重来 |

## 文件清单

```
docker-compose-demo.yml   # 2 容器编排（postgres + neo4j）
.env.demo                 # Demo 配置（PG + Neo4j + 内网 API 模型）
docker/demo/
├── init.sql              # PG 初始化（pgvector 扩展）
├── start-demo.sh         # 启动数据库 + 验证脚本
└── README.md             # 本文件
```
