"""登录、会话查询与退出接口。"""
from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, Field

from security import (
    AUTH_COOKIE, AUTH_ENABLED, AUTH_PASSWORD, AUTH_SESSION_TTL, AUTH_USERNAME,
    create_session_token, get_session_user,
)

router = APIRouter()


class LoginRequest(BaseModel):
    username: str = Field(..., min_length=1, max_length=64)
    password: str = Field(..., min_length=1, max_length=128)


@router.post("/login")
async def login(payload: LoginRequest, response: Response):
    if not AUTH_ENABLED:
        response.set_cookie(AUTH_COOKIE, create_session_token(AUTH_USERNAME), httponly=True, samesite="lax", max_age=AUTH_SESSION_TTL)
        return {"authenticated": True, "username": AUTH_USERNAME}
    if payload.username != AUTH_USERNAME or payload.password != AUTH_PASSWORD:
        raise HTTPException(status_code=401, detail="账号或密码错误")
    response.set_cookie(
        AUTH_COOKIE, create_session_token(payload.username), httponly=True,
        samesite="lax", max_age=AUTH_SESSION_TTL, secure=False,
    )
    return {"authenticated": True, "username": payload.username}


@router.get("/me")
async def me(request: Request):
    username = get_session_user(request.cookies.get(AUTH_COOKIE))
    return {"authenticated": bool(username), "username": username}


@router.post("/logout")
async def logout(response: Response):
    response.delete_cookie(AUTH_COOKIE, httponly=True, samesite="lax")
    return {"authenticated": False}
