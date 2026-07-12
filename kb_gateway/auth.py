"""
代理层 JWT 签发与验证

这是代理自己维护的身份系统,和 LightRAG 后端的认证无关。
客户端向代理证明身份 → 代理签发 JWT → 后续请求带 JWT → 代理验签拿 user_id。

练习用:密码和 secret 写死。生产环境:
  - 密码用 bcrypt/argon2 存储(见 lightrag/api/passwords.py 参考)
  - JWT_SECRET 从环境变量或 KMS 读取
  - 用户表放数据库,不要写死
"""

import os
from datetime import datetime, timedelta, timezone

import jwt

# ── 配置(练习用,生产换环境变量) ───────────────────────────

JWT_SECRET = "kb-gateway-practice-secret-change-me!"
JWT_ALGORITHM = "HS256"
TOKEN_EXPIRE_HOURS = 24

# 用户表:username → {password, workspace}
# workspace 来自练习1的 USER_WORKSPACE_MAP
USERS: dict[str, dict] = {
    "alice": {"password": "alice123", "workspace": "tenant_finance"},
    "bob": {"password": "bob123", "workspace": "tenant_engineering"},
}


# ── JWT 签发 ────────────────────────────────────────────────

def create_access_token(user_id: str) -> str:
    """签发 JWT。payload 里放 user_id 和过期时间。"""
    payload = {
        "sub": user_id,                                          # subject = 用户名
        "exp": datetime.now(timezone.utc) + timedelta(hours=TOKEN_EXPIRE_HOURS),
        "iat": datetime.now(timezone.utc),                       # 签发时间
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def verify_token(token: str) -> str | None:
    """
    验证 JWT 签名 + 过期时间。

    Returns:
        user_id(验证通过) / None(验证失败)
    """
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        return payload.get("sub")
    except jwt.ExpiredSignatureError:
        return None
    except jwt.InvalidTokenError:
        return None


# ── 用户校验 ────────────────────────────────────────────────

def authenticate(username: str, password: str) -> str | None:
    """
    校验用户名密码。

    Returns:
        user_id(成功) / None(失败)
    """
    user = USERS.get(username)
    if user and user["password"] == password:
        return username
    return None


def get_user_workspace(user_id: str) -> str | None:
    """从 user_id 查所属 workspace。"""
    user = USERS.get(user_id)
    return user["workspace"] if user else None
