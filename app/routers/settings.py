"""Runtime settings for managed data, knowledge documents, and report templates."""
import json
import logging
import re
import shutil
import threading
import uuid
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path

import pandas as pd
from docx import Document
from fastapi import APIRouter, File, HTTPException, Query, UploadFile
from pydantic import BaseModel

from config import (
    CUSTOM_TEMPLATE_PATH, DATA_FILE_DEFAULTS, DATA_SOURCE_CONFIG_PATH,
    DATA_UPLOAD_DIR, KNOWLEDGE_DIR, KNOWLEDGE_UPLOAD_DIR, TEMPLATE_DOCX,
    TEMPLATE_UPLOAD_DIR, MODEL_CONFIG_PATH, SETTINGS_DIR, get_data_file, get_report_template_path,
    DEEPSEEK_API_KEY, DEEPSEEK_BASE_URL, DEEPSEEK_MODEL, DEEPSEEK_VERIFY_SSL, DEEPSEEK_PROVIDER_LABEL,
    DEEPSEEK_THINKING_ENABLED, DEEPSEEK_THINKING_CAPABILITY, DEEPSEEK_THINKING_HINT,
    MIMO_API_KEY, MIMO_BASE_URL, MIMO_MODEL, MIMO_VERIFY_SSL, MIMO_PROVIDER_LABEL,
    MIMO_THINKING_ENABLED, MIMO_THINKING_CAPABILITY, MIMO_THINKING_HINT,
)
from data.cost_data import cost_service
from resources.manager import resource_manager
from report.template_parser import TemplateParser

router = APIRouter()
logger = logging.getLogger("settings")

MAX_UPLOAD_BYTES = 25 * 1024 * 1024
KNOWLEDGE_BUILD_STATUS_PATH = SETTINGS_DIR / "knowledge_build_status.json"
_knowledge_build_lock = threading.RLock()
_knowledge_build_thread: threading.Thread | None = None
_knowledge_build_pending = False


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _read_knowledge_build_status() -> dict:
    try:
        status = json.loads(KNOWLEDGE_BUILD_STATUS_PATH.read_text(encoding="utf-8"))
        if isinstance(status, dict):
            # A daemon thread cannot survive a process restart. Do not leave the
            # UI showing "running" forever after an interrupted process.
            if status.get("status") in {"queued", "running"}:
                status.update({"status": "failed", "error": "上次知识库构建进程已中断，当前仍使用上一版索引"})
                status["finished_at"] = _utc_now()
                try:
                    KNOWLEDGE_BUILD_STATUS_PATH.write_text(json.dumps(status, ensure_ascii=False), encoding="utf-8")
                except OSError:
                    pass
            return status
    except (OSError, ValueError, json.JSONDecodeError):
        pass
    return {"task_id": None, "status": "idle", "pending": False, "error": None}


_knowledge_build_status = _read_knowledge_build_status()


def _write_knowledge_build_status(status: dict) -> None:
    KNOWLEDGE_BUILD_STATUS_PATH.parent.mkdir(parents=True, exist_ok=True)
    temp = KNOWLEDGE_BUILD_STATUS_PATH.with_suffix(".tmp")
    temp.write_text(json.dumps(status, ensure_ascii=False), encoding="utf-8")
    temp.replace(KNOWLEDGE_BUILD_STATUS_PATH)


def _knowledge_status_snapshot() -> dict:
    with _knowledge_build_lock:
        snapshot = dict(_knowledge_build_status)
        snapshot["pending"] = bool(_knowledge_build_pending)
        return snapshot


def _start_knowledge_build_locked(trigger: str) -> dict:
    """Create one coalesced background build task; caller holds the lock."""
    global _knowledge_build_thread, _knowledge_build_pending, _knowledge_build_status
    task = {
        "task_id": f"kb-{uuid.uuid4().hex[:12]}", "status": "queued",
        "requested_at": _utc_now(), "started_at": None, "finished_at": None,
        "document_count": 0, "chunk_count": 0, "error": None,
        "trigger": trigger, "pending": False,
    }
    _knowledge_build_status = task
    _knowledge_build_pending = False
    _write_knowledge_build_status(task)

    def worker(task_id: str):
        global _knowledge_build_thread, _knowledge_build_pending, _knowledge_build_status
        with _knowledge_build_lock:
            if _knowledge_build_status.get("task_id") == task_id:
                _knowledge_build_status.update({"status": "running", "started_at": _utc_now()})
                _write_knowledge_build_status(_knowledge_build_status)
        try:
            from rag.build_knowledge import build_knowledge_base, _file_signatures
            chunk_count = int(build_knowledge_base() or 0)
            document_count = len(_file_signatures())
            with _knowledge_build_lock:
                if _knowledge_build_status.get("task_id") == task_id:
                    _knowledge_build_status.update({"status": "completed", "finished_at": _utc_now(), "document_count": document_count, "chunk_count": chunk_count, "error": None})
                    _write_knowledge_build_status(_knowledge_build_status)
                rerun = _knowledge_build_pending
                _knowledge_build_pending = False
                _knowledge_build_thread = None
                if rerun:
                    _start_knowledge_build_locked("coalesced")
        except Exception as exc:
            logger.exception("知识库重构失败")
            with _knowledge_build_lock:
                if _knowledge_build_status.get("task_id") == task_id:
                    _knowledge_build_status.update({"status": "failed", "finished_at": _utc_now(), "error": str(exc)[:500]})
                    _write_knowledge_build_status(_knowledge_build_status)
                rerun = _knowledge_build_pending
                _knowledge_build_pending = False
                _knowledge_build_thread = None
                if rerun:
                    _start_knowledge_build_locked("coalesced")

    _knowledge_build_thread = threading.Thread(target=worker, args=(task["task_id"],), daemon=True, name="settings-knowledge-builder")
    _knowledge_build_thread.start()
    return dict(task)


def request_knowledge_build(trigger: str = "upload") -> dict:
    """Queue a coalesced build and return a durable status snapshot."""
    global _knowledge_build_pending
    with _knowledge_build_lock:
        if _knowledge_build_thread and _knowledge_build_thread.is_alive():
            _knowledge_build_pending = True
            _knowledge_build_status["pending"] = True
            _write_knowledge_build_status(_knowledge_build_status)
            return _knowledge_status_snapshot()
        return _start_knowledge_build_locked(trigger)
DATA_FILE_LABELS = {
    "cost_2026": "本厂成本汇总（本年）", "cost_2025": "本厂成本汇总（上年）",
    "material": "原材料消耗明细", "overhead": "制造费用明细",
    "budget": "预算数据", "labor": "人工工时明细",
    "benchmark_2026": "对标工厂成本汇总（本年）",
    "benchmark_2025": "对标工厂成本汇总（上年）",
    "market": "行业市场价格行情", "industry": "行业成本基准",
}
MARKET_REQUIRED_COLUMNS = {"药材名称"}
MARKET_PRICE_COLUMN_PATTERN = re.compile(r"^(?:[1-9]|1[0-2])月价格$")

class ModelConfigPayload(BaseModel):
    deepseek_provider_label: str | None = None
    deepseek_api_key: str | None = None
    deepseek_base_url: str | None = None
    deepseek_model: str | None = None
    deepseek_verify_ssl: bool | None = None
    deepseek_thinking_enabled: bool | None = None
    mimo_provider_label: str | None = None
    mimo_api_key: str | None = None
    mimo_base_url: str | None = None
    mimo_model: str | None = None
    mimo_verify_ssl: bool | None = None
    mimo_thinking_enabled: bool | None = None


def _validate_data_columns(key: str, uploaded_columns: set[str], expected_columns: set[str]) -> None:
    """Validate the minimum schema needed by each active data consumer."""
    required_columns = MARKET_REQUIRED_COLUMNS if key == "market" else expected_columns
    missing = required_columns - uploaded_columns
    if missing:
        raise HTTPException(422, f"CSV 缺少必要字段: {', '.join(sorted(missing))}")
    if key == "market" and not any(MARKET_PRICE_COLUMN_PATTERN.match(column) for column in uploaded_columns):
        raise HTTPException(422, "行业市场价格行情至少需要一列“1月价格”至“12月价格”")


def _merge_market_data(current: pd.DataFrame, uploaded: pd.DataFrame) -> pd.DataFrame:
    """Merge market periods horizontally so first- and second-half rows join."""
    key_columns = [column for column in ("药材名称", "规格等级", "单位") if column in current.columns and column in uploaded.columns]
    if not key_columns:
        return pd.concat([current, uploaded], ignore_index=True).drop_duplicates()
    combined = pd.concat([current, uploaded], ignore_index=True, sort=False)

    def latest_value(values):
        valid = values[values.notna()]
        return valid.iloc[-1] if not valid.empty else pd.NA

    return combined.groupby(key_columns, dropna=False, as_index=False, sort=False).agg(latest_value)


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


def _validate_knowledge_content(content: bytes, filename: str) -> dict:
    """Validate container integrity before a document enters the active RAG set."""
    suffix = Path(filename).suffix.lower()
    try:
        if suffix == ".docx":
            Document(BytesIO(content))
        elif suffix == ".pdf":
            import pymupdf as fitz
            document = fitz.open(stream=content, filetype="pdf")
            document.close()
        else:
            content.decode("utf-8-sig")
    except Exception as exc:
        raise HTTPException(422, f"知识文档无法解析: {exc}") from exc
    return {"valid": True, "format": suffix.lstrip("."), "bytes": len(content)}


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
    # Keep the status compact; exposing one asterisk per secret character
    # makes long provider keys overflow the settings dialog.
    return (value[:4] + "••••••••" + value[-4:]) if len(value) > 8 else ("已配置" if value else "未配置")

def _model_summary() -> dict:
    return {
        "deepseek": {"provider_label": DEEPSEEK_PROVIDER_LABEL, "api_key": _mask_key(DEEPSEEK_API_KEY), "configured": bool(DEEPSEEK_API_KEY), "base_url": DEEPSEEK_BASE_URL, "model": DEEPSEEK_MODEL, "verify_ssl": DEEPSEEK_VERIFY_SSL, "thinking_enabled": DEEPSEEK_THINKING_ENABLED, "thinking_capability": DEEPSEEK_THINKING_CAPABILITY, "thinking_hint": DEEPSEEK_THINKING_HINT},
        "mimo": {"provider_label": MIMO_PROVIDER_LABEL, "api_key": _mask_key(MIMO_API_KEY), "configured": bool(MIMO_API_KEY), "base_url": MIMO_BASE_URL, "model": MIMO_MODEL, "verify_ssl": MIMO_VERIFY_SSL, "thinking_enabled": MIMO_THINKING_ENABLED, "thinking_capability": MIMO_THINKING_CAPABILITY, "thinking_hint": MIMO_THINKING_HINT},
    }


def _file_info(path: Path, *, deletable: bool, key: str | None = None,
               label: str | None = None, resource: dict | None = None) -> dict:
    if path.is_file():
        stat = path.stat()
        size = stat.st_size
        updated_at = datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M")
    else:
        size = 0
        updated_at = ""
    info = {
        "key": key, "label": label or path.name,
        "name": _resource_filename(resource) if resource else path.name,
        "size": size, "updated_at": updated_at,
        "imported": deletable, "deletable": deletable,
    }
    if resource:
        info.update({
            "resource_id": resource["resource_id"],
            "version": resource["version"],
            "status": resource["status"],
            "sha256": resource["sha256"],
            "created_at": resource["created_at"],
            "published_at": resource.get("published_at"),
            "validation": resource.get("metadata", {}).get("validation", {}),
        })
    return info


def _resource_filename(resource: dict) -> str:
    return str(resource.get("metadata", {}).get("original_filename") or resource["filename"])


def _template_display_name(resource: dict) -> str:
    metadata = resource.get("metadata", {}) if resource else {}
    return str(metadata.get("display_name") or resource.get("filename") or "报告模板")


def _template_report_type(resource: dict) -> str:
    value = str((resource or {}).get("metadata", {}).get("report_type") or "monthly")
    return value if value in {"monthly", "quarterly", "topic"} else "monthly"


def _template_records() -> list[dict]:
    records = []
    for resource in resource_manager.list("template"):
        if resource.get("status") != "active":
            continue
        records.append({
            "resource_id": resource["resource_id"],
            "logical_key": resource["logical_key"],
            "name": _template_display_name(resource),
            "report_type": _template_report_type(resource),
            "version": resource["version"],
            "filename": resource["filename"],
            "status": resource["status"],
            "created_at": resource.get("created_at"),
            "published_at": resource.get("published_at"),
            "is_default": resource["logical_key"] == "default",
        })
    if not any(item["is_default"] for item in records):
        records.insert(0, {
            "resource_id": None,
            "logical_key": "default",
            "name": "竞赛默认模板",
            "report_type": "monthly",
            "version": None,
            "filename": TEMPLATE_DOCX.name,
            "status": "default",
            "created_at": None,
            "published_at": None,
            "is_default": True,
        })
    return records


def _sync_template_compat(resource: dict | None) -> None:
    """Keep the old mirror path available for older integrations."""
    TEMPLATE_UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    if resource:
        source = Path(resource["path"])
        temp = CUSTOM_TEMPLATE_PATH.with_suffix(".tmp")
        shutil.copyfile(source, temp)
        temp.replace(CUSTOM_TEMPLATE_PATH)
    else:
        CUSTOM_TEMPLATE_PATH.unlink(missing_ok=True)


def _sync_knowledge_compat() -> None:
    """Mirror active managed documents into the directory scanned by the RAG builder."""
    KNOWLEDGE_UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    records = resource_manager.list("knowledge")
    managed_names = {_resource_filename(record) for record in records}
    for path in KNOWLEDGE_UPLOAD_DIR.iterdir():
        if path.is_file() and path.name in managed_names:
            path.unlink(missing_ok=True)
    for record in records:
        if record["status"] != "active":
            continue
        target = KNOWLEDGE_UPLOAD_DIR / _resource_filename(record)
        temp = target.with_suffix(target.suffix + ".tmp")
        shutil.copyfile(record["path"], temp)
        temp.replace(target)


def _apply_data_resource(resource: dict | None) -> None:
    """Apply a managed data version and refresh the legacy override mirror."""
    if resource is None:
        return
    DATA_UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    target = DATA_UPLOAD_DIR / f"{resource['logical_key']}.csv"
    temp = target.with_suffix(".tmp")
    shutil.copyfile(resource["path"], temp)
    temp.replace(target)
    overrides = _read_overrides()
    overrides[resource["logical_key"]] = str(target)
    _write_overrides(overrides)
    cost_service.load_all()


def _clear_data_resource(key: str) -> None:
    overrides = _read_overrides()
    overrides.pop(key, None)
    _write_overrides(overrides)
    (DATA_UPLOAD_DIR / f"{key}.csv").unlink(missing_ok=True)
    cost_service.load_all()


def _publish_resource(resource_id: str, *, knowledge_trigger: str = "resource") -> dict:
    resource = resource_manager.get(resource_id)
    if not resource:
        raise HTTPException(404, "资源版本不存在")
    if resource["resource_type"] not in {"data", "knowledge", "template"}:
        raise HTTPException(422, "不支持发布该资源类型")
    previous = resource_manager.active_record(resource["resource_type"], resource["logical_key"])
    published = resource_manager.publish(resource_id)
    try:
        if published["resource_type"] == "data":
            _apply_data_resource(published)
        elif published["resource_type"] == "template":
            if published["logical_key"] == "default":
                _sync_template_compat(published)
        else:
            _sync_knowledge_compat()
            _schedule_knowledge_build(knowledge_trigger)
    except Exception as exc:
        resource_manager.archive(published["resource_id"])
        if previous:
            restored = resource_manager.publish(previous["resource_id"])
            if restored["resource_type"] == "data":
                _apply_data_resource(restored)
            elif restored["resource_type"] == "template" and restored["logical_key"] == "default":
                _sync_template_compat(restored)
            else:
                _sync_knowledge_compat()
        raise HTTPException(422, f"资源发布失败，已恢复上一版本: {exc}") from exc
    return published


def _schedule_knowledge_build(trigger: str = "resource") -> dict:
    """Compatibility wrapper for existing resource publication paths."""
    return request_knowledge_build(trigger)


def _summary() -> dict:
    overrides = _read_overrides()
    data_files = []
    for key, default in DATA_FILE_DEFAULTS.items():
        current = get_data_file(key)
        resource = resource_manager.active_record("data", key)
        imported = bool(resource) or (key in overrides and current != default)
        data_files.append(_file_info(current, deletable=imported, key=key,
                                     label=DATA_FILE_LABELS[key], resource=resource))

    knowledge_files = []
    if KNOWLEDGE_DIR.exists():
        for path in sorted(KNOWLEDGE_DIR.iterdir()):
            if path.is_file() and path.suffix.lower() in {".pdf", ".docx", ".txt"}:
                knowledge_files.append(_file_info(path, deletable=False))
    if KNOWLEDGE_UPLOAD_DIR.exists():
        for path in sorted(KNOWLEDGE_UPLOAD_DIR.iterdir()):
            if path.is_file() and path.suffix.lower() in {".pdf", ".docx", ".txt"}:
                resource = resource_manager.active_record("knowledge", path.name)
                knowledge_files.append(_file_info(path, deletable=True, resource=resource))

    template = get_report_template_path()
    template_resource = resource_manager.active_record("template", "default")
    try:
        from rag.vector_store import VectorStore
        rag = VectorStore.status()
    except Exception:
        rag = {"embedding_model_ready": False, "index_chunks": 0}
    rag["build"] = _knowledge_status_snapshot()
    return {
        "data_files": data_files,
        "knowledge_files": knowledge_files,
        "template": _file_info(template, deletable=bool(template_resource) or template == CUSTOM_TEMPLATE_PATH,
                                label="当前报告模板", resource=template_resource),
        "templates": _template_records(),
        "resources": resource_manager.summary(),
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
    mapping = {"deepseek_provider_label":"DEEPSEEK_PROVIDER_LABEL", "deepseek_api_key":"DEEPSEEK_API_KEY", "deepseek_base_url":"DEEPSEEK_BASE_URL", "deepseek_model":"DEEPSEEK_MODEL", "deepseek_verify_ssl":"DEEPSEEK_VERIFY_SSL", "deepseek_thinking_enabled":"DEEPSEEK_THINKING_ENABLED", "mimo_provider_label":"MIMO_PROVIDER_LABEL", "mimo_api_key":"MIMO_API_KEY", "mimo_base_url":"MIMO_BASE_URL", "mimo_model":"MIMO_MODEL", "mimo_verify_ssl":"MIMO_VERIFY_SSL", "mimo_thinking_enabled":"MIMO_THINKING_ENABLED"}
    updates = {}
    for key, value in values.items():
        config_key = mapping[key]
        if isinstance(value, str): value = value.strip()
        if key.endswith("_api_key") and not value:
            # An empty/whitespace key means "keep the current key" in the UI.
            continue
        if config_key.endswith("_BASE_URL") and value and not str(value).startswith(("http://", "https://")):
            raise HTTPException(422, "接口地址必须以 http:// 或 https:// 开头")
        if config_key.endswith("_MODEL") and not value:
            raise HTTPException(422, "模型名称不能为空")
        updates[config_key] = value
    previous_bytes = MODEL_CONFIG_PATH.read_bytes() if MODEL_CONFIG_PATH.is_file() else None
    current = _read_model_overrides(); current.update(updates); _write_model_overrides(current)
    try:
        import importlib, config as config_module
        importlib.reload(config_module)
        for name in ('DEEPSEEK_API_KEY', 'DEEPSEEK_BASE_URL', 'DEEPSEEK_MODEL', 'DEEPSEEK_VERIFY_SSL', 'DEEPSEEK_PROVIDER_LABEL', 'DEEPSEEK_THINKING_ENABLED', 'DEEPSEEK_THINKING_CAPABILITY', 'DEEPSEEK_THINKING_HINT', 'MIMO_API_KEY', 'MIMO_BASE_URL', 'MIMO_MODEL', 'MIMO_VERIFY_SSL', 'MIMO_PROVIDER_LABEL', 'MIMO_THINKING_ENABLED', 'MIMO_THINKING_CAPABILITY', 'MIMO_THINKING_HINT'):
            globals()[name] = getattr(config_module, name)
        from llm.client import llm_client
        llm_client.reload()
        # Detect capabilities against the newly loaded clients before reporting success.
        detected = {}
        for provider in ("deepseek", "mimo"):
            if any(item[0] == provider for item in llm_client._providers):
                detected[provider] = llm_client.probe_thinking_capability(provider)
        if detected:
            current = _read_model_overrides()
            for provider, result in detected.items():
                prefix = provider.upper()
                current[f"{prefix}_THINKING_CAPABILITY"] = result["capability"]
                current[f"{prefix}_THINKING_HINT"] = result["hint"]
                if result["capability"] != "configurable":
                    current[f"{prefix}_THINKING_ENABLED"] = result["capability"] == "always_on"
            _write_model_overrides(current)
            importlib.reload(config_module)
            for name in ('DEEPSEEK_THINKING_ENABLED', 'DEEPSEEK_THINKING_CAPABILITY', 'DEEPSEEK_THINKING_HINT', 'MIMO_THINKING_ENABLED', 'MIMO_THINKING_CAPABILITY', 'MIMO_THINKING_HINT'):
                globals()[name] = getattr(config_module, name)
            llm_client.reload()
        # A changed provider/model must not leave the dashboard serving an
        # attribution generated with the previous configuration.
        try:
            from analysis.dashboard import _ATTRIBUTION_CACHE
            _ATTRIBUTION_CACHE.clear()
        except Exception:
            pass
    except Exception as exc:
        try:
            if previous_bytes is None:
                MODEL_CONFIG_PATH.unlink(missing_ok=True)
            else:
                MODEL_CONFIG_PATH.write_bytes(previous_bytes)
            import importlib, config as config_module
            importlib.reload(config_module)
            for name in ('DEEPSEEK_API_KEY', 'DEEPSEEK_BASE_URL', 'DEEPSEEK_MODEL', 'DEEPSEEK_VERIFY_SSL', 'DEEPSEEK_PROVIDER_LABEL', 'DEEPSEEK_THINKING_ENABLED', 'DEEPSEEK_THINKING_CAPABILITY', 'DEEPSEEK_THINKING_HINT', 'MIMO_API_KEY', 'MIMO_BASE_URL', 'MIMO_MODEL', 'MIMO_VERIFY_SSL', 'MIMO_PROVIDER_LABEL', 'MIMO_THINKING_ENABLED', 'MIMO_THINKING_CAPABILITY', 'MIMO_THINKING_HINT'):
                globals()[name] = getattr(config_module, name)
            from llm.client import llm_client
            llm_client.reload()
        except Exception:
            logger.exception("模型配置探测失败后恢复旧配置失败")
        raise HTTPException(500, f"配置已保存，但模型客户端重载失败: {exc}") from exc
    return {"message": "模型配置已保存并生效", "summary": _summary()}

@router.delete("/models")
async def reset_models():
    MODEL_CONFIG_PATH.unlink(missing_ok=True)
    try:
        import importlib, config as config_module
        importlib.reload(config_module)
        for name in ('DEEPSEEK_API_KEY', 'DEEPSEEK_BASE_URL', 'DEEPSEEK_MODEL', 'DEEPSEEK_VERIFY_SSL', 'DEEPSEEK_PROVIDER_LABEL', 'DEEPSEEK_THINKING_ENABLED', 'DEEPSEEK_THINKING_CAPABILITY', 'DEEPSEEK_THINKING_HINT', 'MIMO_API_KEY', 'MIMO_BASE_URL', 'MIMO_MODEL', 'MIMO_VERIFY_SSL', 'MIMO_PROVIDER_LABEL', 'MIMO_THINKING_ENABLED', 'MIMO_THINKING_CAPABILITY', 'MIMO_THINKING_HINT'):
            globals()[name] = getattr(config_module, name)
        from llm.client import llm_client
        llm_client.reload()
        try:
            from analysis.dashboard import _ATTRIBUTION_CACHE
            _ATTRIBUTION_CACHE.clear()
        except Exception:
            pass
    except Exception as exc:
        raise HTTPException(500, f"配置已恢复，但模型客户端重载失败: {exc}") from exc
    return {"message": "模型配置已恢复为环境变量设置", "summary": _summary()}


@router.post("/data/{key}")
async def upload_data_file(
    key: str,
    file: UploadFile = File(...),
    mode: str = Query("replace", pattern="^(replace|append)$"),
):
    if key not in DATA_FILE_DEFAULTS:
        raise HTTPException(404, "未知数据文件类型")
    content, filename = await _read_upload(file, {".csv"})
    try:
        uploaded = pd.read_csv(BytesIO(content), encoding="utf-8-sig")
        uploaded.columns = uploaded.columns.str.strip()
        uploaded_columns = set(uploaded.columns)
        expected_columns = set(pd.read_csv(DATA_FILE_DEFAULTS[key], encoding="utf-8-sig", nrows=0).columns.str.strip())
    except Exception as exc:
        raise HTTPException(422, f"CSV 无法读取: {exc}") from exc
    # Market-price files are period-specific: a second-half file legitimately
    # carries 7月价格–12月价格 instead of the default 1月价格–6月价格.
    _validate_data_columns(key, uploaded_columns, expected_columns)
    if uploaded.empty:
        raise HTTPException(422, "CSV 不得为空")
    for required in ("产品名称", "月份"):
        if required in uploaded.columns and uploaded[required].isna().all():
            raise HTTPException(422, f"CSV 至少需要一条有效{required}数据")

    if mode == "append":
        current_path = get_data_file(key)
        try:
            current = pd.read_csv(current_path, encoding="utf-8-sig")
            current.columns = current.columns.str.strip()
            merged = _merge_market_data(current, uploaded) if key == "market" else pd.concat([current, uploaded], ignore_index=True).drop_duplicates()
            output = BytesIO()
            merged.to_csv(output, index=False)
            content = output.getvalue()
        except Exception as exc:
            raise HTTPException(422, f"无法合并当前数据文件: {exc}") from exc
        filename = f"{Path(filename).stem}_合并.csv"

    record = resource_manager.save_bytes(
        "data", key, filename, content,
        metadata={"original_filename": filename, "validation": {
            "valid": True, "columns": len(uploaded_columns), "missing_columns": [],
            "mode": mode,
        }},
    )
    try:
        published = _publish_resource(record["resource_id"])
    except Exception:
        # _publish_resource restores the previous active version on failure.
        raise
    return {"message": f"{DATA_FILE_LABELS[key]}已导入并生效（版本 v{published['version']}）", "summary": _summary()}


@router.delete("/data/{key}")
async def delete_data_file(key: str):
    if key not in DATA_FILE_DEFAULTS:
        raise HTTPException(404, "未知数据文件类型")
    active = resource_manager.active_record("data", key)
    if active:
        resource_manager.archive(active["resource_id"])
        try:
            _clear_data_resource(key)
        except Exception as exc:
            resource_manager.publish(active["resource_id"])
            _apply_data_resource(active)
            raise HTTPException(422, f"恢复默认数据失败: {exc}") from exc
    else:
        overrides = _read_overrides()
        path = Path(overrides.pop(key, ""))
        if not path.is_file():
            raise HTTPException(409, "当前使用默认数据，无法删除")
        path.unlink(missing_ok=True)
        _write_overrides(overrides)
        cost_service.load_all()
    return {"message": f"已恢复默认{DATA_FILE_LABELS[key]}", "summary": _summary()}


@router.post("/knowledge")
async def upload_knowledge_file(file: UploadFile = File(...)):
    content, filename = await _read_upload(file, {".pdf", ".docx", ".txt"})
    validation = _validate_knowledge_content(content, filename)
    record = resource_manager.save_bytes(
        "knowledge", filename, filename, content,
        metadata={"original_filename": filename, "validation": validation},
    )
    published = _publish_resource(record["resource_id"], knowledge_trigger="upload")
    build = _knowledge_status_snapshot()
    return {"message": f"知识文档已导入（版本 v{published['version']}），知识库正在更新", "knowledge_build": build, "summary": _summary()}


@router.delete("/knowledge/{filename}")
async def delete_knowledge_file(filename: str):
    safe_name = _safe_filename(filename)
    active = resource_manager.active_record("knowledge", safe_name)
    if active:
        resource_manager.archive(active["resource_id"])
        _sync_knowledge_compat()
        build = _schedule_knowledge_build("delete")
        return {"message": "知识文档已删除，知识库正在更新", "knowledge_build": build, "summary": _summary()}
    target = (KNOWLEDGE_UPLOAD_DIR / safe_name).resolve()
    try:
        target.relative_to(KNOWLEDGE_UPLOAD_DIR.resolve())
    except ValueError as exc:
        raise HTTPException(400, "文件路径无效") from exc
    if not target.is_file():
        raise HTTPException(404, "仅可删除已导入的知识文档")
    target.unlink()
    build = _schedule_knowledge_build("delete")
    return {"message": "知识文档已删除，知识库正在更新", "knowledge_build": build, "summary": _summary()}


@router.post("/template")
async def upload_template(
    file: UploadFile = File(...),
    mode: str = Query("replace", pattern="^(replace|add)$"),
    template_id: str | None = Query(None),
    display_name: str | None = Query(None, min_length=1, max_length=120),
    report_type: str = Query("monthly", pattern="^(monthly|quarterly|topic)$"),
):
    content, filename = await _read_upload(file, {".docx"})
    try:
        document = Document(BytesIO(content))
        placeholder_count = len(TemplateParser.PLACEHOLDER_RE.findall("\n".join(
            [p.text for p in document.paragraphs] +
            [cell.text for table in document.tables for row in table.rows for cell in row.cells]
        )))
    except Exception as exc:
        raise HTTPException(422, f"报告模板不是有效的 Word 文件: {exc}") from exc
    logical_key = "default"
    if mode == "add":
        logical_key = f"template-{__import__('uuid').uuid4().hex[:12]}"
    elif template_id:
        target = resource_manager.get(template_id)
        if not target or target.get("resource_type") != "template":
            raise HTTPException(404, "目标报告模板不存在")
        logical_key = target["logical_key"]
        if target.get("status") != "active":
            raise HTTPException(409, "只能替换当前生效的报告模板")
        display_name = display_name or _template_display_name(target)
        report_type = _template_report_type(target)
    metadata = {"original_filename": filename, "display_name": display_name or ("默认模板" if logical_key == "default" else Path(filename).stem), "report_type": report_type, "validation": {
        "valid": True, "placeholder_count": placeholder_count
    }}
    record = resource_manager.save_bytes(
        "template", logical_key, filename, content,
        metadata=metadata,
    )
    published = _publish_resource(record["resource_id"])
    return {"message": f"报告模板已导入并生效（版本 v{published['version']}）", "summary": _summary()}


@router.delete("/template")
async def delete_template():
    active = resource_manager.active_record("template", "default")
    if active:
        resource_manager.archive(active["resource_id"])
        _sync_template_compat(None)
        return {"message": "已恢复默认报告模板", "summary": _summary()}
    if not CUSTOM_TEMPLATE_PATH.is_file():
        raise HTTPException(409, "当前使用默认报告模板，无法删除")
    CUSTOM_TEMPLATE_PATH.unlink()
    return {"message": "已恢复默认报告模板", "summary": _summary()}


@router.delete("/template/{resource_id}")
async def delete_custom_template(resource_id: str):
    resource = resource_manager.get(resource_id)
    if not resource or resource.get("resource_type") != "template":
        raise HTTPException(404, "报告模板不存在")
    if resource.get("logical_key") == "default":
        return await delete_template()
    if resource.get("status") != "active":
        raise HTTPException(409, "报告模板当前不可删除")
    resource_manager.archive(resource_id)
    return {"message": "报告模板已删除", "summary": _summary()}


@router.post("/reload-data")
async def reload_data():
    cost_service.load_all()
    return {"message": "成本数据已重新加载", "summary": _summary()}


@router.post("/rebuild-knowledge")
async def rebuild_knowledge(trigger: str = Query("retry", pattern="^(retry|manual)$")):
    build = _schedule_knowledge_build(trigger)
    return {"message": "知识库重建任务已启动", "knowledge_build": build, "summary": _summary()}


@router.get("/knowledge/status")
async def knowledge_build_status():
    try:
        from rag.vector_store import VectorStore
        index = VectorStore.status()
    except Exception:
        index = {"embedding_model_ready": False, "index_chunks": 0}
    return {"knowledge_build": _knowledge_status_snapshot(), "index": index}


@router.get("/resources")
async def list_resources(resource_type: str | None = None, logical_key: str | None = None):
    return {"resources": resource_manager.list(resource_type, logical_key)}


@router.get("/resources/{resource_id}")
async def get_resource(resource_id: str):
    resource = resource_manager.get(resource_id)
    if not resource:
        raise HTTPException(404, "资源版本不存在")
    return resource


@router.post("/resources/{resource_id}/publish")
async def publish_resource(resource_id: str):
    resource = _publish_resource(resource_id)
    return {"message": f"资源已发布（版本 v{resource['version']}）", "resource": resource, "summary": _summary()}


@router.post("/resources/{resource_id}/rollback")
async def rollback_resource(resource_id: str):
    resource = _publish_resource(resource_id)
    return {"message": f"已回滚至版本 v{resource['version']}", "resource": resource, "summary": _summary()}


@router.delete("/resources/{resource_id}")
async def delete_resource(resource_id: str):
    resource = resource_manager.get(resource_id)
    if not resource:
        raise HTTPException(404, "资源版本不存在")
    if resource["status"] == "active":
        raise HTTPException(409, "当前版本正在使用，请通过对应设置接口恢复默认后再删除")
    if not resource_manager.discard(resource_id):
        raise HTTPException(409, "资源版本当前不可删除")
    return {"message": "资源历史版本已删除", "summary": _summary()}
