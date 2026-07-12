# 练习 1:多 workspace 隔离

**目标**:alice(财务部)和 bob(工程部)的数据完全隔离,不可能串。

---

## 一、两种实现模式

### 1.1 SDK 模式 —— 直接管理 LightRAG Python 实例

```
WorkspaceManager
  ├─ tenant_finance → LightRAG(workspace="tenant_finance") 实例1
  └─ tenant_engineering → LightRAG(workspace="tenant_engineering") 实例2
```

核心代码(`kb_gateway/workspace_manager.py`):

```python
class WorkspaceManager:
    async def _get_or_create(self, workspace: str) -> LightRAG:
        # 懒加载 + 双重检查锁
        if workspace in self._instances:
            return self._instances[workspace]           # 快速路径(无锁)
        async with self._locks[workspace]:              # 互斥
            if workspace in self._instances:            # 双重检查
                return self._instances[workspace]
            rag = LightRAG(workspace=workspace, ...)
            await rag.initialize_storages()
            self._instances[workspace] = rag
            return rag

    async def query(self, user_id: str, question: str, ...):
        workspace = self._resolve_workspace(user_id)   # 身份→workspace
        rag = await self._get_or_create(workspace)
        return await rag.aquery(question, ...)
```

### 1.2 代理模式 —— FastAPI 反向代理

```
客户端 curl/Postman
    │  Authorization: Bearer <JWT>
    ▼
代理 (:8000) ── JWT 验证 + workspace 路由
    ├──► 后端 #1 (:9621, tenant_finance)     ← alice
    └──► 后端 #2 (:9622, tenant_engineering)  ← bob
         (subprocess 自动管理)
```

代理用 `subprocess.Popen` 启动两个 LightRAG server 子进程,不同端口 + 不同 WORKSPACE。

---

## 二、验证结果

```
✅ alice 查 "总收入" → "公司全年总收入为15.6亿元"
✅ bob   查 "总收入" → "无法提供这些问题的答案"(隔离生效)
✅ 无 token → 401
✅ 错密码 → 401
```

---

## 三、踩的坑

### 坑位 1:subprocess stdout 管道阻塞

用 `stdout=subprocess.PIPE` 但不读取,缓冲区满后子进程 hang。

**修复**:重定向到日志文件。

### 坑位 2:pending.discard 类型错误

```python
# 错误:pending 存的是 workspace 名,删的却是 port
pending.discard(port)     # ← 永远删不掉

# 修复:
pending.discard(workspace)
```

### 坑位 3:旧进程占用端口

新代理启动失败 `Errno 10048`,因为上次的进程没杀干净。

**修复**:启动前检查端口并 kill。

---

## 四、学到的核心概念

1. **物理隔离 vs 逻辑过滤**:workspace 隔离是物理级别的(各自独立存储),比查询时加 filter 更安全
2. **懒加载 + 双重检查锁**:多实例管理中避免并发重复初始化
3. **JWT 全流程**:签发(登录) → 携带(Authorization header) → 验证(中间件) → 拒绝(401)
4. **subprocess 进程管理**:用 Python 拉起/监控/清理外部 server 进程

---

## 五、局限性

权限粒度只到 **workspace 级(租户级)**,同一 workspace 内所有文档对用户都可见。

→ 练习 2 解决这个问题。
