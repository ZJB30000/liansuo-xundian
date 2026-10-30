"""登录鉴权接口（阶段3）：POST /api/auth/login、GET /api/auth/me。"""

import json

from fastapi import APIRouter, Depends, Request

from my_tool.api import db
from my_tool.api.errors import ApiError
from my_tool.api.security import create_token, get_current_user

router = APIRouter()


@router.post("/api/auth/login")
async def login(request: Request):
    """登录：用户名+密码 → JWT。失败返回 401 中文信息。"""
    try:
        body = await request.json()
    except json.JSONDecodeError:
        raise ApiError(400, "BODY_NOT_JSON", "请求体不是合法 JSON")
    username = (body or {}).get("username")
    password = (body or {}).get("password")
    if not isinstance(username, str) or not isinstance(password, str) or not username or not password:
        raise ApiError(400, "INVALID_PARAM", "用户名和密码不能为空")

    user = db.get_user_by_name(username.strip())
    if user is None or not db.verify_password(password, user["password_hash"]):
        raise ApiError(401, "LOGIN_FAILED", "用户名或密码错误")

    return {
        "code": "OK",
        "token": create_token(user["id"], user["username"], user["role"]),
        "username": user["username"],
        "role": user["role"],
    }


@router.get("/api/auth/me")
async def me(user: dict = Depends(get_current_user)):
    """返回当前登录用户与角色（接口级鉴权的演示端点）。"""
    return {"code": "OK", **user}
