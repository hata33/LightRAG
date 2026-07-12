# kb_gateway — 多租户知识库网关

> 练习 1 + 练习 2:多 workspace 隔离 + 文档级权限控制

设计文档:[`Doc/LightRAG/04-融合与实践/05-企业级权限控制技术选型.md`](../Doc/LightRAG/04-融合与实践/05-企业级权限控制技术选型.md)

---

## 两种权限粒度

```
┌─ 练习 1:workspace 级隔离 ────────────────────────┐
│  alice → tenant_finance     (物理隔离,不可能串)    │
│  bob   → tenant_engineering                       │
└───────────────────────────────────────────────────┘
          │
          ▼ 同一 workspace 内,再做文档级控制
┌─ 练习 2:文档级 ACL (ReBAC) ──────────────────────┐
│  tenant_finance 内:                               │
│    finance_report.txt   → alice 有 view 权限 ✅    │
│    salary_table.txt     → alice 无权限 ❌          │
│    (两份文档在同一 workspace,但权限不同)          │
└───────────────────────────────────────────────────┘
```

## 架构

```
客户端
  │  POST /query  Authorization: Bearer <JWT>
  ▼
代理 (:8000)                          ← kb_gateway/proxy.py
  │  1. 验 JWT → user_id
  │  2. user_id → workspace → 后端端口  (练习1: workspace 路由)
  │  3. 后端返回结果后:
  │     对每个引用文档,问权限引擎       (练习2: 文档级 ACL)
  │     user 能 view 吗?不能 → 移除引用
  │
  ├──► LightRAG server (:9621/:9622)
  └──► 权限引擎 (内存 ReBAC)            ← kb_gateway/spicedb_client.py
```

## 快速开始

### 一键启动(包含 workspace 隔离 + 文档级 ACL)

```bash
cd D:\Project\LightRAG

# 终端 1:启动全套
.venv/Scripts/python -m kb_gateway.run_proxy

# 终端 2:测试
.venv/Scripts/python -m kb_gateway.demo_doc_acl
```

### 各测试脚本

| 脚本 | 测试内容 |
|---|---|
| `demo.py` | 练习1 SDK 模式:多 workspace 隔离(直接管理 LightRAG 实例) |
| `demo_proxy.py` | 练习1 代理模式:JWT 认证 + workspace 路由(HTTP 端到端) |
| `demo_doc_acl.py` | 练习2 文档级 ACL:同一 workspace 内不同文档不同权限 |

## 文件结构

```
kb_gateway/
├── __init__.py
├── workspace_manager.py   ← 练习1 SDK 模式:多 LightRAG 实例池
├── demo.py                ← 练习1 SDK 模式:演示脚本
├── auth.py                ← JWT 签发/验证 + 用户表
├── backend_manager.py     ← subprocess 管理后端 server 进程
├── proxy.py               ← FastAPI 反向代理 + JWT + workspace 路由 + 文档ACL
├── run_proxy.py           ← 一键启动(含权限引擎初始化)
├── spicedb_client.py      ← 练习2:ReBAC 权限引擎(纯Python,兼容SpiceDB接口)
├── demo_proxy.py          ← 练习1 代理模式:端到端 HTTP 测试
├── demo_doc_acl.py        ← 练习2:文档级权限测试
└── README.md              ← 本文件
```

## 权限模型(ReBAC)

### Schema

```zed
definition user {}

definition document {
    relation viewer: user
    relation owner: user
    permission view = viewer + owner
}
```

### 预置权限关系

| 用户 | 文档 | 关系 | 效果 |
|---|---|---|---|
| alice | finance_report | viewer + owner | ✅ 能查看 |
| alice | salary_table | (无) | ❌ 无法查看 |
| alice | finance_report_2025 | (无) | ❌ 无法查看 |
| bob | api_spec | viewer | ✅ 能查看 |

### API

| 方法 | 说明 |
|---|---|
| `engine.grant_document_view(doc_id, user_id)` | 授予查看权限 |
| `engine.revoke_document_view(doc_id, user_id)` | 撤销权限 |
| `engine.can_view_document(user_id, doc_id) -> bool` | 检查能否查看 |
| `engine.get_viewable_documents(user_id) -> set` | 获取白名单(pre-filter 用) |

## Post-fetch 过滤的工作原理

```
1. 客户端: POST /query { "query": "CEO年薪多少" }
2. 代理:   验 JWT → user_id=alice → workspace=tenant_finance → port 9621
3. 代理:   转发给 LightRAG 后端
4. 后端:   向量检索 + LLM 生成 → 返回 { response, references }
5. 代理:   【ACL 过滤】对 references 里每个 file_path:
             salary_table.txt → doc_id=salary_table → can_view(alice, salary_table) = False → 移除
             finance_report.txt → doc_id=finance_report → can_view(alice, finance_report) = True → 保留
6. 代理:   返回过滤后的 { response, references, _acl_denied_documents }
```

## ⚠️ Post-fetch 过滤的局限性(重要!)

代理层过滤的是**返回给客户端的引用列表(references)**。但 LLM 在生成回答时已经看到了越权文档的内容。

```
实际测试结果:
  alice 查 "CEO年薪" →
    回答: "CEO张三的年薪是850万"        ← ⚠️ 薪资数字已泄露到文本里!
    引用: [finance_report.txt]          ← salary_table 被过滤了
    被拒: [salary_table, finance_report_2025]  ← ACL 标记
```

**为什么**:代理在后端返回结果后才过滤。但后端的 LLM 在生成答案时,上下文里已包含了 salary_table 的 chunk 内容,生成的文本就带着薪资数字。

**怎么彻底解决**:需要**练习 3 的向量层 pre-filter** —— 在检索阶段就排除越权文档,让 LLM 根本看不到这些内容。这需要修改 LightRAG 的 `operate.py` 和 `BaseVectorStorage.query`。

代理层能做到的:过滤引用 + 标记被拒文档 + 审计日志 + 拒绝整个回答(如果全被拒)。

## 用户与 workspace

| 用户 | 密码 | workspace | 端口 |
|---|---|---|---|
| alice | alice123 | tenant_finance | 9621 |
| bob | bob123 | tenant_engineering | 9622 |

## 手动测试(curl)

```bash
# 登录
curl -X POST http://localhost:8000/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username":"alice","password":"alice123"}'

# 查询(带 token)
curl -X POST http://localhost:8000/query \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{"query":"公司营收多少","mode":"hybrid"}'

# 响应里会包含 _acl_denied_documents 字段,标记被 ACL 拦截的文档
```

## 你学到了什么

### 练习 1
1. 反向代理:不动 LightRAG 核心,加一层身份认证 + 路由
2. JWT 全流程:签发 → 携带 → 验证 → 拒绝
3. 多租户路由:user → workspace → 后端端口
4. subprocess 进程管理

### 练习 2
5. ReBAC 权限模型:Google Zanzibar 的核心概念(relationship → permission)
6. 文档级 ACL:同一 workspace 内不同文档不同权限
7. Post-fetch 过滤的原理和局限 —— **亲身体验为什么它不够安全**
8. file_path → document_id 的映射(LightRAG 用文件路径,SpiceDB 用对象 ID)

## 下一步

- **练习 3:向量层 pre-filter** —— 改 `BaseVectorStorage.query` 加 filter 参数,检索时就排除越权文档,彻底解决 post-fetch 的局限
