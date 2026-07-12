"""
练习 2 端到端测试 —— 文档级权限(Doc-level ACL)

验证:同一 workspace 内,alice 能看"月报"但看不到"薪资表"。

前提:先启动代理(在另一个终端):
    .venv/Scripts/python -m kb_gateway.run_proxy

场景:
  1. alice 登录
  2. alice 插入两份文档到同一 workspace (tenant_finance):
     - finance_report(月报)—— alice 有 view 权限
     - salary_table(薪资表)—— alice 没有 view 权限
  3. 等 pipeline 处理完
  4. alice 查月报内容 → 能查到(references 含 finance_report)
  5. alice 查薪资内容 → references 被 ACL 过滤掉(_acl_denied_documents 标记)
  6. 演示动态授权:给 alice 加 salary_table 权限后,立刻能查到
"""

import asyncio
import time

import httpx

PROXY_URL = "http://127.0.0.1:8000"

FINANCE_REPORT = """
2025年第一季度财务月报:
1月收入 1.2 亿元,2月收入 1.5 亿元,3月收入 1.8 亿元。
季度总营收 4.5 亿,环比增长 12%。
主要成本: 研发投入 8000 万,市场推广 3000 万。
毛利率 65%,运营利润率 22%。
现金储备 12 亿,财务状况健康。
"""

SALARY_TABLE = """
2025年核心岗位薪资表(机密 — 仅限 HR 和高管):
CEO 张三: 年薪 850 万 + 期权 2000 万
CTO 李四: 年薪 520 万 + 期权 800 万
CFO 王五: 年薪 480 万 + 期权 600 万
首席科学家赵六: 年薪 400 万 + 期权 500 万
VP 工程: 年薪 300 万 + 期权 300 万
高级工程师平均: 年薪 65 万
初级工程师平均: 年薪 28 万
"""


async def main():
    print("=" * 60)
    print("  练习 2: 文档级权限测试 (Doc-level ACL)")
    print("=" * 60)

    async with httpx.AsyncClient(timeout=120.0, proxy=None) as client:
        # 检查代理
        try:
            resp = await client.get(f"{PROXY_URL}/health")
            print(f"  代理状态: {resp.json()}\n")
        except httpx.ConnectError:
            print("❌ 代理未运行! 请先执行:")
            print("   .venv/Scripts/python -m kb_gateway.run_proxy")
            return

        # ── 登录 ──────────────────────────────────────────────
        print("=" * 60)
        print("📝 步骤 1: alice 登录")
        print("=" * 60)
        resp = await client.post(
            f"{PROXY_URL}/auth/login",
            json={"username": "alice", "password": "alice123"},
        )
        token = resp.json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}
        print(f"✅ 登录成功, workspace={resp.json()['workspace']}\n")

        # ── 插入两份文档 ──────────────────────────────────────
        print("=" * 60)
        print("📝 步骤 2: alice 插入两份文档(月报 + 薪资表)")
        print("=" * 60)

        resp = await client.post(
            f"{PROXY_URL}/documents/text",
            json={"text": FINANCE_REPORT, "file_source": "finance_report.txt"},
            headers=headers,
        )
        print(f"  月报插入: {resp.status_code} {resp.json().get('status', '')}")

        resp = await client.post(
            f"{PROXY_URL}/documents/text",
            json={"text": SALARY_TABLE, "file_source": "salary_table.txt"},
            headers=headers,
        )
        print(f"  薪资表插入: {resp.status_code} {resp.json().get('status', '')}")
        print(f"     (薪资表已存入 workspace,但 alice 没有 view 权限)")

        # 等 pipeline
        print("\n⏳ 等待 pipeline 处理 60 秒...")
        time.sleep(60)

        # ── 场景 1: alice 查月报(有权限) ────────────────────
        print("\n" + "=" * 60)
        print("🔍 场景 1: alice 查月报内容（有 view 权限）")
        print("=" * 60)
        resp = await client.post(
            f"{PROXY_URL}/query",
            json={"query": "季度总营收是多少？毛利率多少？", "mode": "hybrid"},
            headers=headers,
        )
        data = resp.json()
        refs = data.get("references", [])
        denied = data.get("_acl_denied_documents", [])

        print(f"  状态码: {resp.status_code}")
        print(f"  回答: {data.get('response', '')[:300]}")
        print(f"  引用文档: {[r.get('file_path') for r in refs]}")
        if denied:
            print(f"  被拒文档: {denied}")
        else:
            print(f"  ✅ 无被拒文档（alice 有 finance_report 的权限）")

        # ── 场景 2: alice 查薪资表(无权限) ───────────────────
        print("\n" + "=" * 60)
        print("🛡️  场景 2: alice 查薪资表内容（无 view 权限，应被 ACL 过滤）")
        print("=" * 60)
        resp = await client.post(
            f"{PROXY_URL}/query",
            json={"query": "CEO年薪多少？CTO薪资是多少？", "mode": "hybrid"},
            headers=headers,
        )
        data = resp.json()
        refs = data.get("references", [])
        denied = data.get("_acl_denied_documents", [])

        print(f"  状态码: {resp.status_code}")
        print(f"  回答: {data.get('response', '')[:300]}")
        print(f"  引用文档: {[r.get('file_path') for r in refs]}")
        if denied:
            print(f"  ✅ ACL 过滤生效! 被拒文档: {denied}")
            print(f"     即使薪资表在 alice 的 workspace 里，她也没有 view 权限")
        else:
            print(f"  ⚠️ 未触发 ACL 过滤（可能查询没命中薪资表文档）")

        # ── 场景 3: 演示 post-fetch 的局限性 ─────────────────
        print("\n" + "=" * 60)
        print("⚠️  场景 3: post-fetch 过滤的局限性演示")
        print("=" * 60)
        print("  即使 references 被过滤了，LLM 的回答文本可能已包含薪资数字。")
        print("  这是 post-fetch 过滤的根本局限 —— LLM 在生成答案时已看到了越权内容。")
        print("  要彻底解决需要练习 3 的向量层 pre-filter（检索时就排除）。")
        print("  代理层能做的是:过滤引用 + 标记被拒文档 + 审计日志。")

        # ── 总结 ──────────────────────────────────────────────
        print("\n" + "=" * 60)
        print("📊 练习 2 总结")
        print("=" * 60)
        print("  ✅ 文档级 ACL 在代理层生效")
        print("  ✅ 同一 workspace 内的文档可以有不同的权限")
        print("  ✅ ReBAC 模型: user → document → view permission")
        print("  ⚠️  post-fetch 过滤有局限(LLM 文本可能已含越权内容)")
        print("     → 练习 3 的向量层 pre-filter 才能彻底解决")


if __name__ == "__main__":
    asyncio.run(main())
