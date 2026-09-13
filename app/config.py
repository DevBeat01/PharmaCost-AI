"""集中配置管理"""
import os
from pathlib import Path
from dotenv import load_dotenv

# 无论从项目根目录还是 app 目录启动，都明确读取 app/.env。
load_dotenv(Path(__file__).resolve().parent / ".env")

# 项目根目录
BASE_DIR = Path(__file__).resolve().parent.parent
APP_DIR = Path(__file__).resolve().parent
# Docker 部署可通过 DATA_DIR=/data 将竞赛数据包作为运行时只读卷挂载。
DATA_DIR = Path(os.getenv("DATA_DIR", str(BASE_DIR / "创灵境_考题模拟数据")))

# 数据文件路径
COST_DATA_DIR = DATA_DIR / "01_成本明细数据"
INDUSTRY_DATA_DIR = DATA_DIR / "02_行业参考数据"
KNOWLEDGE_DIR = DATA_DIR / "03_制药知识文档"
TEMPLATE_DIR = DATA_DIR / "04_报告模板"
SETTINGS_DIR = APP_DIR / "settings"
DATA_UPLOAD_DIR = SETTINGS_DIR / "data_uploads"
KNOWLEDGE_UPLOAD_DIR = SETTINGS_DIR / "knowledge_uploads"
TEMPLATE_UPLOAD_DIR = SETTINGS_DIR / "template_uploads"
DATA_SOURCE_CONFIG_PATH = SETTINGS_DIR / "data_sources.json"
MODEL_CONFIG_PATH = SETTINGS_DIR / "model_config.json"

# CSV文件路径
CSV_COST_2026 = COST_DATA_DIR / "中药一厂_成本汇总_2026年1-6月.csv"
CSV_COST_2025 = COST_DATA_DIR / "中药一厂_成本汇总_2025年1-6月.csv"
CSV_MATERIAL = COST_DATA_DIR / "中药一厂_原材料消耗明细_2026年1-6月.csv"
CSV_OVERHEAD = COST_DATA_DIR / "中药一厂_制造费用明细_2026年1-6月.csv"
CSV_BUDGET = COST_DATA_DIR / "中药一厂_预算数据_2026年.csv"
CSV_LABOR = COST_DATA_DIR / "中药一厂_人工工时明细_2026年1-6月.csv"
CSV_BENCH_2026 = COST_DATA_DIR / "中药二厂_成本汇总_2026年1-6月.csv"
CSV_BENCH_2025 = COST_DATA_DIR / "中药二厂_成本汇总_2025年1-6月.csv"
CSV_MARKET = INDUSTRY_DATA_DIR / "药材市场价格行情_2026年上半年.csv"
CSV_INDUSTRY = INDUSTRY_DATA_DIR / "行业成本基准数据_2026.csv"

DATA_FILE_DEFAULTS = {
    "cost_2026": CSV_COST_2026,
    "cost_2025": CSV_COST_2025,
    "material": CSV_MATERIAL,
    "overhead": CSV_OVERHEAD,
    "budget": CSV_BUDGET,
    "labor": CSV_LABOR,
    "benchmark_2026": CSV_BENCH_2026,
    "benchmark_2025": CSV_BENCH_2025,
    "market": CSV_MARKET,
    "industry": CSV_INDUSTRY,
}

# 报告模板
TEMPLATE_DOCX = TEMPLATE_DIR / "月度成本分析报告模板.docx"
CUSTOM_TEMPLATE_PATH = TEMPLATE_UPLOAD_DIR / "active_template.docx"


def get_data_file(key: str) -> Path:
    """Return an imported override when one is configured, otherwise the contest source CSV."""
    default = DATA_FILE_DEFAULTS[key]
    # Prefer a published version from the resource registry. The legacy JSON
    # override remains as a backwards-compatible fallback for old deployments.
    try:
        from resources.manager import resource_manager
        published = resource_manager.active_path("data", key)
        if published is not None:
            return published
    except Exception:
        pass
    try:
        import json
        overrides = json.loads(DATA_SOURCE_CONFIG_PATH.read_text(encoding="utf-8"))
        path = Path(overrides.get(key, ""))
        if path.is_file() and path.resolve().is_relative_to(DATA_UPLOAD_DIR.resolve()):
            return path
    except (OSError, ValueError, json.JSONDecodeError):
        pass
    return default


def get_report_template_path() -> Path:
    """Use the managed template override only when it is a valid local file."""
    try:
        from resources.manager import resource_manager
        published = resource_manager.active_path("template", "default")
        if published is not None:
            return published
    except Exception:
        pass
    return CUSTOM_TEMPLATE_PATH if CUSTOM_TEMPLATE_PATH.is_file() else TEMPLATE_DOCX

# LLM配置：环境变量提供默认值，系统设置中的运行时覆盖文件优先级更高。
def _read_model_overrides() -> dict:
    try:
        import json
        data = json.loads(MODEL_CONFIG_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError, json.JSONDecodeError):
        return {}


_MODEL_OVERRIDES = _read_model_overrides()


def _model_value(name: str, default):
    value = _MODEL_OVERRIDES.get(name)
    return value if value is not None else default


def _model_bool(name: str, default: bool) -> bool:
    value = _model_value(name, default)
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() not in {"0", "false", "no", "off"}


DEEPSEEK_API_KEY = str(_model_value("DEEPSEEK_API_KEY", os.getenv("DEEPSEEK_API_KEY", "")))
DEEPSEEK_BASE_URL = str(_model_value("DEEPSEEK_BASE_URL", os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")))
DEEPSEEK_MODEL = str(_model_value("DEEPSEEK_MODEL", os.getenv("DEEPSEEK_MODEL", "deepseek-chat")))
DEEPSEEK_VERIFY_SSL = _model_bool("DEEPSEEK_VERIFY_SSL", os.getenv("DEEPSEEK_VERIFY_SSL", "true").strip().lower() not in {"0", "false", "no"})
DEEPSEEK_PROVIDER_LABEL = str(_model_value("DEEPSEEK_PROVIDER_LABEL", os.getenv("DEEPSEEK_PROVIDER_LABEL", "DeepSeek")))

MIMO_API_KEY = str(_model_value("MIMO_API_KEY", os.getenv("MIMO_API_KEY", "")))
MIMO_BASE_URL = str(_model_value("MIMO_BASE_URL", os.getenv("MIMO_BASE_URL", "https://api.xiaomimimo.com/v1")))
MIMO_MODEL = str(_model_value("MIMO_MODEL", os.getenv("MIMO_MODEL", "mimo-v2.5")))
MIMO_VERIFY_SSL = _model_bool("MIMO_VERIFY_SSL", os.getenv("MIMO_VERIFY_SSL", "true").strip().lower() not in {"0", "false", "no"})
MIMO_PROVIDER_LABEL = str(_model_value("MIMO_PROVIDER_LABEL", os.getenv("MIMO_PROVIDER_LABEL", "MiMo")))

# Embedding配置
EMBEDDING_PROVIDER = os.getenv("EMBEDDING_PROVIDER", "local")

# 向量数据库
CHROMA_DB_PATH = os.getenv("CHROMA_DB_PATH", str(APP_DIR / "chroma_db"))

# RPA服务
RPA_BASE_URL = os.getenv("RPA_BASE_URL", "http://127.0.0.1:8090")
RPA_TASK_DB_PATH = Path(os.getenv("RPA_TASK_DB_PATH", str(APP_DIR / "rpa_tasks.sqlite3")))

# 检索参数
RETRIEVAL_TOP_K = 5
BM25_WEIGHT = 0.3
VECTOR_WEIGHT = 0.7

# 文本切分参数
CHUNK_SIZE_RECIPE = 400
CHUNK_SIZE_PROCESS = 600
CHUNK_SIZE_EQUIPMENT = 300
CHUNK_SIZE_GMP = 500
CHUNK_OVERLAP = 50

# 波动告警阈值
ALERT_THRESHOLD = 10.0  # ±10%

# API安全
API_KEY = os.getenv("API_KEY", "")

# 产品列表
PRODUCTS = ["银黄口服液", "板蓝根颗粒", "六味地黄胶囊"]
MONTHS = ["2026-01", "2026-02", "2026-03", "2026-04", "2026-05", "2026-06"]

# 产品规格映射
PRODUCT_SPECS = {
    "银黄口服液": "10ml×10支/盒",
    "板蓝根颗粒": "10g×20袋/盒",
    "六味地黄胶囊": "0.3g×60粒/盒",
}
