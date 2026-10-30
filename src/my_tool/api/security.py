"""鉴权与权限（阶段3）：PBKDF2 密码、JWT 签发/校验、接口级权限依赖。

课程项目约定：JWT 密钥用环境变量 JWT_SECRET，缺省值仅用于本地演示，
生产环境必须显式配置。
"""

import os
import time
from typing import Optional

import jwt                                    # PyJWT
from fastapi import Depends, Request

from my_tool.api.errors import ApiError

# JWT 配置：HS256，12 小时有效
JWT_SECRET = os.environ.get("JWT_SECRET", "course-demo-secret-change-me")
JWT_EXPIRE_HOURS = 12


def create_token(user_id: int, username: str, role: str) -> str:
    """签发 JWT：载荷带用户ID、用户名、角色与过期时间。"""
    payload = {
        "sub": str(user_id),
        "username": username,
        "role": role,
        "exp": int(time.time()) + JWT_EXPIRE_HOURS * 3600,
    }
    return jwt.encode(payload, JWT_SECRET, algorithm="HS256")


def decode_token(token: str) -> dict:
    """校验 JWT，失败抛 401（中文信息）。"""
    try:
        return jwt.decode(token, JWT_SECRET, algorithms=["HS256"])
    except jwt.ExpiredSignatureError:
        raise ApiError(401, "TOKEN_EXPIRED", "登录已过期，请重新登录")
    except Exception:
        raise ApiError(401, "TOKEN_INVALID", "登录凭证无效，请重新登录")


def _bearer_token(request: Request) -> str:
    """从 Authorization: Bearer <token> 里取令牌。"""
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        raise ApiError(401, "UNAUTHORIZED", "未登录，请先登录")
    return auth[7:].strip()


def get_current_user(request: Request) -> dict:
    """FastAPI 依赖：要求任意已登录用户（员工或管理员）。"""
    payload = decode_token(_bearer_token(request))
    return {"user_id": int(payload["sub"]), "username": payload["username"], "role": payload["role"]}


def require_admin(user: dict = Depends(get_current_user)) -> dict:
    """FastAPI 依赖：要求管理员角色，否则 403。"""
    if user["role"] != "admin":
        raise ApiError(403, "FORBIDDEN", "仅管理员可执行此操作")
    return user


def optional_user(request: Request) -> Optional[dict]:
    """可选鉴权：未登录返回 None（给首页等公共页面用）。"""
    try:
        return get_current_user(request)
    except ApiError:
        return None
