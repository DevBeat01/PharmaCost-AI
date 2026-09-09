"""Runtime settings for managed data, knowledge documents, and report templates."""
import json
import logging
import re
import threading
from datetime import datetime
from io import BytesIO
from pathlib import Path

import pandas as pd
from docx import Document
from fastapi import APIRouter, File, HTTPException, UploadFile
from pydantic import BaseModel

from config import (
    CUSTOM_TEMPLATE_PATH, DATA_FILE_DEFAULTS, DATA_SOURCE_CONFIG_PATH,
    DATA_UPLOAD_DIR, KNOWLEDGE_DIR, KNOWLEDGE_UPLOAD_DIR, TEMPLATE_DOCX,
    TEMPLATE_UPLOAD_DIR, MODEL_CONFIG_PATH, get_data_file, get_report_template_path,
    DEEPSEEK_API_KEY, DEEPSEEK_BASE_URL, DEEPSEEK_MODEL, DEEPSEEK_VERIFY_SSL, DEEPSEEK_PROVIDER_LABEL,
    MIMO_API_KEY, MIMO_BASE_URL, MIMO_MODEL, MIMO_VERIFY_SSL, MIMO_PROVIDER_LABEL,
)
from data.cost_data import cost_service

router = APIRouter()
logger = logging.getLogger("settings")

MAX_UPLOAD_BYTES = 25 * 1024 * 1024
DATA_FILE_LABELS = {
    "cost_2026": "本厂成本汇总（本年）", "cost_2025": "本厂成本汇总（上年）",
    "material": "原材料消耗明细", "overhead": "制造费用明细",
    "budget": "预算数据", "labor": "人工工时明细",
    "benchmark_2026": "对标工厂成本汇总（本年）",
    "benchmark_2025": "对标工厂成本汇总（上年）",
    "market": "行业市场价格行情", "industry": "行业成本基准",
}

class ModelConfigPayload(BaseModel):
    deepseek_provider_label: str | None = None
    deepseek_api_key: str | None = None
    deepseek_base_url: str | None = None
    deepseek_model: str | None = None
    deepseek_verify_ssl: bool | None = None
    mimo_provider_label: str | None = None
    mimo_api_key: str | None = None
    mimo_base_url: str | None = None
    mimo_model: str | None = None
    mimo_verify_ssl: bool | None = None


def _safe_filename(name: str | None) -> str:
    filename = Path(name or "").name
    cleaned = re.sub(r"[^0-9A-Za-z._\-\u4e00-\u9fff]", "_", filename)
    if not cleaned or cleaned in {".", ".."}:
        raise HTTPException(400, "文件名无效")
    return cleaned


async def _read_upload(file: UploadFile, suffixes: set[str]) -> tuple[bytes, str]:
    filename = _safe_filename(file.filename)
    if Path(filename).suffix.lower() not in suffixes:
        raise HTTPException(422, f"仅支持 {', '.join(sorted(suffixes))} 文件")
    content = await file.read(MAX_UPLOAD_BYTES + 1)
    if not content:
        raise HTTPException(422, "上传文件为空")
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, "文件不能超过 25MB")
    return content, filename


def _read_overrides() -> dict:
    try:
        data = json.loads(DATA_SOURCE_CONFIG_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _write_overrides(data: dict) -> None:
    DATA_SOURCE_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    temp = DATA_SOURCE_CONFIG_PATH.with_suffix(".tmp")
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(DATA_SOURCE_CONFIG_PATH)

def _read_model_overrides() -> dict:
    try:
        data = json.loads(MODEL_CONFIG_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}

def _write_model_overrides(data: dict) -> None:
    MODEL_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    temp = MODEL_CONFIG_PATH.with_suffix(".tmp")
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(MODEL_CONFIG_PATH)

def _mask_key(value: str) -> str:
    value = str(value or "")
    return (value[:4] + "*" * max(4, len(value) - 8) + value[-4:]) if len(value) > 8 else ("已配置" if value else "未配置")

def _model_summary() -> dict:
    return {
        "deepseek": {"provider_label": DEEPSEEK_PROVIDER_LABEL, "api_key": _mask_key(DEEPSEEK_API_KEY), "configured": bool(DEEPSEEK_API_KEY), "base_url": DEEPSEEK_BASE_URL, "model": DEEPSEEK_MODEL, "verify_ssl": DEEPSEEK_VERIFY_SSL},
        "mimo": {"provider_label": MIMO_PROVIDER_LABEL, "api_key": _mask_key(MIMO_API_KEY), "configured": bool(MIMO_API_KEY), "base_url": MIMO_BASE_URL, "model": MIMO_MODEL, "verify_ssl": MIMO_VERIFY_SSL},
    }


def _file_info(path: Path, *, deletable: bool, key: str | None = None, label: str | None = None) -> dict:
    stat = path.stat()
    return {
        "key": key, "label": label or path.name, "name": path.name,
        "size": stat.st_size, "updated_at": datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M"),
        "imported": deletable, "deletable": deletable,
    }


def _schedule_knowledge_build() -> None:
    def build():
        try:
            from rag.build_knowledge import build_knowledge_base
            build_knowledge_base()
        except Exception:
            logger.exception("设置变更后的知识库重建失败")
    threading.Thread(target=build, daemon=True, name="settings-knowledge-builder").start()


def _summary() -> dict:
    overrides = _read_overrides()
    data_files = []
    for key, default in DATA_FILE_DEFAULTS.items():
        current = get_data_file(key)
        imported = key in overrides and current != default
        data_files.append(_file_info(current, deletable=imported, key=key, label=DATA_FILE_LABELS[key]))

    knowledge_files = []
    for path in sorted(KNOWLEDGE_DIR.iterdir()):
        if path.is_file() and path.suffix.lower() in {".pdf", ".docx", ".txt"}:
            knowledge_files.append(_file_info(path, deletable=False))
    if KNOWLEDGE_UPLOAD_DIR.exists():
        for path in sorted(KNOWLEDGE_UPLOAD_DIR.iterdir()):
            if path.is_file() and path.suffix.lower() in {".pdf", ".docx", ".txt"}:
                knowledge_files.append(_file_info(path, deletable=True))

    template = get_report_template_path()
    try:
        from rag.vector_store import VectorStore
        rag = VectorStore.status()
    except Exception:
        rag = {"embedding_model_ready": False, "index_chunks": 0}
    return {
        "data_files": data_files,
        "knowledge_files": knowledge_files,
        "template": _file_info(template, deletable=template == CUSTOM_TEMPLATE_PATH, label="当前报告模板"),
        "system": {
            "data_loaded": cost_service._loaded,
            "rag": rag,
            "text_model": f"{DEEPSEEK_PROVIDER_LABEL}（主）/ {MIMO_PROVIDER_LABEL}（备用）",
        },
        "models": _model_summary(),
    }


@router.get("/summary")
async def settings_summary():
    return _summary()

@router.put("/models")
async def update_models(payload: ModelConfigPayload):
    values = payload.model_dump(exclude_unset=True) if hasattr(payload, "model_dump") else payload.dict(exclude_unset=True)
    mapping = {"deepseek_provider_label":"DEEPSEEK_PROVIDER_LABEL", "deepseek_api_key":"DEEPSEEK_API_KEY", "deepseek_base_url":"DEEPSEEK_BASE_URL", "deepseek_model":"DEEPSEEK_MODEL", "deepseek_verify_ssl":"DEEPSEEK_VERIFY_SSL", "mimo_provider_label":"MIMO_PROVIDER_LABEL", "mimo_api_key":"MIMO_API_KEY", "mimo_base_url":"MIMO_BASE_URL", "mimo_model":"MIMO_MODEL", "mimo_verify_ssl":"MIMO_VERIFY_SSL"}
    updates = {}
    for key, value in values.items():
        config_key = mapping[key]
        if key.endswith("_api_key") and value == "":
            continue
        if isinstance(value, str): value = value.strip()
        if config_key.endswith("_BASE_URL") and value and not str(value).startswith(("http://", "https://")):
            raise HTTPException(422, "接口地址必须以 http:// 或 https:// 开头")
        if config_key.endswith("_MODEL") and not value:
            raise HTTPException(422, "模型名称不能为空")
        updates[config_key] = value
    current = _read_model_overrides(); current.update(updates); _write_model_overrides(current)
    try:
        import importlib, config as config_module
        importlib.reload(config_module)
        for name in ('DEEPSEEK_API_KEY', 'DEEPSEEK_BASE_URL', 'DEEPSEEK_MODEL', 'DEEPSEEK_VERIFY_SSL', 'DEEPSEEK_PROVIDER_LABEL', 'MIMO_API_KEY', 'MIMO_BASE_URL', 'MIMO_MODEL', 'MIMO_VERIFY_SSL', 'MIMO_PROVIDER_LABEL'):
            globals()[name] = getattr(config_module, name)
        from llm.client import llm_client
        llm_client.reload()
    except Exception as exc:
        raise HTTPException(500, f"配置已保存，但模型客户端重载失败: {exc}") from exc
    return {"message": "模型配置已保存并生效", "summary": _summary()}

@router.delete("/models")
async def reset_models():
    MODEL_CONFIG_PATH.unlink(missing_ok=True)
    try:
        import importlib, config as config_module
        importlib.reload(config_module)
        for name in ('DEEPSEEK_API_KEY', 'DEEPSEEK_BASE_URL', 'DEEPSEEK_MODEL', 'DEEPSEEK_VERIFY_SSL', 'DEEPSEEK_PROVIDER_LABEL', 'MIMO_API_KEY', 'MIMO_BASE_URL', 'MIMO_MODEL', 'MIMO_VERIFY_SSL', 'MIMO_PROVIDER_LABEL'):
            globals()[name] = getattr(config_module, name)
        from llm.client import llm_client
        llm_client.reload()
    except Exception as exc:
        raise HTTPException(500, f"配置已恢复，但模型客户端重载失败: {exc}") from exc
    return {"message": "模型配置已恢复为环境变量设置", "summary": _summary()}


@router.post("/data/{key}")
async def upload_data_file(key: str, file: UploadFile = File(...)):
    if key not in DATA_FILE_DEFAULTS:
        raise HTTPException(404, "未知数据文件类型")
    content, _ = await _read_upload(file, {".csv"})
    try:
        uploaded_columns = set(pd.read_csv(BytesIO(content), encoding="utf-8-sig", nrows=0).columns.str.strip())
        expected_columns = set(pd.read_csv(DATA_FILE_DEFAULTS[key], encoding="utf-8-sig", nrows=0).columns.str.strip())
    except Exception as exc:
        raise HTTPException(422, f"CSV 无法读取: {exc}") from exc
    missing = expected_columns - uploaded_columns
    if missing:
        raise HTTPException(422, f"CSV 缺少必要字段: {', '.join(sorted(missing))}")

    DATA_UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    target = DATA_UPLOAD_DIR / f"{key}.csv"
    target.write_bytes(content)
    overrides = _read_overrides()
    overrides[key] = str(target)
    _write_overrides(overrides)
    try:
        cost_service.load_all()
    except Exception as exc:
        target.unlink(missing_ok=True)
        overrides.pop(key, None)
        _write_overrides(overrides)
        cost_service.load_all()
        raise HTTPException(422, f"数据重载失败，已恢复原文件: {exc}") from exc
    return {"message": f"{DATA_FILE_LABELS[key]}已导入并生效", "summary": _summary()}


@router.delete("/data/{key}")
async def delete_data_file(key: str):
    if key not in DATA_FILE_DEFAULTS:
        raise HTTPException(404, "未知数据文件类型")
    overrides = _read_overrides()
    path = Path(overrides.pop(key, ""))
    if not path.is_file():
        raise HTTPException(409, "当前使用默认数据，无法删除")
    path.unlink()
    _write_overrides(overrides)
    cost_service.load_all()
    return {"message": f"已恢复默认{DATA_FILE_LABELS[key]}", "summary": _summary()}


@router.post("/knowledge")
async def upload_knowledge_file(file: UploadFile = File(...)):
    content, filename = await _read_upload(file, {".pdf", ".docx", ".txt"})
    KNOWLEDGE_UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    target = KNOWLEDGE_UPLOAD_DIR / filename
    target.write_bytes(content)
    _schedule_knowledge_build()
    return {"message": "知识文档已导入，知识库正在后台更新", "summary": _summary()}


@router.delete("/knowledge/{filename}")
async def delete_knowledge_file(filename: str):
    target = (KNOWLEDGE_UPLOAD_DIR / _safe_filename(filename)).resolve()
    try:
        target.relative_to(KNOWLEDGE_UPLOAD_DIR.resolve())
    except ValueError as exc:
        raise HTTPException(400, "文件路径无效") from exc
    if not target.is_file():
        raise HTTPException(404, "仅可删除已导入的知识文档")
    target.unlink()
    _schedule_knowledge_build()
    return {"message": "知识文档已删除，知识库正在后台更新", "summary": _summary()}


@router.post("/template")
async def upload_template(file: UploadFile = File(...)):
    content, _ = await _read_upload(file, {".docx"})
    try:
        Document(BytesIO(content))
    except Exception as exc:
        raise HTTPException(422, f"报告模板不是有效的 Word 文件: {exc}") from exc
    TEMPLATE_UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    CUSTOM_TEMPLATE_PATH.write_bytes(content)
    return {"message": "报告模板已导入，后续生成报告将使用新模板", "summary": _summary()}


@router.delete("/template")
async def delete_template():
    if not CUSTOM_TEMPLATE_PATH.is_file():
        raise HTTPException(409, "当前使用默认报告模板，无法删除")
    CUSTOM_TEMPLATE_PATH.unlink()
    return {"message": "已恢复默认报告模板", "summary": _summary()}


@router.post("/reload-data")
async def reload_data():
    cost_service.load_all()
    return {"message": "成本数据已重新加载", "summary": _summary()}


@router.post("/rebuild-knowledge")
async def rebuild_knowledge():
    _schedule_knowledge_build()
    return {"message": "知识库重建任务已启动", "summary": _summary()}
