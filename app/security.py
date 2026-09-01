"""安全模块 — 认证、输入验证、错误处理"""
import os
import re
import logging
from fastapi import Header, HTTPException, Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response

logger = logging.getLogger("security")

API_KEY = os.getenv("API_KEY", "")

VALID_PRODUCTS = {"银黄口服液", "板蓝根颗粒", "六味地黄胶囊"}
VALID_MONTHS = {f"2026-{m:02d}" for m in range(1, 7)}
VALID_MONTHS_2025 = {f"2025-{m:02d}" for m in range(1, 7)}
ALL_VALID_MONTHS = VALID_MONTHS | VALID_MONTHS_2025

_VALID_PRODUCT_RE = re.compile(r'^[a-zA-Z\u4e00-\u9fff]+$')
_VALID_MONTH_RE = re.compile(r'^\d{4}-\d{2}$')
_VALID_TASK_ID_RE = re.compile(r'^[A-Za-z0-9][A-Za-z0-9-]{7,31}$')


def validate_product(product: str) -> str:
    if not product or len(product) > 50:
        raise HTTPException(status_code=400, detail="产品名称参数无效")
    if product not in VALID_PRODUCTS:
        raise HTTPException(status_code=400, detail=f"不支持的产品名称: {product}")
    if not _VALID_PRODUCT_RE.match(product):
        raise HTTPException(status_code=400, detail="产品名称格式无效")
    return product


def validate_month(month: str) -> str:
    if not month or len(month) != 7:
        raise HTTPException(status_code=400, detail="月份参数无效")
    if not _VALID_MONTH_RE.match(month):
        raise HTTPException(status_code=400, detail="月份格式无效,应为YYYY-MM")
    if month not in ALL_VALID_MONTHS:
        raise HTTPException(status_code=400, detail=f"不支持的月份: {month}")
    return month


def validate_task_id(task_id: str) -> str:
    if not task_id or not _VALID_TASK_ID_RE.match(task_id):
        raise HTTPException(status_code=400, detail="任务ID格式无效")
    return task_id


class APIKeyMiddleware(BaseHTTPMiddleware):
    """API Key 认证中间件 — 保护所有 /api/* 端点"""

    SKIP_PATHS = {"/health", "/", "/favicon.ico", "/api/products"}

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
