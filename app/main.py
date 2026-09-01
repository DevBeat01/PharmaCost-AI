"""FastAPI应用入口"""
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, Response
from pathlib import Path
import sys
import os
import logging

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("main")

sys.path.insert(0, str(Path(__file__).resolve().parent))

from data.cost_data import cost_service
from routers import dashboard, report, benchmark, rpa_router, settings

app = FastAPI(
    title="制药成本智能分析报告系统",
    description="基于RAG+LLM的制药企业产品成本智能分析",
    version="1.0.0"
)

API_KEY = os.getenv("API_KEY", "")
from security import SecurityHeadersMiddleware, setup_exception_handlers

app.add_middleware(SecurityHeadersMiddleware)
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


@app.on_event("startup")
async def startup():
    """启动时加载数据和构建知识库"""
    cost_service.load_all()
    logger.info("数据加载完成")

    import threading

    def _build_kb():
        try:
            from rag.build_knowledge import build_knowledge_base
            build_knowledge_base()
            logger.info("知识库构建完成")
        except Exception:
            logger.warning("知识库构建警告（非致命）", exc_info=True)

    # 构建在后台线程执行，避免嵌入模型初始化阻塞 API 接口启动。
    threading.Thread(target=_build_kb, daemon=True, name="knowledge-index-builder").start()
    logger.info("知识库检查已后台启动（已有索引将复用，本次启动不阻塞接口）")


@app.get("/")
async def root():
    """返回前端主页"""
    return FileResponse(str(static_dir / "index.html"))


@app.get("/favicon.ico")
async def favicon():
    """内联SVG favicon,避免404"""
    svg = (
        "<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'>"
        "<text y='.9em' font-size='90'>💊</text></svg>"
    )
    return Response(
        content=svg.encode("utf-8"),
        media_type="image/svg+xml",
        headers={"Cache-Control": "public, max-age=86400"},
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
