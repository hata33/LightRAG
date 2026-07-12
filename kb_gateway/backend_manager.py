"""
后端 LightRAG server 实例管理

负责用 subprocess 启动/停止多个 LightRAG server 进程,
每个进程绑定不同端口 + 不同 workspace。

启动原理:
  LightRAG server 从环境变量读 PORT 和 WORKSPACE(config.py:263,376)。
  我们用 subprocess.Popen 启动子进程,通过 env 传不同配置。

  进程1: PORT=9621 WORKSPACE=tenant_finance
  进程2: PORT=9622 WORKSPACE=tenant_engineering
"""

import os
import signal
import subprocess
import sys
import time
import atexit
from typing import Any

import httpx

# workspace → 端口映射
# 端口选择:9621 是 LightRAG 默认端口,往后递增
WORKSPACE_PORTS: dict[str, int] = {
    "tenant_finance": 9621,
    "tenant_engineering": 9622,
}


class BackendManager:
    """
    管理多个 LightRAG server 后端进程。

    用法:
        mgr = BackendManager()
        mgr.start()          # 启动所有后端
        port = mgr.get_port("tenant_finance")  # → 9621
        mgr.stop()           # 停止所有后端
    """

    def __init__(self, log_dir: str = "./kb_gateway_logs"):
        self._processes: dict[str, subprocess.Popen] = {}  # workspace → 进程
        self._log_dir = log_dir
        self._log_files: dict[str, Any] = {}  # workspace → open file handle

    def start(self, timeout: float = 120.0):
        """
        启动所有 workspace 的后端 server,并等待就绪。

        Args:
            timeout: 等待所有后端就绪的最大秒数(LightRAG 首次启动较慢)
        """
        print("[BackendManager] 启动 LightRAG 后端实例...")

        # 准备日志目录(子进程输出写到这里,避免管道缓冲区满导致 hang)
        os.makedirs(self._log_dir, exist_ok=True)

        # 继承当前进程的环境变量(带 .env 里的 LLM/Embedding 配置)
        base_env = os.environ.copy()

        for workspace, port in WORKSPACE_PORTS.items():
            env = base_env.copy()
            env["PORT"] = str(port)
            env["WORKSPACE"] = workspace

            # 重定向 stdout/stderr 到日志文件(不用 PIPE,避免缓冲区满 hang 死)
            log_path = os.path.join(self._log_dir, f"{workspace}.log")
            log_file = open(log_path, "w", encoding="utf-8")

            print(f"  启动: workspace='{workspace}' → port={port} (日志: {log_path})")

            proc = subprocess.Popen(
                [
                    sys.executable, "-m", "lightrag.api.lightrag_server",
                ],
                env=env,
                stdout=log_file,
                stderr=subprocess.STDOUT,
                # Windows 下用新进程组,Ctrl+C 不会直接传给子进程
                creationflags=subprocess.CREATE_NEW_PROCESS_GROUP
                if sys.platform == "win32"
                else 0,
            )
            self._processes[workspace] = proc
            self._log_files[workspace] = log_file

        # 注册退出时自动清理
        atexit.register(self.stop)

        # 等待所有后端就绪
        self._wait_ready(timeout)

    def _wait_ready(self, timeout: float):
        """轮询每个后端的 /health 直到全部响应。"""
        print("[BackendManager] 等待后端就绪...", end="", flush=True)

        deadline = time.time() + timeout
        pending = set(WORKSPACE_PORTS.keys())

        while pending and time.time() < deadline:
            print(".", end="", flush=True)
            time.sleep(2)  # LightRAG server 启动较慢,每 2 秒检查一次

            for workspace in list(pending):
                port = WORKSPACE_PORTS[workspace]
                if self._check_health(port):
                    pending.discard(workspace)
                    print(f"\n  ✓ workspace='{workspace}' (port {port}) 就绪", end="")

        if pending:
            # 打印未就绪后端的日志,帮助排查
            self._dump_failed_logs(pending)
            raise RuntimeError(
                f"后端未在 {timeout}s 内就绪: {pending}"
            )

        print("\n[BackendManager] 所有后端就绪 ✓")

    def _check_health(self, port: int) -> bool:
        """检查某个端口的后端是否响应 /health。"""
        try:
            resp = httpx.get(f"http://127.0.0.1:{port}/health", timeout=3.0)
            return resp.status_code == 200
        except Exception:
            return False

    def _dump_failed_logs(self, failed_workspaces: set[str]):
        """打印启动失败的后端日志,帮助排查。"""
        for workspace in failed_workspaces:
            log_path = os.path.join(self._log_dir, f"{workspace}.log")
            print(f"\n--- {workspace} 日志 ({log_path}) ---")
            try:
                with open(log_path, "r", encoding="utf-8") as f:
                    content = f.read()
                    # 打印最后 2000 字符
                    print(content[-2000:] if len(content) > 2000 else content)
            except Exception as e:
                print(f"  (无法读取日志: {e})")

    def get_port(self, workspace: str) -> int:
        """workspace → 端口"""
        port = WORKSPACE_PORTS.get(workspace)
        if port is None:
            raise ValueError(f"未知 workspace: {workspace}")
        return port

    def stop(self):
        """终止所有后端进程。"""
        if not self._processes:
            return

        print("\n[BackendManager] 停止所有后端...")

        for workspace, proc in self._processes.items():
            if proc.poll() is None:  # 进程还活着
                print(f"  终止: workspace='{workspace}' (pid={proc.pid})")
                try:
                    if sys.platform == "win32":
                        # Windows: send CTRL_BREAK to process group
                        proc.send_signal(signal.CTRL_BREAK_EVENT)
                    else:
                        proc.terminate()
                    proc.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    print(f"    强制 kill: workspace='{workspace}'")
                except Exception as e:
                    print(f"    停止失败: {e}")

        self._processes.clear()

        # 关闭日志文件
        for f in self._log_files.values():
            try:
                f.close()
            except Exception:
                pass
        self._log_files.clear()

        print("[BackendManager] 所有后端已停止")
