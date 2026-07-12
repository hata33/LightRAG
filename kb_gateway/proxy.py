"""
代理主程序 — FastAPI 反向代理

职责:
  1. 提供自己的 /auth/login 接口,签发 JWT
  2. 对所有转发请求验证 JWT,拿到 user_id
  3. user_id → workspace → 后端端口,用 httpx 转发
  4. 透传响应(包括流式)

转发策略:
  对客户端完全透明 —— 路径、请求体、响应体都原样转发,
  只在中间加了一层身份认证 + workspace 路由。

后端 API 参考(来自 lightrag/api/routers/):
  POST   /query                 查询(非流式)
  POST   /query/stream          查询(流式 NDJSON)
  POST   /query/data            查询(返回结构化数据)
  POST   /documents/text        插入文本
  POST   /documents/texts       批量插入
  POST   /documents/upload      上传文件(multipart)
  DELETE /documents             清空
  GET    /documents/*           文档状态相关
  GET/POST /graph/*             图谱相关
  POST   /api/chat              Ollama 兼容聊天
"""

from typing import Any
import json

import httpx
from fastapi import FastAPI, Request, Response, HTTPException, Depends
from fastapi.responses import StreamingResponse, JSONResponse
from pydantic import BaseModel

from kb_gateway.auth import (
    authenticate,
    create_access_token,
    verify_token,
    get_user_workspace,
)
from kb_gateway.backend_manager import BackendManager
from kb_gateway.spicedb_client import (
    get_engine,
    file_path_to_doc_id,
)

app = FastAPI(
    title="KB Gateway Proxy",
    description="多 workspace 路由代理 —— 练习 1 方案 C",
    version="0.1.0",
)

# 全局后端管理器(由 run_proxy.py 注入)
_backend_manager: BackendManager | None = None


def set_backend_manager(mgr: BackendManager):
    """run_proxy.py 启动时调用,注入后端管理器。"""
    global _backend_manager
    _backend_manager = mgr


def _get_backend() -> BackendManager:
    if _backend_manager is None:
        raise RuntimeError("BackendManager 未初始化,请用 run_proxy.py 启动")
    return _backend_manager


# ──────────────────────────────────────────────────────────────
# 1. 认证接口(代理自己的)
# ──────────────────────────────────────────────────────────────

class LoginRequest(BaseModel):
    username: str
    password: str


@app.post("/auth/login")
async def login(req: LoginRequest):
    """
    用户登录,返回 JWT。

    客户端拿到 token 后,所有后续请求带:
        Authorization: Bearer <token>
    """
    user_id = authenticate(req.username, req.password)
    if user_id is None:
        raise HTTPException(status_code=401, detail="用户名或密码错误")

    token = create_access_token(user_id)
    workspace = get_user_workspace(user_id)

    return {
        "access_token": token,
        "token_type": "bearer",
        "user": user_id,
        "workspace": workspace,
    }


# ──────────────────────────────────────────────────────────────
# 2. 认证依赖 —— 所有转发接口都用
# ──────────────────────────────────────────────────────────────

async def require_user(request: Request) -> str:
    """
    从 Authorization header 提取并验证 JWT,返回 user_id。

    所有需要认证的转发接口都 Depends(require_user)。
    """
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="缺少 Authorization: Bearer <token>")

    token = auth_header[7:]  # 去掉 "Bearer "
    user_id = verify_token(token)
    if user_id is None:
        raise HTTPException(status_code=401, detail="token 无效或已过期")

    return user_id


def _resolve_backend_port(user_id: str) -> int:
    """user_id → workspace → 后端端口"""
    workspace = get_user_workspace(user_id)
    if workspace is None:
        raise HTTPException(status_code=403, detail=f"用户 '{user_id}' 未分配 workspace")

    backend = _get_backend()
    try:
        return backend.get_port(workspace)
    except ValueError:
        raise HTTPException(status_code=500, detail=f"workspace '{workspace}' 无对应后端")


# ──────────────────────────────────────────────────────────────
# 3. 通用转发逻辑
# ──────────────────────────────────────────────────────────────

# 不需要转发的 hop-by-hop headers
_HOP_HEADERS = {
    "host", "transfer-encoding", "connection", "keep-alive",
    "proxy-authenticate", "proxy-authorization", "te", "trailers",
    "upgrade",
}

# 需要流式响应的路径(返回 NDJSON,不能缓冲)
_STREAMING_PATHS = {"/query/stream"}


async def _forward(
    request: Request,
    user_id: str,
    path: str,
    *,
    is_stream: bool = False,
) -> Response:
    """
    核心转发函数。

    Args:
        request: 原始请求
        user_id: 已认证的用户 ID(从 JWT 解出)
        path: 要转发到的后端路径
        is_stream: 是否流式响应(NDJSON)
    """
    port = _resolve_backend_port(user_id)
    url = f"http://127.0.0.1:{port}{path}"

    # 准备 headers:去掉 hop-by-hop 和 Authorization
    fwd_headers = {}
    for key, value in request.headers.items():
        if key.lower() not in _HOP_HEADERS and key.lower() != "authorization":
            fwd_headers[key] = value

    # 读取请求体
    body = await request.body()

    if is_stream:
        return await _forward_stream(url, request.method, fwd_headers, body, port)
    else:
        return await _forward_normal(url, request.method, fwd_headers, body, port)


async def _forward_normal(url, method, headers, body, port) -> Response:
    """非流式转发:等待完整响应后返回。"""
    async with httpx.AsyncClient() as client:
        try:
            resp = await client.request(
                method,
                url,
                headers=headers,
                content=body,
                timeout=300.0,  # LLM 调用可能很慢
            )
        except httpx.ConnectError:
            raise HTTPException(
                status_code=502,
                detail=f"无法连接后端 (port {port})",
            )

    # 构造响应:透传状态码、body、大部分 headers
    resp_headers = {}
    for key, value in resp.headers.items():
        if key.lower() not in _HOP_HEADERS:
            resp_headers[key] = value

    return Response(
        content=resp.content,
        status_code=resp.status_code,
        headers=resp_headers,
        media_type=resp.headers.get("content-type"),
    )


async def _forward_stream(url, method, headers, body, port) -> StreamingResponse:
    """流式转发:逐块透传,不缓冲。用于 /query/stream 等 NDJSON 接口。"""

    async def generate():
        async with httpx.AsyncClient() as client:
            async with client.stream(
                method, url, headers=headers, content=body, timeout=300.0
            ) as resp:
                async for chunk in resp.aiter_bytes():
                    yield chunk

    return StreamingResponse(
        generate(),
        media_type="application/x-ndjson",
    )


# ──────────────────────────────────────────────────────────────
# 4. 文档级 ACL 过滤(练习 2 新增)
# ──────────────────────────────────────────────────────────────

def _filter_references_by_acl(user_id: str, resp_json: dict) -> dict:
    """
    对查询响应中的 references 做文档级 ACL 过滤。

    LightRAG 的 /query 响应格式(QueryResponse):
        { "response": "...", "references": [ {"file_path": "xxx", ...}, ... ] }

    对每个 reference 的 file_path:
      1. 转成 SpiceDB document ID(file_path_to_doc_id)
      2. 问权限引擎:user 能 view 这个文档吗?
      3. 不能 → 从 references 列表里移除

    ⚠️ Post-fetch 过滤的局限:
      这只过滤了引用列表。如果 LLM 的回答文本里直接写了薪资数字,
      文本内容不会被过滤(因为文本已经生成了)。
      要彻底解决需要练习 3 的向量层 pre-filter。
    """
    references = resp_json.get("references")
    if not references:
        return resp_json  # 没有 references,不需要过滤

    engine = get_engine()
    filtered_refs = []
    denied_docs = []

    for ref in references:
        file_path = ref.get("file_path", "")
        doc_id = file_path_to_doc_id(file_path)

        if engine.can_view_document(user_id, doc_id):
            filtered_refs.append(ref)
        else:
            denied_docs.append(doc_id)

    resp_json["references"] = filtered_refs

    # 如果有被拒的文档,在响应里加一个标记(方便调试)
    if denied_docs:
        resp_json["_acl_denied_documents"] = denied_docs
        # 如果所有引用都被拒了,改写回答
        if not filtered_refs:
            resp_json["response"] = (
                "⚠️ 权限拒绝:您没有访问相关文档的权限。"
                f"(被拒文档: {', '.join(denied_docs)})"
            )

    return resp_json


def _filter_query_data_by_acl(user_id: str, resp_json: dict) -> dict:
    """
    对 /query/data 的结构化响应做 ACL 过滤。

    QueryDataResponse 格式:
        { "data": { "chunks": [...], "references": [...], ... } }

    chunks 里每个项也有 file_path,需要过滤。
    """
    data = resp_json.get("data", {})
    engine = get_engine()

    # 过滤 references
    references = data.get("references", [])
    if references:
        data["references"] = [
            ref for ref in references
            if engine.can_view_document(user_id, file_path_to_doc_id(ref.get("file_path", "")))
        ]

    # 过滤 chunks(每个 chunk 有 file_path)
    chunks = data.get("chunks", [])
    if chunks:
        data["chunks"] = [
            chunk for chunk in chunks
            if engine.can_view_document(user_id, file_path_to_doc_id(chunk.get("file_path", "")))
        ]

    resp_json["data"] = data
    return resp_json


async def _forward_with_acl(
    request: Request,
    user_id: str,
    path: str,
    filter_fn=None,
) -> Response:
    """
    转发 query 请求,集成两层 ACL 防御:
      层1 (pre-filter): 把 ACL 白名单注入请求体,后端检索时就排除越权文档
      层2 (post-fetch): 后端返回后,再过滤 references/chunks(双保险)

    流程:
      1. 查权限引擎,拿 user_id 的 allowed_doc_ids
      2. 注入到请求体的 acl_allowed_doc_ids 字段
      3. 转发给后端(后端 pre-filter 生效)
      4. 拿到响应,post-fetch 过滤(双保险)
      5. 返回
    """
    port = _resolve_backend_port(user_id)
    url = f"http://127.0.0.1:{port}{path}"

    fwd_headers = {}
    for key, value in request.headers.items():
        # 跳过 hop-by-hop、authorization、content-length
        # content-length 要跳过:我们会改写 body(注入 ACL),长度会变
        # 让 httpx 自己根据新 body 重算
        if key.lower() in _HOP_HEADERS or key.lower() == "authorization":
            continue
        if key.lower() == "content-length":
            continue
        fwd_headers[key] = value

    body = await request.body()

    # ── 层1: pre-filter —— 注入 ACL 白名单 ──
    engine = get_engine()
    allowed_doc_ids = engine.get_viewable_documents(user_id)
    if allowed_doc_ids is not None:
        try:
            body_json = json.loads(body)
            body_json["acl_allowed_doc_ids"] = list(allowed_doc_ids)
            body = json.dumps(body_json, ensure_ascii=False).encode()
            logger_info = f"[Proxy ACL] user={user_id} injected {len(allowed_doc_ids)} allowed doc_ids"
            print(logger_info)
        except (json.JSONDecodeError, UnicodeDecodeError):
            pass  # 非 JSON body(如 multipart),跳过注入

    async with httpx.AsyncClient() as client:
        try:
            resp = await client.request(
                "POST", url, headers=fwd_headers, content=body, timeout=300.0,
            )
        except httpx.ConnectError:
            raise HTTPException(status_code=502, detail=f"无法连接后端 (port {port})")

    # ACL 过滤
    if filter_fn and resp.status_code == 200:
        try:
            resp_json = resp.json()
            resp_json = filter_fn(user_id, resp_json)
            return Response(
                content=json.dumps(resp_json, ensure_ascii=False).encode(),
                status_code=resp.status_code,
                media_type="application/json",
            )
        except Exception:
            # JSON 解析失败,返回原始响应
            pass

    # 非 200 或过滤失败,原样返回
    resp_headers = {}
    for key, value in resp.headers.items():
        if key.lower() not in _HOP_HEADERS:
            resp_headers[key] = value

    return Response(
        content=resp.content,
        status_code=resp.status_code,
        headers=resp_headers,
        media_type=resp.headers.get("content-type"),
    )


# ──────────────────────────────────────────────────────────────
# 5. 路由定义 —— 所有 LightRAG API 都经过这里
# ──────────────────────────────────────────────────────────────

# --- Query 路由(带文档级 ACL 过滤) ---

@app.post("/query")
async def proxy_query(request: Request, user_id: str = Depends(require_user)):
    return await _forward_with_acl(request, user_id, "/query", _filter_references_by_acl)


@app.post("/query/stream")
async def proxy_query_stream(request: Request, user_id: str = Depends(require_user)):
    return await _forward(request, user_id, "/query/stream", is_stream=True)


@app.post("/query/data")
async def proxy_query_data(request: Request, user_id: str = Depends(require_user)):
    return await _forward_with_acl(request, user_id, "/query/data", _filter_query_data_by_acl)


# --- Document 路由 ---

@app.post("/documents/text")
async def proxy_doc_text(request: Request, user_id: str = Depends(require_user)):
    return await _forward(request, user_id, "/documents/text")


@app.post("/documents/texts")
async def proxy_doc_texts(request: Request, user_id: str = Depends(require_user)):
    return await _forward(request, user_id, "/documents/texts")


@app.post("/documents/upload")
async def proxy_doc_upload(request: Request, user_id: str = Depends(require_user)):
    return await _forward(request, user_id, "/documents/upload")


@app.delete("/documents")
async def proxy_doc_clear(request: Request, user_id: str = Depends(require_user)):
    return await _forward(request, user_id, "/documents")


@app.api_route(
    "/documents/{sub_path:path}",
    methods=["GET", "POST", "DELETE", "PUT", "PATCH"],
)
async def proxy_doc_sub(
    request: Request,
    sub_path: str,
    user_id: str = Depends(require_user),
):
    """捕获 /documents/ 下所有其他子路由(pipeline_status, paginated, status_counts 等)。"""
    return await _forward(request, user_id, f"/documents/{sub_path}")


# --- Graph 路由 ---

@app.api_route(
    "/graph/{sub_path:path}",
    methods=["GET", "POST", "DELETE", "PUT", "PATCH"],
)
async def proxy_graph(
    request: Request,
    sub_path: str,
    user_id: str = Depends(require_user),
):
    """转发所有 /graph/* 路由(含 DELETE /graph/entity/delete 等带 body 的)。"""
    return await _forward(request, user_id, f"/graph/{sub_path}")


# --- Ollama 兼容 API ---

@app.api_route(
    "/api/{sub_path:path}",
    methods=["GET", "POST"],
)
async def proxy_ollama(
    request: Request,
    sub_path: str,
    user_id: str = Depends(require_user),
):
    """转发 /api/* (Ollama 兼容接口)。/api/chat 的流式响应自动处理。"""
    target = f"/api/{sub_path}"
    is_stream = sub_path in ("chat", "generate")
    return await _forward(request, user_id, target, is_stream=is_stream)


# --- 健康检查(无需认证) ---

@app.get("/health")
async def health():
    """代理自身的健康检查。"""
    backend = _get_backend()
    status = {}
    for ws, port in {"tenant_finance": 9621, "tenant_engineering": 9622}.items():
        try:
            resp = httpx.get(f"http://127.0.0.1:{port}/health", timeout=3.0)
            status[ws] = f"up (port {port})"
        except Exception:
            status[ws] = f"down (port {port})"

    return {"proxy": "up", "backends": status}
