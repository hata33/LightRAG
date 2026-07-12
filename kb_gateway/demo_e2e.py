"""
练习 1+2+3 端到端全链路测试

验证完整链路:
  客户端 → 代理(JWT验签 + workspace路由)
    → 权限引擎查白名单 → 注入 acl_allowed_doc_ids 到请求体
    → 后端 LightRAG (pre-filter:检索阶段就排除越权文档)
    → 代理 post-fetch 双保险过滤
    → 返回干净结果

核心对比(和练习2的区别):
  练习2: 代理只做 post-fetch → LLM 文本泄露 "850万"
  全链路: 代理注入 pre-filter → LLM 根本看不到薪资 → 文本干净

前提:先启动代理
    .venv/Scripts/python -m kb_gateway.run_proxy
"""

import asyncio
import time

import httpx

PROXY_URL = "http://127.0.0.1:8000"

# 两份文档,都插入 alice 的 workspace (tenant_finance)
# alice 有 finance_report 权限,没有 salary_table 权限
SALARY = "高管薪资: CEO年薪900万 CTO年薪600万 首席科学家年薪500万"
BUDGET = "部门预算: 研发部3亿 市场部1.5亿 运营部8000万 总预算5.3亿"


async def main():
    print("=" * 60)
    print("  全链路测试: JWT → workspace → ACL注入 → pre-filter")
    print("=" * 60)

    async with httpx.AsyncClient(timeout=120.0, proxy=None) as client:
        try:
            resp = await client.get(f"{PROXY_URL}/health")
            print(f"  代理: {resp.json()}\n")
        except httpx.ConnectError:
            print("❌ 代理未运行! 先启动: .venv/Scripts/python -m kb_gateway.run_proxy")
            return

        # ── alice 登录 ────────────────────────────────────────
        resp = await client.post(
            f"{PROXY_URL}/auth/login",
            json={"username": "alice", "password": "alice123"},
        )
        token = resp.json()["access_token"]
        headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
        print(f"✅ alice 登录, workspace={resp.json()['workspace']}")
        print(f"   权限: alice 能看 finance_report, 不能看 salary_table\n")

        # ── 插入两份文档 ──────────────────────────────────────
        print("📝 插入两份文档到 tenant_finance...")
        resp = await client.post(
            f"{PROXY_URL}/documents/text",
            json={"text": SALARY, "file_source": "salary_table.txt"},
            headers=headers,
        )
        print(f"  salary_table: {resp.status_code}")
        resp = await client.post(
            f"{PROXY_URL}/documents/text",
            json={"text": BUDGET, "file_source": "finance_report.txt"},
            headers=headers,
        )
        print(f"  finance_report: {resp.status_code}")

        print("\n⏳ 等 pipeline 70 秒...")
        time.sleep(70)

        # ── 场景 1: 查预算(有权限) ──────────────────────────
        print("\n" + "=" * 60)
        print("🔍 场景 1: alice 查预算(有权限 → 应该能查到)")
        print("=" * 60)
        resp = await client.post(
            f"{PROXY_URL}/query",
            json={"query": "部门预算多少？总预算是多少？", "mode": "hybrid"},
            headers=headers,
        )
        data = resp.json()
        answer = data.get("response", "")
        print(f"  回答: {answer[:300]}")
        print(f"  引用: {[r.get('file_path') for r in data.get('references', [])]}")
        has_budget = "5.3" in answer or "3亿" in answer or "1.5亿" in answer
        print(f"  {'✅ 有权限,查到了预算' if has_budget else '⚠️ 没查到预算'}")

        # ── 场景 2: 查薪资(无权限 → pre-filter 生效) ────────
        print("\n" + "=" * 60)
        print("🛡️  场景 2: alice 查薪资(无权限 → pre-filter 应生效)")
        print("=" * 60)
        resp = await client.post(
            f"{PROXY_URL}/query",
            json={"query": "CEO年薪多少？CTO薪资多少？", "mode": "hybrid"},
            headers=headers,
        )
        data = resp.json()
        answer = data.get("response", "")
        print(f"  回答: {answer[:300]}")
        print(f"  引用: {[r.get('file_path') for r in data.get('references', [])]}")
        denied = data.get("_acl_denied_documents")
        print(f"  post-fetch 被拒: {denied if denied else '无'}")

        has_salary_leak = "900万" in answer or "600万" in answer or "500万" in answer
        if has_salary_leak:
            print("  ❌ 失败! 薪资泄露到回答文本")
        else:
            print("  ✅ 成功! pre-filter 生效,LLM 文本不含薪资")

        # ── 对比总结 ──────────────────────────────────────────
        print("\n" + "=" * 60)
        print("📊 全链路 vs 练习2 对比")
        print("=" * 60)
        print("  练习2 (post-fetch only):")
        print("    LLM 看到薪资 → 回答泄露 '900万' → 只过滤 references")
        print("  全链路 (pre-filter + post-fetch):")
        print("    代理注入 ACL 白名单 → 后端检索就排除薪资文档")
        print("    → LLM 根本没看到薪资 → 回答天然干净")
        print("    → post-fetch 作为双保险(防御纵深)")
        print()
        print("  架构:")
        print("    客户端 → 代理(JWT+ACL注入) → 后端(pre-filter) → 代理(post-fetch) → 干净结果")


if __name__ == "__main__":
    asyncio.run(main())
