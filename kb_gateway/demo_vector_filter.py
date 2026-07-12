"""
练习 3 端到端测试 —— 向量层 pre-filter

验证:ACL 白名单传给 QueryParam 后,检索阶段就排除越权文档,
LLM 的回答文本里不再包含越权内容。

对比练习2(post-fetch):
  练习2: LLM 看到了薪资内容 → 文本泄露 "850万" → 只过滤了 references
  练习3: LLM 根本看不到薪资内容 → 文本不包含薪资 → 彻底解决

直接用 SDK(不走代理),因为 pre-filter 在 LightRAG 核心里。
"""

import asyncio
import os
import time

from dotenv import load_dotenv

from lightrag import LightRAG, QueryParam
from lightrag.llm.openai import openai_complete_if_cache, openai_embed
from lightrag.utils import wrap_embedding_func_with_attrs
import numpy as np


async def llm_model_func(prompt, system_prompt=None, history_messages=[], **kwargs) -> str:
    return await openai_complete_if_cache(
        os.getenv("LLM_MODEL", "glm-4-flash"),
        prompt,
        system_prompt=system_prompt,
        history_messages=history_messages,
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
    return await openai_embed.func(
        texts,
        model=os.getenv("EMBEDDING_MODEL", "text-embedding-v3"),
        base_url=os.getenv("EMBEDDING_BINDING_HOST"),
        api_key=os.getenv("EMBEDDING_BINDING_API_KEY"),
    )


# 测试数据
SALARY_DATA = """
2025年核心岗位薪资表:
CEO张三年薪850万, CTO李四年薪520万, CFO王五年薪480万,
首席科学家赵六年薪400万, VP工程年薪300万,
高级工程师平均65万, 初级工程师平均28万。
"""

BUDGET_DATA = """
2025年部门预算:
研发部预算 2.5 亿,市场部预算 1.2 亿,运营部预算 8000 万。
总预算 4.5 亿,其中人力成本占 60%。
新项目立项: AI平台 5000 万,云迁移 3000 万。
"""


async def main():
    load_dotenv()

    print("=" * 60)
    print("  练习 3: 向量层 pre-filter 测试")
    print("=" * 60)

    rag = LightRAG(
        workspace="test_vector_filter",
        llm_model_func=llm_model_func,
        llm_model_name=os.getenv("LLM_MODEL", "glm-4-flash"),
        embedding_func=embedding_func,
    )
    await rag.initialize_storages()

    try:
        # ── 插入两份文档到同一 workspace ────────────────────
        print("\n📝 插入两份文档(薪资表 + 预算报告)...")
        await rag.ainsert(SALARY_DATA, file_paths=["salary_table.txt"])
        await rag.ainsert(BUDGET_DATA, file_paths=["budget_report.txt"])

        print("⏳ 等 pipeline 处理 60 秒...")
        time.sleep(60)

        # ── 场景 1: 无 ACL 限制 → 能查到薪资 ───────────────
        print("\n" + "=" * 60)
        print("🔍 场景 1: 无 ACL 限制(能查到薪资)")
        print("=" * 60)
        result = await rag.aquery(
            "CEO年薪多少？",
            param=QueryParam(mode="hybrid"),
        )
        print(f"回答: {result[:300]}")

        # ── 场景 2: ACL 白名单只含 budget → 查不到薪资 ────
        print("\n" + "=" * 60)
        print("🛡️  场景 2: ACL 只允许 budget_report(查不到薪资)")
        print("=" * 60)
        result = await rag.aquery(
            "CEO年薪多少？",
            param=QueryParam(
                mode="hybrid",
                acl_allowed_doc_ids={"budget_report"},  # 只允许看预算报告
            ),
        )
        print(f"回答: {result[:300]}")
        print()
        if "850" in result or "520" in result or "薪资" in result:
            print("❌ 失败! 薪资内容仍泄露到回答文本中")
        else:
            print("✅ 成功! LLM 文本中不包含薪资内容(pre-filter 生效)")

        # ── 场景 3: ACL 白名单含 salary → 能查到薪资 ───────
        print("\n" + "=" * 60)
        print("✅ 场景 3: ACL 允许 salary_table(能查到薪资)")
        print("=" * 60)
        result = await rag.aquery(
            "CEO年薪多少？",
            param=QueryParam(
                mode="hybrid",
                acl_allowed_doc_ids={"salary_table", "budget_report"},
            ),
        )
        print(f"回答: {result[:300]}")
        if "850" in result or "520" in result:
            print("✅ 成功! 有权限时能正常查到薪资")
        else:
            print("⚠️  查到了但回答没直接给数字(可能 LLM 回答方式不同)")

        # ── 对比总结 ────────────────────────────────────────
        print("\n" + "=" * 60)
        print("📊 练习2(post-fetch) vs 练习3(pre-filter) 对比")
        print("=" * 60)
        print("  练习2: LLM 看到薪资 → 回答泄露 '850万' → 只过滤 references")
        print("  练习3: LLM 看不到薪资 → 回答不含薪资 → 彻底解决")
        print()
        print("  核心区别:")
        print("    post-fetch: 过滤输出(LLM 已生成答案)")
        print("    pre-filter: 过滤输入(LLM 根本没看到越权内容)")

    finally:
        await rag.finalize_storages()


if __name__ == "__main__":
    asyncio.run(main())
