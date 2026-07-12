# 练习 2 实现计划:SpiceDB 文档级权限

## 目标

在代理层加文档级权限:同一 workspace 内,alice 能看"月报"但看不到"薪资表"。

## 架构(在练习1的代理上加一层)

```
客户端
  │  POST /query  Authorization: Bearer <JWT>
  ▼
代理 (:8000)
  │  1. 验 JWT → user_id
  │  2. user_id → workspace → 后端端口        (练习1已有)
  │  3. 【新增】查询结果回来后,检查 references 里的 file_path
  │     对每个引用文档,问 SpiceDB: user 能 view 这个文档吗?
  │     不能 → 从回答里移除该引用 / 整个拒绝
  │
  ├──► LightRAG server (:9621/:9622)
  └──► SpiceDB (:50051)                    ← 新增的权限引擎
```

## SpiceDB Schema (ReBAC 模型)

```zed
definition user {}

definition document {
    relation viewer: user
    relation owner: user
    permission view = viewer + owner
}
```

## 权限关系(演示场景)

在同一个 workspace `tenant_finance` 里:
- alice 是 `finance_report`(月报)的 viewer → 能查到
- alice **不是** `salary_table`(薪资表)的 viewer → 查不到
- bob 在 `tenant_engineering` workspace → 整个 workspace 级隔离(练习1已有)

## 新增/修改的文件

### 1. `kb_gateway/spicedb_client.py` — SpiceDB 客户端封装(新建)
- `SpiceDBClient` 类:
  - `__init__`:连 gRPC `localhost:50051`,用预设 key
  - `write_schema(schema_text)`:写入上面的 schema
  - `grant_view(document_id, user_id)`:写关系 `document:X#viewer@user:Y`
  - `check_view(user_id, document_id) -> bool`:查 user 能否 view document
  - `lookup_viewable_docs(user_id) -> set[str]`:查 user 能看的所有文档 ID

### 2. `kb_gateway/spicedb_manager.py` — SpiceDB 容器管理(新建)
- `SpiceDBManager` 类(和 BackendManager 同模式):
  - `start()`:docker run 启动 SpiceDB 容器(需要先启个内存 datastore)
  - `stop()`:docker stop / rm
- 用 docker run 起两个容器:
  - `spicedb datastore`(内存 datastore,`--datastore-engine memory`)
  - `spicedb serve`(连 datastore,暴露 50051)

### 3. 修改 `kb_gateway/proxy.py` — 加文档级过滤
- 在 `/query` 和 `/query/data` 的响应处理中:
  - 解析返回的 `references`(每个含 `file_path`)
  - 对每个 `file_path`,调用 `spicedb_client.check_view(user_id, file_path)`
  - 过滤掉无权限的引用
  - 如果全部引用都无权限,返回"无权访问"

### 4. 修改 `kb_gateway/run_proxy.py` — 启动时初始化 SpiceDB
- 启动顺序:SpiceDB 容器 → 写 schema → 写关系 → 后端 → 代理
- 预置演示数据(alice 能看月报,不能看薪资表)

### 5. `kb_gateway/demo_doc_acl.py` — 文档级权限测试(新建)
验证:
1. alice 插入月报 + 薪资表到同一 workspace
2. alice 查月报内容 → 能查到(有权限)
3. alice 查薪资表内容 → 被拒(无权限,即使数据在她的 workspace 里)
4. bob 查任何 finance 数据 → 被 workspace 隔离拦住(练习1已有)

### 6. 更新 `kb_gateway/README.md`

## 关键设计决策

### Post-fetch 过滤的局限性(会在 demo 里演示)
LLM 在生成答案时已经看到了越权文档的内容。代理过滤的是**返回给客户端的引用列表**。
- 如果答案文本里直接包含了薪资数字,代理拦不住文本(只拦了引用)
- 这正是练习 3(深度内核向量过滤)要解决的问题
- demo 里会明确展示这个局限,作为学习要点

### 文档 ID 的映射
- LightRAG 用 `file_path` 标识文档来源(如 `salary_table.txt`)
- SpiceDB 用 `document:salary_table` 作为 object ID
- 映射规则:`file_path` 的 basename → SpiceDB document ID

## 依赖
- `grpcio` + `authzed`(authzed-py):SpiceDB Python 客户端 —— 需要 pip install
- Docker:运行 SpiceDB 容器

## 测试验证
```
✅ alice 查月报 → 能查到(有 view 权限)
✅ alice 查薪资表 → 被拒(无 view 权限,即使数据在同一 workspace)
✅ bob 查任何 finance 数据 → 被 workspace 隔离拦住
✅ 权限关系可通过 SpiceDB 动态修改(加权限后立即生效)
```
