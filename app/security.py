"""安全模块 — 认证、输入验证、错误处理"""
import os
import re
import logging
import base64
import hashlib
import hmac
import json
import secrets
import time
from fastapi import Header, HTTPException, Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response

AUTH_ENABLED = os.getenv("AUTH_ENABLED", "true").strip().lower() not in {"0", "false", "no", "off"}
AUTH_USERNAME = os.getenv("AUTH_USERNAME", "admin")
AUTH_PASSWORD = os.getenv("AUTH_PASSWORD", "admin123")
# Keep sessions valid across normal service restarts when an explicit secret
# is not configured. Changing the username/password still invalidates tokens.
AUTH_SECRET = os.getenv("AUTH_SECRET") or hashlib.sha256(
    f"pharmacost:{AUTH_USERNAME}:{AUTH_PASSWORD}".encode("utf-8")
).hexdigest()
AUTH_COOKIE = "pharmacost_session"
AUTH_SESSION_TTL = int(os.getenv("AUTH_SESSION_TTL", str(8 * 60 * 60)))

logger = logging.getLogger("security")

API_KEY = os.getenv("API_KEY", "")

# These defaults keep validation available before the data service has loaded.
# Once loaded, validate_product/month use the dimensions derived from the
# active CSV resources so imported products and month ranges are accepted.
VALID_PRODUCTS = {"银黄口服液", "板蓝根颗粒", "六味地黄胶囊"}
VALID_MONTHS = {f"2026-{m:02d}" for m in range(1, 7)}
VALID_MONTHS_2025 = {f"2025-{m:02d}" for m in range(1, 7)}
ALL_VALID_MONTHS = VALID_MONTHS | VALID_MONTHS_2025

# Product names come from uploaded CSV dimensions and may include dosage
# numbers, spaces, or common specification separators.
_VALID_PRODUCT_RE = re.compile(r'^[\w\u4e00-\u9fff（）()×*+./\- ]+$', re.UNICODE)
_VALID_MONTH_RE = re.compile(r'^\d{4}-\d{2}$')
_VALID_TASK_ID_RE = re.compile(r'^[A-Za-z0-9][A-Za-z0-9-]{7,31}$')


def create_session_token(username: str) -> str:
    payload = {"sub": username, "exp": int(time.time()) + AUTH_SESSION_TTL}
    body = base64.urlsafe_b64encode(json.dumps(payload, separators=(",", ":")).encode()).rstrip(b"=").decode()
    signature = hmac.new(AUTH_SECRET.encode(), body.encode(), hashlib.sha256).digest()
    return f"{body}.{base64.urlsafe_b64encode(signature).rstrip(b'=').decode()}"


def get_session_user(token: str | None) -> str | None:
    if not token or "." not in token:
        return None
    body, encoded_signature = token.split(".", 1)
    expected = hmac.new(AUTH_SECRET.encode(), body.encode(), hashlib.sha256).digest()
    try:
        signature = base64.urlsafe_b64decode(encoded_signature + "=" * (-len(encoded_signature) % 4))
        payload = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
    except (ValueError, TypeError, json.JSONDecodeError):
        return None
    if not hmac.compare_digest(signature, expected):
        return None
    if payload.get("exp", 0) <= int(time.time()):
        return None
    return str(payload.get("sub")) if payload.get("sub") else None


def validate_product(product: str) -> str:
    if not product or len(product) > 50:
        raise HTTPException(status_code=400, detail="产品名称参数无效")
    valid_products = _runtime_products()
    if product not in valid_products:
        raise HTTPException(status_code=400, detail=f"不支持的产品名称: {product}")
    if not _VALID_PRODUCT_RE.match(product):
        raise HTTPException(status_code=400, detail="产品名称格式无效")
    return product


def validate_month(month: str) -> str:
    if not month or len(month) != 7:
        raise HTTPException(status_code=400, detail="月份参数无效")
    if not _VALID_MONTH_RE.match(month):
        raise HTTPException(status_code=400, detail="月份格式无效,应为YYYY-MM")
    if month not in _runtime_months():
        raise HTTPException(status_code=400, detail=f"不支持的月份: {month}")
    return month


def _runtime_products() -> set[str]:
    """Return product names from the active data, with startup defaults."""
    try:
        from data.cost_data import cost_service
        products = {
            str(item.get("name", "") if isinstance(item, dict) else item).strip()
            for item in cost_service.get_products()
        }
        return {item for item in products if item} or VALID_PRODUCTS
    except Exception:
        return VALID_PRODUCTS


def _runtime_months() -> set[str]:
    """Return supported months from the active data, with startup defaults."""
    try:
        from data.cost_data import cost_service
        months = {str(item).strip() for item in cost_service.get_months()}
        return {item for item in months if item} or ALL_VALID_MONTHS
    except Exception:
        return ALL_VALID_MONTHS


def validate_task_id(task_id: str) -> str:
    if not task_id or not _VALID_TASK_ID_RE.match(task_id):
        raise HTTPException(status_code=400, detail="任务ID格式无效")
    return task_id


class APIKeyMiddleware(BaseHTTPMiddleware):
    """API Key 认证中间件 — 保护所有 /api/* 端点"""

    SKIP_PATHS = {"/health", "/health/live", "/health/ready", "/", "/favicon.ico", "/api/products", "/api/auth/login", "/api/auth/me", "/api/auth/logout"}

    async def dispatch(self, request: Request, call_next):
        path = request.url.path

        if path in self.SKIP_PATHS or not path.startswith("/api/"):
            return await call_next(request)

        if API_KEY and path.startswith("/api/"):
            api_key = request.headers.get("X-API-Key") or request.query_params.get("api_key")
            if not api_key:
                api_key = request.headers.get("x-api-key")
            if api_key != API_KEY:
                logger.warning("认证失败: %s", path)
                return JSONResponse(
                    status_code=401,
                    content={"detail": "未授权访问,请提供有效的API Key"},
                )

        return await call_next(request)


class AuthMiddleware(BaseHTTPMiddleware):
    """会话认证中间件，登录/健康检查等公共接口除外。"""

    PUBLIC_PATHS = {
        "/api/auth/login", "/api/auth/me", "/api/auth/logout",
        "/health", "/health/live", "/health/ready", "/", "/favicon.ico",
    }

    async def dispatch(self, request: Request, call_next):
        if not AUTH_ENABLED or request.url.path in self.PUBLIC_PATHS or not request.url.path.startswith("/api/"):
            return await call_next(request)
        username = get_session_user(request.cookies.get(AUTH_COOKIE))
        if not username:
            return JSONResponse(status_code=401, content={"detail": "请先登录"})
        request.state.user = username
        return await call_next(request)


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """安全响应头中间件"""

    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-XSS-Protection"] = "1; mode=block"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; "
            "script-src 'self' 'unsafe-inline'; "
            "style-src 'self' 'unsafe-inline'; "
            "font-src 'self'; "
            "img-src 'self' data:; "
            "connect-src 'self' http://localhost:*;"
        )
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        return response


def setup_exception_handlers(app):
    """注册全局异常处理器"""

    @app.exception_handler(HTTPException)
    async def http_exception_handler(request: Request, exc: HTTPException):
        logger.warning("HTTP错误 %s: %s", exc.status_code, exc.detail)
        return JSONResponse(
            status_code=exc.status_code,
            content={"detail": exc.detail},
        )

    @app.exception_handler(Exception)
    async def global_exception_handler(request: Request, exc: Exception):
        logger.error("未处理异常: %s", str(exc), exc_info=True)
        return JSONResponse(
            status_code=500,
            content={"detail": "服务器内部错误,请联系管理员"},
        )


def setup_security(app):
    """注册安全中间件（含API Key认证）"""
    app.add_middleware(SecurityHeadersMiddleware)
    app.add_middleware(APIKeyMiddleware)
    setup_exception_handlers(app)
