"""FastAPI应用入口"""
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pathlib import Path
import sys
import os
import logging
import asyncio
import subprocess
import urllib.request
import urllib.error
import time

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("main")

sys.path.insert(0, str(Path(__file__).resolve().parent))

from data.cost_data import cost_service
from routers import dashboard, report, benchmark, rpa_router, settings, auth
from config import RPA_BASE_URL

app = FastAPI(
    title="制药成本智能分析报告系统",
    description="基于RAG+LLM的制药企业产品成本智能分析",
    version="1.0.0"
)

API_KEY = os.getenv("API_KEY", "")
from security import SecurityHeadersMiddleware, setup_exception_handlers

app.add_middleware(SecurityHeadersMiddleware)
from security import AuthMiddleware
app.add_middleware(AuthMiddleware)
setup_exception_handlers(app)

if API_KEY:
    from security import APIKeyMiddleware
    app.add_middleware(APIKeyMiddleware)
    logger.info("API Key 认证已启用")
else:
    logger.warning("未配置API_KEY, 所有API端点将无认证保护 (仅开发环境使用)")

# 挂载静态文件
static_dir = Path(__file__).resolve().parent / "static"
app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

# 注册路由
app.include_router(dashboard.router, prefix="/api/dashboard", tags=["看板"])
app.include_router(report.router, prefix="/api/report", tags=["报告"])
app.include_router(benchmark.router, prefix="/api/benchmark", tags=["对标"])
app.include_router(rpa_router.router, prefix="/api/rpa", tags=["RPA"])
app.include_router(settings.router, prefix="/api/settings", tags=["设置"])
app.include_router(auth.router, prefix="/api/auth", tags=["认证"])


@app.on_event("startup")
async def startup():
    """启动时加载数据和构建知识库"""
    cost_service.load_all()
    logger.info("数据加载完成")
    try:
        from analysis.dashboard import (
            _PRIMARY_LLM_FIRST_CHUNK_TIMEOUT_SECONDS,
            _BACKUP_LLM_FIRST_CHUNK_TIMEOUT_SECONDS,
            _PRIMARY_LLM_TEXT_TIMEOUT_SECONDS,
            _BACKUP_LLM_TEXT_TIMEOUT_SECONDS,
        )
        logger.info(
            "归因模型超时配置: 主模型首事件%.1fs/总计%.1fs，备用模型首事件%.1fs/总计%.1fs",
            _PRIMARY_LLM_FIRST_CHUNK_TIMEOUT_SECONDS,
            _PRIMARY_LLM_TEXT_TIMEOUT_SECONDS,
            _BACKUP_LLM_FIRST_CHUNK_TIMEOUT_SECONDS,
            _BACKUP_LLM_TEXT_TIMEOUT_SECONDS,
        )
    except Exception:
        logger.exception("归因模型超时配置读取失败")

    # 直接运行 `uvicorn app.main:app` 时不会经过 start.bat。开发/演示环境
    # 若使用本地 Mock RPA，应用自身负责探测并拉起它，避免派发必然失败。
    # 测试进程不启动后台服务，避免占用 8090 并污染测试环境。
    if "pytest" not in sys.modules and not os.getenv("PHARMACOST_DISABLE_RPA_AUTOSTART"):
        await asyncio.to_thread(_ensure_local_rpa)

    # 复用设置模块的单任务调度器，启动检查与上传/删除不会并发写索引。
    settings.request_knowledge_build("startup")
    logger.info("知识库检查已后台启动（已有索引将复用，本次启动不阻塞接口）")


def _ensure_local_rpa() -> None:
    """在本地默认地址不可达时启动项目自带 RPA Mock。"""
    base_url = str(RPA_BASE_URL or "").rstrip("/")
    if base_url not in {"http://127.0.0.1:8090", "http://localhost:8090"}:
        return
    health_url = f"{base_url}/health"
    try:
        with urllib.request.urlopen(health_url, timeout=0.6) as response:
            if response.status < 500:
                logger.info("RPA服务已就绪: %s", base_url)
                return
    except (OSError, urllib.error.URLError):
        pass

    project_root = Path(__file__).resolve().parent.parent
    mock_script = project_root / "rpa_mock" / "mock_rpa_server.py"
    if not mock_script.exists():
        logger.warning("RPA服务不可达且未找到本地 Mock 服务: %s", mock_script)
        return
    try:
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        subprocess.Popen(
            [sys.executable, str(mock_script)],
            cwd=str(project_root),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=creationflags,
        )
        for _ in range(20):
            try:
                with urllib.request.urlopen(health_url, timeout=0.5) as response:
                    if response.status < 500:
                        logger.info("已自动启动本地 RPA Mock 服务: %s", base_url)
                        return
            except (OSError, urllib.error.URLError):
                time.sleep(0.25)
        logger.warning("本地 RPA Mock 服务启动超时，请检查端口 8090")
    except OSError:
        logger.warning("无法自动启动本地 RPA Mock 服务", exc_info=True)


@app.get("/")
async def root():
    """返回前端主页"""
    return FileResponse(str(static_dir / "index.html"))


@app.get("/favicon.ico")
async def favicon():
    """返回应用 Logo 作为 favicon，兼容浏览器默认的 /favicon.ico 请求。"""
    return FileResponse(
        str(static_dir / "images" / "logo-concept-v1.svg"),
        media_type="image/svg+xml",
        headers={"Cache-Control": "public, max-age=86400", "X-Content-Type-Options": "nosniff"},
    )


@app.get("/api/products")
async def get_products():
    """获取产品列表"""
    return {"products": cost_service.get_products(), "months": cost_service.get_months()}


@app.get("/health")
async def health():
    return {"status": "ok", "live": True}


@app.get("/health/live")
async def health_live():
    return {"status": "ok"}


@app.get("/health/ready")
async def health_ready():
    """Dependency-aware readiness probe; process liveness remains /health/live."""
    checks = {"data": bool(cost_service._loaded), "output": False, "rag": {}}
    try:
        from routers.report import OUTPUT_DIR
        checks["output"] = OUTPUT_DIR.exists() and OUTPUT_DIR.is_dir()
    except Exception:
        checks["output"] = False
    try:
        from rag.vector_store import VectorStore
        checks["rag"] = VectorStore.status()
    except Exception as exc:
        checks["rag"] = {"embedding_model_ready": False, "error": str(exc)}
    ready = checks["data"] and checks["output"]
    return {"status": "ready" if ready else "degraded", "ready": ready, "checks": checks}
