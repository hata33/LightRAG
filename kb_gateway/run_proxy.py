"""
一键启动入口(练习 2:加文档级权限)

一条命令拉起完整系统:
  1. 初始化权限引擎(ReBAC),预置文档级权限
  2. 用 subprocess 启动两个 LightRAG server 后端(不同端口 + workspace)
  3. 等待后端就绪
  4. 启动代理 FastAPI(端口 8000)
  5. Ctrl+C 时:关代理 → 杀后端

运行方式:
    cd D:\\Project\\LightRAG
    .venv/Scripts/python -m kb_gateway.run_proxy
"""

import asyncio
import sys

from dotenv import load_dotenv

from kb_gateway.backend_manager import BackendManager
from kb_gateway.proxy import app, set_backend_manager
from kb_gateway.spicedb_client import PermissionEngine, set_engine

PROXY_PORT = 8000


def init_permission_engine():
    """
    初始化权限引擎,预置演示数据。

    权限关系:
      tenant_finance workspace 内:
        - alice 能看 finance_report(月报)     → viewer
        - alice 不能看 salary_table(薪资表)   → 无授权

      tenant_engineering workspace 内:
        - bob 能看所有工程文档                  → viewer
    """
    engine = PermissionEngine()

    # alice 对 finance_report 有 view 权限
    engine.grant_document_view("finance_report", "alice")
    engine.grant_document_view("finance_report", "alice", as_owner=True)

    # 注意:alice 对 salary_table 没有任何授权 → 无法 view

    # bob 对工程文档有权限
    engine.grant_document_view("api_spec", "bob")

    # bob 也对 finance_report 有权限(演示:跨 workspace 不会串,因为 workspace 隔离在更外层)
    # 但实际上 bob 的请求会路由到 tenant_engineering,根本不会查到 finance 数据

    set_engine(engine)

    print("[PermissionEngine] 权限引擎已初始化")
    print("[PermissionEngine] 当前权限关系:")
    for line in engine.dump_relationships():
        print(f"  {line}")

    return engine


def main():
    # 加载 .env
    load_dotenv()

    # Windows 下用 SelectorEventLoop
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

    print("=" * 60)
    print("  KB Gateway 代理 — 多 workspace 路由 + 文档级权限")
    print("=" * 60)

    # 1. 初始化权限引擎
    print("\n📋 初始化权限引擎(ReBAC)...")
    init_permission_engine()

    # 2. 启动后端
    print()
    backend = BackendManager()
    backend.start(timeout=120.0)
    set_backend_manager(backend)

    # 3. 启动代理
    print(f"\n[Proxy] 代理启动在 http://127.0.0.1:{PROXY_PORT}")
    print(f"[Proxy] API 文档: http://127.0.0.1:{PROXY_PORT}/docs")
    print(f"[Proxy] 按 Ctrl+C 停止全部服务\n")

    import uvicorn

    try:
        uvicorn.run(
            app,
            host="0.0.0.0",
            port=PROXY_PORT,
            log_config=None,
        )
    except KeyboardInterrupt:
        print("\n[Proxy] 收到 Ctrl+C,正在关闭...")
    finally:
        backend.stop()
        print("[Proxy] 全部停止,再见 👋")


if __name__ == "__main__":
    main()
