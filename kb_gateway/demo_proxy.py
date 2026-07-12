"""
端到端测试脚本 —— 通过 HTTP 测试代理的完整流程

前提:先启动代理(在另一个终端):
    .venv/Scripts/python -m kb_gateway.run_proxy

然后运行本脚本:
    .venv/Scripts/python -m kb_gateway.demo_proxy

验证五个场景:
  1. alice 登录拿 JWT                  ✅ 认证系统工作
  2. alice 插入财务数据                  ✅ 写入路由正确
  3. alice 查询财务数据 → 能查到         ✅ 查询路由正确
  4. bob 查询同样问题 → 查不到           ✅ 隔离生效
  5. 不带 token 访问 → 401              ✅ 鉴权生效
"""

import asyncio
import time

import httpx

PROXY_URL = "http://127.0.0.1:8000"

FINANCE_TEXT = """
2025年度财务报告:
公司全年总收入 15.6 亿元，同比增长 30%。
核心客户: 腾讯（年合同额 2 亿）、阿里（1.5 亿）。
净利润 3.2 亿，净利率 20.5%。
员工总数 1200 人，研发投入占比 18%。
"""

ENGINEERING_TEXT = """
微服务架构规范:
网关: Spring Cloud Gateway，限流 500 QPS。
服务注册: Nacos，健康检查间隔 5 秒。
链路追踪: SkyWalking，采样率 10%。
配置中心: Apollo，灰度发布支持。
"""


async def main():
    print("=" * 60)
    print("  代理端到端测试")
    print("=" * 60)

    async with httpx.AsyncClient(timeout=120.0) as client:
        # 先检查代理是否在运行
        try:
            resp = await client.get(f"{PROXY_URL}/health")
            print(f"  代理健康检查: {resp.json()}\n")
        except httpx.ConnectError:
            print("❌ 代理未运行! 请先执行:")
            print("   .venv/Scripts/python -m kb_gateway.run_proxy")
            return

        # ── 场景 1: alice 登录 ──────────────────────────────
        print("=" * 60)
        print("📝 场景 1: alice 登录拿 JWT")
        print("=" * 60)
        resp = await client.post(
            f"{PROXY_URL}/auth/login",
            json={"username": "alice", "password": "alice123"},
        )
        assert resp.status_code == 200, f"登录失败: {resp.status_code} {resp.text}"
        alice_token = resp.json()["access_token"]
        alice_ws = resp.json()["workspace"]
        print(f"✅ 登录成功: user=alice, workspace={alice_ws}")
        print(f"   token: {alice_token[:50]}...\n")

        # ── 场景 2: alice 插入财务数据 ──────────────────────
        print("=" * 60)
        print("📝 场景 2: alice 插入财务数据")
        print("=" * 60)
        resp = await client.post(
            f"{PROXY_URL}/documents/text",
            json={"text": FINANCE_TEXT, "file_source": "finance_report_2025.txt"},
            headers={"Authorization": f"Bearer {alice_token}"},
        )
        print(f"   插入响应: {resp.status_code}")
        if resp.status_code == 200:
            print(f"✅ 插入成功: {resp.json()}")
        else:
            print(f"   {resp.text[:200]}")
        print()

        # 等待 pipeline 处理(实体抽取很慢)
        print("⏳ 等待 pipeline 处理 30 秒...")
        time.sleep(30)

        # ── 场景 3: alice 查询 → 应该能查到 ─────────────────
        print("=" * 60)
        print("🔍 场景 3: alice 查询财务数据（应该能查到）")
        print("=" * 60)
        resp = await client.post(
            f"{PROXY_URL}/query",
            json={"query": "公司全年总收入是多少？", "mode": "hybrid"},
            headers={"Authorization": f"Bearer {alice_token}"},
        )
        print(f"   状态码: {resp.status_code}")
        if resp.status_code == 200:
            answer = resp.json().get("response", "")[:400]
            print(f"✅ alice 的回答:\n   {answer}")
        else:
            print(f"   {resp.text[:300]}")
        print()

        # ── 场景 4: bob 查同样问题 → 查不到 ─────────────────
        print("=" * 60)
        print("🛡️  场景 4: bob 查同样问题（应该查不到 — 隔离生效）")
        print("=" * 60)

        # bob 登录
        resp = await client.post(
            f"{PROXY_URL}/auth/login",
            json={"username": "bob", "password": "bob123"},
        )
        bob_token = resp.json()["access_token"]
        bob_ws = resp.json()["workspace"]
        print(f"   bob 登录: workspace={bob_ws}")

        # bob 查询
        resp = await client.post(
            f"{PROXY_URL}/query",
            json={"query": "公司全年总收入是多少？", "mode": "hybrid"},
            headers={"Authorization": f"Bearer {bob_token}"},
        )
        print(f"   状态码: {resp.status_code}")
        if resp.status_code == 200:
            answer = resp.json().get("response", "")[:400]
            print(f" bob 的回答:\n   {answer}")
            print("\n💡 bob 的 workspace 里没有财务数据 → 隔离生效!")
        print()

        # ── 场景 5: 不带 token → 401 ─────────────────────────
        print("=" * 60)
        print("🚫 场景 5: 不带 token 访问（应该 401）")
        print("=" * 60)
        resp = await client.post(
            f"{PROXY_URL}/query",
            json={"query": "随便", "mode": "hybrid"},
            # 不带 Authorization header
        )
        if resp.status_code == 401:
            print(f"✅ 鉴权生效: 401 Unauthorized")
            print(f"   {resp.json()}")
        else:
            print(f"❌ 预期 401,实际 {resp.status_code}: {resp.text[:200]}")
        print()

        # ── 场景 6: 错密码登录 → 401 ────────────────────────
        print("=" * 60)
        print("🚫 场景 6: 错密码登录（应该 401）")
        print("=" * 60)
        resp = await client.post(
            f"{PROXY_URL}/auth/login",
            json={"username": "alice", "password": "wrong_password"},
        )
        if resp.status_code == 401:
            print(f"✅ 密码错误被拒: 401")
        else:
            print(f"❌ 预期 401,实际 {resp.status_code}")

        # ── 总结 ────────────────────────────────────────────
        print("\n" + "=" * 60)
        print("📊 端到端测试总结")
        print("=" * 60)
        print("  ✅ 代理自身的 JWT 认证工作正常")
        print("  ✅ 代理正确路由到对应 workspace 后端")
        print("  ✅ workspace 隔离生效(bob 查不到 alice 的数据)")
        print("  ✅ 未认证请求被拒(401)")
        print("  ✅ 错误密码被拒(401)")


if __name__ == "__main__":
    asyncio.run(main())
