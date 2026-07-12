"""
WorkspaceManager — 多租户 RAG 实例池

核心职责:
  1. 为每个 workspace 懒加载一个独立的 LightRAG 实例
  2. 根据用户身份（user_id → workspace 映射）路由请求到正确实例
  3. 管理实例生命周期（初始化 / 清理）

隔离原理:
  LightRAG 的 workspace 参数会让四类存储（KV / Vector / Graph / DocStatus）
  自动隔离 —— 文件型存储用子目录，关系型用 workspace 列，集合型用前缀。
  每个 workspace 是一个物理隔离的知识库，不可能串租户。

这是练习 1 的实现。设计文档见:
  Doc/LightRAG/04-融合与实践/05-企业级权限控制技术选型.md  方案④
"""

import asyncio
import os
from typing import Any

import numpy as np
from lightrag import LightRAG, QueryParam
from lightrag.llm.openai import openai_complete_if_cache, openai_embed
from lightrag.utils import wrap_embedding_func_with_attrs


# ──────────────────────────────────────────────────────────────
# 第一步：构造 LLM 和 Embedding 的可调用对象
# 这两个函数会被注入到每个 LightRAG 实例
# ──────────────────────────────────────────────────────────────

async def llm_model_func(
    prompt,
    system_prompt=None,
    history_messages=[],
    keyword_extraction=False,
    **kwargs,
) -> str:
    """智谱 GLM-4-Flash（OpenAI 兼容接口）"""
    return await openai_complete_if_cache(
        kwargs.get("model_name", os.getenv("LLM_MODEL", "glm-4-flash")),
        prompt,
        system_prompt=system_prompt,
        history_messages=history_messages,
        keyword_extraction=keyword_extraction,
        base_url=os.getenv("LLM_BINDING_HOST"),
        api_key=os.getenv("LLM_BINDING_API_KEY"),
        **{k: v for k, v in kwargs.items() if k != "model_name"},
    )


@wrap_embedding_func_with_attrs(
    embedding_dim=int(os.getenv("EMBEDDING_DIM", "1024")),
    max_token_size=int(os.getenv("EMBEDDING_TOKEN_LIMIT", "8192")),
    model_name=os.getenv("EMBEDDING_MODEL", "text-embedding-v3"),
)
async def embedding_func(texts: list[str]) -> np.ndarray:
    """阿里云 text-embedding-v3（OpenAI 兼容接口）"""
    return await openai_embed.func(
        texts,
        model=os.getenv("EMBEDDING_MODEL", "text-embedding-v3"),
        base_url=os.getenv("EMBEDDING_BINDING_HOST"),
        api_key=os.getenv("EMBEDDING_BINDING_API_KEY"),
    )


# ──────────────────────────────────────────────────────────────
# 第二步：用户 → workspace 的映射（模拟身份系统）
# 企业场景中，这个映射来自 JWT claim / 数据库 / LDAP
# ──────────────────────────────────────────────────────────────

# 用户所属的 workspace。生产环境从用户表查。
USER_WORKSPACE_MAP: dict[str, str] = {
    "alice": "tenant_finance",    # alice 属于财务部
    "bob": "tenant_engineering",  # bob 属于工程部
}


# ──────────────────────────────────────────────────────────────
# 第三步：WorkspaceManager —— 本练习的核心
# ──────────────────────────────────────────────────────────────

class WorkspaceManager:
    """
    多 workspace 的 LightRAG 实例池。

    特性:
      - 懒加载: 第一次访问 workspace 时才创建实例，避免启动时初始化全部
      - 线程安全: 用锁保护实例创建，防止并发重复初始化
      - 身份路由: request(user_id, ...) 自动选对实例
      - 生命周期统一管理: initialize_all / finalize_all

    用法:
        mgr = WorkspaceManager()
        await mgr.initialize_all()

        # alice 只能访问财务部数据
        result = await mgr.query("alice", "去 年营收是多少？")

        # bob 只能访问工程部数据
        result = await mgr.query("bob", "API 怎么设计？")

        await mgr.finalize_all()
    """

    def __init__(self, working_dir: str = "./rag_storage"):
        self.working_dir = working_dir
        self._instances: dict[str, LightRAG] = {}     # workspace → rag 实例
        self._locks: dict[str, asyncio.Lock] = {}      # 每个实例一把锁，防并发创建
        self._global_lock = asyncio.Lock()              # 保护 _locks 字典本身

    async def _get_or_create(self, workspace: str) -> LightRAG:
        """
        获取或创建某个 workspace 的 LightRAG 实例（懒加载 + 双重检查锁）。

        为什么需要锁:
          如果两个请求同时访问一个还没初始化的 workspace，
          没有锁的话会创建两个实例，第二个会覆盖第一个的存储句柄。
        """
        # 已存在 → 直接返回（无锁快速路径）
        if workspace in self._instances:
            return self._instances[workspace]

        # 获取该 workspace 专属的锁（防止并发重复创建）
        async with self._global_lock:
            if workspace not in self._locks:
                self._locks[workspace] = asyncio.Lock()

        async with self._locks[workspace]:
            # 双重检查：拿到锁后可能已被其他协程创建
            if workspace in self._instances:
                return self._instances[workspace]

            print(f"  [WorkspaceManager] 创建实例: workspace='{workspace}'")
            rag = LightRAG(
                working_dir=self.working_dir,
                workspace=workspace,
                llm_model_func=llm_model_func,
                llm_model_name=os.getenv("LLM_MODEL", "glm-4-flash"),
                embedding_func=embedding_func,
            )
            await rag.initialize_storages()

            self._instances[workspace] = rag
            return rag

    # ── 对外 API ──────────────────────────────────────────────

    async def insert(self, user_id: str, text: str, **kwargs) -> dict[str, Any]:
        """用户 user_id 向自己的 workspace 插入文档"""
        workspace = self._resolve_workspace(user_id)
        rag = await self._get_or_create(workspace)
        print(f"  [insert] user='{user_id}' → workspace='{workspace}'")
        await rag.ainsert(text, **kwargs)
        return {"user": user_id, "workspace": workspace, "status": "inserted"}

    async def query(self, user_id: str, question: str, mode: str = "hybrid", **kwargs) -> dict[str, Any]:
        """用户 user_id 只能查询自己 workspace 的数据"""
        workspace = self._resolve_workspace(user_id)
        rag = await self._get_or_create(workspace)
        print(f"  [query]  user='{user_id}' → workspace='{workspace}'")
        result = await rag.aquery(question, param=QueryParam(mode=mode, **kwargs))
        return {"user": user_id, "workspace": workspace, "answer": result}

    # ── 身份 → workspace 路由 ──────────────────────────────────

    def _resolve_workspace(self, user_id: str) -> str:
        """
        把 user_id 解析成 workspace 名。

        生产环境替换为:
          - 从 JWT token 解析
          - 查数据库 user 表
          - 查 LDAP/AD 的部门属性
        """
        ws = USER_WORKSPACE_MAP.get(user_id)
        if ws is None:
            raise PermissionError(
                f"用户 '{user_id}' 未分配任何 workspace，拒绝访问。"
                f"已知用户: {list(USER_WORKSPACE_MAP.keys())}"
            )
        return ws

    # ── 生命周期管理 ──────────────────────────────────────────

    async def initialize_all(self, workspaces: list[str] | None = None):
        """
        预初始化 workspace 实例。

        Args:
            workspaces: 要初始化的 workspace 列表。None 则用 USER_WORKSPACE_MAP 的值去重。
        """
        if workspaces is None:
            workspaces = list(set(USER_WORKSPACE_MAP.values()))

        print(f"[WorkspaceManager] 初始化 {len(workspaces)} 个 workspace...")
        # 并发初始化，加快启动
        await asyncio.gather(*[self._get_or_create(ws) for ws in workspaces])
        print(f"[WorkspaceManager] 就绪: {list(self._instances.keys())}")

    async def finalize_all(self):
        """关闭所有实例的存储连接，安全退出。"""
        print("[WorkspaceManager] 关闭所有实例...")
        for ws, rag in self._instances.items():
            try:
                await rag.finalize_storages()
                print(f"  ✓ 已关闭 workspace='{ws}'")
            except Exception as e:
                print(f"  ✗ 关闭 workspace='{ws}' 失败: {e}")
        self._instances.clear()

    def list_workspaces(self) -> list[str]:
        """返回当前已加载的 workspace 列表"""
        return list(self._instances.keys())
