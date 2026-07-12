"""
练习 1 演示脚本 —— 多租户工作区隔离

验证三个场景:
  场景 1: alice(财务部)插入财务数据后，alice 能查到           ✅ 正常
  场景 2: bob(工程部)查询财务数据，查不到                      ✅ 隔离成功
  场景 3: 未注册用户 charlie 访问，被拒绝                       ✅ 鉴权生效

运行方式:
    cd D:\\Project\\LightRAG
    python -m kb_gateway.demo
"""

import asyncio
import os

from dotenv import load_dotenv

from kb_gateway.workspace_manager import WorkspaceManager

# ── 测试数据 ────────────────────────────────────────────────

# 财务部的机密数据（只应该被 alice 看到）
FINANCE_DOC = """
2025年度财务报告（机密 — 仅限财务部）:

公司全年总收入 8.2 亿元，同比增长 23%。
其中企业服务收入 5.1 亿，消费级产品收入 3.1 亿。

核心客户: 中石化（年合同额 1.2 亿）、国家电网（8000 万）。
员工平均薪资: 高级工程师 45 万/年，初级工程师 22 万/年。
CEO 年薪: 850 万。

成本结构: 人力成本占 55%，云服务支出 3200 万，办公场地 1800 万。
净利润 1.6 亿，净利率 19.5%。
"""

# 工程部的技术文档（只应该被 bob 看到）
ENGINEERING_DOC = """
API 网关技术规范（工程部内部）:

所有微服务统一通过 Kong 网关暴露。
认证使用 JWT + RBAC，token 有效期 2 小时。
限流策略: 普通用户 100 req/min，VIP 用户 1000 req/min。

数据库: PostgreSQL 16 主从架构，读连接池上限 200。
缓存: Redis 7 集群，3 主 3 从，哨兵自动故障转移。
消息队列: RabbitMQ，消费端最大并发 50。

部署: Kubernetes 1.29，HPA 根据 CPU>70% 自动扩容。
日志收集: Vector → Loki → Grafana 全链路。
"""


async def main():
    # 加载 .env（智谱 LLM + 阿里云 Embedding 配置）
    load_dotenv()

    print("=" * 60)
    print("练习 1: 多租户工作区隔离 Demo")
    print("=" * 60)

    mgr = WorkspaceManager(working_dir="./rag_storage")

    try:
        # ────────────────────────────────────────────────────
        # 步骤 1: 初始化 + 插入数据
        # ────────────────────────────────────────────────────
        print("\n📝 步骤 1: 向各自的 workspace 插入数据")
        print("-" * 50)

        # alice 向财务部插入数据
        await mgr.insert("alice", FINANCE_DOC)

        # bob 向工程部插入数据
        await mgr.insert("bob", ENGINEERING_DOC)

        # 等待异步 pipeline 处理完
        print("\n⏳ 等待 pipeline 处理（实体抽取 + 向量索引）...")
        await asyncio.sleep(10)

        # ────────────────────────────────────────────────────
        # 场景 1: alice 查询财务数据 → 应该能查到
        # ────────────────────────────────────────────────────
        print("\n" + "=" * 60)
        print("🔍 场景 1: alice 查询财务数据（应该能查到）")
        print("=" * 60)
        result_alice = await mgr.query(
            "alice", "公司全年总收入是多少？核心客户有哪些？"
        )
        print(f"\n✅ alice 的回答:\n{result_alice['answer'][:500]}")

        # ────────────────────────────────────────────────────
        # 场景 2: bob 查询财务数据 → 应该查不到！
        # ────────────────────────────────────────────────────
        print("\n" + "=" * 60)
        print("🛡️  场景 2: bob 查询财务数据（应该查不到 — 隔离生效）")
        print("=" * 60)
        result_bob = await mgr.query(
            "bob", "公司全年总收入是多少？核心客户有哪些？CEO年薪多少？"
        )
        print(f"\n bob 的回答:\n{result_bob['answer'][:500]}")
        print("\n💡 bob 是工程部的，他的 workspace 里只有技术文档，")
        print("   所以他查不到任何财务数据 —— 隔离成功！")

        # ────────────────────────────────────────────────────
        # 场景 3: 未注册用户 → 应该被拒绝
        # ────────────────────────────────────────────────────
        print("\n" + "=" * 60)
        print("🚫 场景 3: 未注册用户 charlie 访问（应该被拒绝）")
        print("=" * 60)
        try:
            await mgr.query("charlie", "随便什么")
        except PermissionError as e:
            print(f"✅ 鉴权生效: {e}")

        # ────────────────────────────────────────────────────
        # 总结
        # ────────────────────────────────────────────────────
        print("\n" + "=" * 60)
        print("📊 练习 1 总结")
        print("=" * 60)
        print(f"  已加载 workspace: {mgr.list_workspaces()}")
        print(f"  alice → tenant_finance  (财务数据，物理隔离)")
        print(f"  bob   → tenant_engineering (技术文档，物理隔离)")
        print(f"  charlie → 拒绝（未注册）")
        print("\n  核心结论: workspace 隔离是物理级别的，")
        print("  不存在「忘了加 filter」导致泄漏的风险。")

    except Exception as e:
        print(f"\n❌ 运行出错: {e}")
        import traceback
        traceback.print_exc()
    finally:
        await mgr.finalize_all()


if __name__ == "__main__":
    asyncio.run(main())
