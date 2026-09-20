"""
报告生成API路由

本模块实现了药品成本分析报告的生成、查询和下载功能。
学习要点：
  - FastAPI 路由 (APIRouter) 的定义和使用
  - Query 参数校验与默认值
  - 文件响应 (FileResponse) 的返回
  - 内存任务管理与持久化历史记录
  - 异常处理 (HTTPException, try/except)
  - UUID 唯一标识符的生成
  - Path 路径操作
  - logging 日志记录
"""

# ============================================================
# 一、标准库导入
# ============================================================

import json          # JSON 序列化/反序列化，用于读写历史记录文件
import logging       # Python 内置日志模块，用于记录运行信息和错误
import os
import statistics
import tempfile
import threading
import time          # 时间模块，用于获取时间戳、格式化时间
import uuid          # UUID 模块，用于生成唯一的任务 ID
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path  # Path 是 Python 推荐的路径操作方式，
                          # 比 os.path 更面向对象、更易读

# ============================================================
# 二、第三方库导入（FastAPI）
# ============================================================

from fastapi import APIRouter, HTTPException, Query
# APIRouter   : 路由分组器，把相关的接口组织在一起，
#               最后在 main.py 中通过 app.include_router() 挂载
# HTTPException: 抛出 HTTP 错误响应（如 404、422）
# Query        : 声明 URL 查询参数（?key=value 形式的参数）

from fastapi.responses import FileResponse
# FileResponse : 返回文件下载响应，浏览器会自动触发下载

# ============================================================
# 三、项目内部模块导入
# ============================================================

from security import validate_month, validate_product, validate_task_id
from report.task_store import ReportTaskStore
from resources.manager import resource_manager
# 从 security 模块导入输入校验函数
# 这些函数会对用户输入做安全检查（如防止注入攻击、非法字符等）

# ============================================================
# 四、模块级常量和全局变量
# ============================================================

logger = logging.getLogger("report")
# 创建一个名为 "report" 的日志记录器
# 在代码中用 logger.info() / logger.error() 记录信息
# 配合 logging 配置，可以输出到控制台或文件

router = APIRouter()
# 创建路由实例，所有 @router.get / @router.post 装饰器
# 都会把接口注册到这个路由上
# 最终在 main.py 中：app.include_router(router, prefix="/api/report")

_tasks: dict = {}
# 【内存任务表】用字典存储正在生成或刚生成完的任务
# key = task_id（字符串），value = 任务详情字典
# 注意：重启服务后内存中的数据会丢失，所以同时用文件做持久化
# 类型标注 dict 表示这个变量是字典类型（Python 3.6+ 类型提示）

_TASK_TTL = 3600 * 24 * 30
REPORT_HISTORY_MAX_RECORDS = 0  # 保留兼容名称；历史报告不再按条数自动删除。
# 【任务在内存中的存活时间】单位：秒
# 3600秒(1小时) × 24小时 × 30天 = 30天
# 超过这个时间的任务会从内存中自动清除，释放内存
# 这种机制叫 TTL（Time To Live，存活时间）

_HISTORY_PATH = Path(__file__).resolve().parent.parent / "output" / "report_history.json"

# SQLite is the source of truth for queued/running/completed tasks. The JSON
# history file remains readable for backwards compatibility with old exports.
_TASK_DB_PATH = Path(os.getenv(
    "REPORT_TASK_DB_PATH",
    str(Path(__file__).resolve().parent.parent / "report_tasks.sqlite3"),
))
_TASK_STORE = ReportTaskStore(_TASK_DB_PATH)
_REPORT_EXECUTOR = ThreadPoolExecutor(max_workers=max(1, int(os.getenv("REPORT_WORKERS", "2"))))
_REPORT_CANCELLED: set[str] = set()
_REPORT_LOCK = threading.RLock()
# 【历史记录文件路径】
# Path(__file__)           : 当前文件的路径（app/routers/report.py）
# .resolve()               : 转为绝对路径
# .parent                  : 上一级目录（app/routers/）
# .parent.parent           : 再上一级（app/）
# / "output" / "report_history.json" : 拼接子路径
# 最终结果: PharmaCost-AI/app/output/report_history.json
# Path 的 / 运算符是路径拼接，等价于 os.path.join()

OUTPUT_DIR = Path(__file__).resolve().parent.parent / "output"
# 【报告输出目录】生成的 .docx 和 .pdf 文件都存放在这里

OUTPUT_DIR.mkdir(exist_ok=True)
# 创建输出目录，如果已存在则不报错
# exist_ok=True 的作用：目录存在时不会抛 FileExistsError
# 没有这行的话，第二次运行就会报错


# ============================================================
# 五、历史记录管理函数
# ============================================================

def _load_history():
    """
    从 JSON 文件读取报告生成历史记录。

    返回值：列表，每个元素是一条报告记录字典。
    如果文件不存在或内容损坏，返回空列表 []。

    学习要点：
      - json.loads() 把 JSON 字符串转为 Python 对象
      - Path.read_text() 读取文件全部文本内容
      - isinstance() 检查变量类型
      - except 捕获多种异常
    """
    try:
        # 读取 JSON 文件内容并解析
        data = json.loads(_HISTORY_PATH.read_text(encoding="utf-8"))
        # 确保解析结果是列表（防止文件被篡改为其他 JSON 类型）
        return data if isinstance(data, list) else []
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        # FileNotFoundError : 文件不存在（首次运行时正常情况）
        # json.JSONDecodeError : JSON 格式损坏
        # OSError : 其他文件系统错误（如权限不足）
        return []


def _resolve_output_path(path):
    if not path:
        return None
    output_dir = OUTPUT_DIR.resolve()
    resolved = Path(path).resolve()
    try:
        resolved.relative_to(output_dir)
    except ValueError:
        return None
    return resolved


def _delete_task_files(task):
    for key in ("docx_path", "pdf_path"):
        path = _resolve_output_path(task.get(key))
        if path:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                logger.warning("报告文件删除失败: %s", path)


def _save_history(records):
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=_HISTORY_PATH.parent,
            prefix=f"{_HISTORY_PATH.name}.",
            suffix=".tmp",
            delete=False,
        ) as temp_file:
            json.dump(records, temp_file, ensure_ascii=False, indent=2)
            temp_path = Path(temp_file.name)
        os.replace(temp_path, _HISTORY_PATH)
    finally:
        if temp_path and temp_path.exists():
            temp_path.unlink()


# ============================================================
# 六、内存任务清理函数
# ============================================================

def _cleanup_tasks():
    """
    清理内存中过期的任务记录。

    遍历 _tasks 字典，找出创建时间超过 _TASK_TTL 的任务并删除。
    在每次生成新报告前调用，保持内存整洁。

    学习要点：
      - time.time() 获取当前时间戳（秒，浮点数）
      - 字典推导式 + 列表推导式
      - 字典的 items() 方法同时获取 key 和 value
    """
    now = time.time()  # 当前时间戳
    # 列表推导式：筛选出所有已过期任务的 key
    expired = [
        key
        for key, value in _tasks.items()
        if now - value.get("_created", 0) > _TASK_TTL
        # value.get("_created", 0) 安全取值，key 不存在时返回 0
        # 当前时间 - 创建时间 > 存活期限 → 已过期
    ]
    # 逐个删除过期任务
    for key in expired:
        del _tasks[key]


# ============================================================
# 七、参数校验函数
# ============================================================

def _validate_report_type(value: str) -> str:
    """
    校验报告类型参数。

    只接受三种类型：
      - "monthly"  : 月度报告
      - "quarterly": 季度报告
      - "topic"    : 专题报告

    学习要点：
      - 集合（set）用 {} 创建，查找效率 O(1)
      - raise HTTPException 抛出 HTTP 错误
      - status_code=422 表示"请求参数有误"
    """
    if value not in {"monthly", "quarterly", "topic"}:
        raise HTTPException(status_code=422, detail="报告类型不支持")
    return value  # 校验通过，原样返回


def _validate_output_format(value: str) -> str:
    """
    校验输出格式参数，只接受 "docx" 或 "pdf"。
    """
    if value not in {"docx", "pdf"}:
        raise HTTPException(status_code=422, detail="输出格式不支持")
    return value


# ============================================================
# 八、工具函数
# ============================================================

def _public_task(task: dict) -> dict:
    """
    过滤任务字典中的内部字段，只返回公开信息。

    内部字段以下划线 "_" 开头（如 "_created"），
    这些字段仅内部使用，不应暴露给前端。

    学习要点：
      - 字典推导式 {k: v for k, v in ... if 条件}
      - str.startswith() 判断字符串前缀
      - 这是一种常见的"数据脱敏"模式
    """
    return {
        key: value
        for key, value in task.items()
        if not key.startswith("_")  # 排除以下划线开头的内部字段
    }


def _now_text() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def _save_task(task: dict) -> None:
    task["updated_at"] = _now_text()
    _TASK_STORE.upsert(task)
    _tasks[task["task_id"]] = task


def _find_task(task_id: str) -> dict | None:
    task = _tasks.get(task_id)
    if task:
        return task
    task = _TASK_STORE.get(task_id)
    if task:
        _tasks[task_id] = task
    return task


def _estimate_report_seconds() -> int:
    """Estimate total report time from recent completed tasks."""
    durations = []
    try:
        for record in _TASK_STORE.list(limit=20, completed_only=True):
            value = float(record.get("duration_seconds", 0) or 0)
            if 5 <= value <= 900:
                durations.append(value)
    except Exception:
        logger.debug("读取报告历史耗时失败", exc_info=True)
    if not durations:
        return 60
    return max(15, min(900, round(statistics.median(durations))))


def _template_for_task(template_id: str | None) -> str | None:
    if not template_id:
        return None
    resource = resource_manager.get(template_id)
    if not resource or resource.get("resource_type") != "template":
        raise ValueError("报告模板不存在")
    if resource.get("status") != "active" or not Path(resource.get("path", "")).is_file():
        raise ValueError("报告模板当前不可用")
    return str(resource["path"])


def _run_report_task(task: dict) -> None:
    """Execute the existing engine off the event loop and persist every state."""
    task_id = task["task_id"]
    # Cancellation may arrive after the task is queued but before the worker
    # starts. Check before persisting a generating state so a cancelled task
    # cannot be resurrected in memory or SQLite.
    with _REPORT_LOCK:
        if task_id in _REPORT_CANCELLED:
            _REPORT_CANCELLED.discard(task_id)
            return
        task.update({
            "status": "generating", "progress": 50,
            "started_at": _now_text(), "_started_epoch": time.time(),
        })
        _save_task(task)
    try:
        from report.engine import ReportEngine
        from report.pdf_export import export_pdf

        safe_product = task["product"].replace("/", "_").replace("\\", "_")
        safe_month = task["month"].replace("/", "-")
        stem = f"成本分析报告_{safe_product}_{safe_month}_{task_id}"
        docx_path = _resolve_output_path(OUTPUT_DIR / f"{stem}.docx")
        pdf_path = _resolve_output_path(OUTPUT_DIR / f"{stem}.pdf")
        if not docx_path or not pdf_path:
            raise RuntimeError("报告输出路径无效")
        template_path = _template_for_task(task.get("template_id"))
        if template_path:
            preview = ReportEngine().generate(
                task["product"], task["month"], str(docx_path),
                report_type=task["report_type"], template_path=template_path,
            )
        else:
            # Preserve compatibility with lightweight test/demonstration
            # engines that implement the original three-argument contract.
            preview = ReportEngine().generate(
                task["product"], task["month"], str(docx_path),
                report_type=task["report_type"],
            )
        pdf_error = ""
        try:
            export_pdf(preview, str(pdf_path), str(docx_path))
        except Exception as error:
            # PDF is a secondary export. A missing Word/reportlab converter
            # must not discard a successfully generated DOCX and preview.
            pdf_error = str(error)
            logger.warning("PDF 导出不可用，保留 Word 报告: %s", error)
        with _REPORT_LOCK:
            if task_id in _REPORT_CANCELLED:
                _delete_task_files({"docx_path": str(docx_path), "pdf_path": str(pdf_path)})
                _REPORT_CANCELLED.discard(task_id)
                return
            task.update({
                "status": "completed", "progress": 100,
                "docx_path": str(docx_path), "pdf_path": str(pdf_path),
                "preview": {key: value for key, value in preview.items() if key != "placeholders"},
                "completed_at": _now_text(), "created_at": task.get("created_at") or _now_text(),
                "duration_seconds": round(time.time() - task.get("_created", time.time()), 1),
            })
            if not pdf_path.exists():
                task["pdf_path"] = ""
            if pdf_error:
                task["pdf_error"] = "PDF导出不可用，请安装 reportlab 或配置 Word"
            _save_task(task)
            # Keep the legacy JSON export synchronized for existing consumers.
            history = [item for item in _load_history() if item.get("task_id") != task_id]
            history.append(_public_task(task))
            _save_history(history)
        logger.info("报告生成成功: %s", task_id)
    except Exception:
        with _REPORT_LOCK:
            if task_id in _REPORT_CANCELLED:
                _REPORT_CANCELLED.discard(task_id)
                return
            logger.error("报告生成失败: %s", task_id, exc_info=True)
            task.update({"status": "failed", "progress": 100, "error": "报告生成失败,请稍后重试或联系管理员"})
            _save_task(task)


# ============================================================
# 九、API 接口定义
# ============================================================

# ---- 接口 1: 生成报告 ----

@router.post("/generate")
# @router.post 表示这是一个 POST 请求接口
# 完整路径: POST /api/report/generate（prefix 在 main.py 中配置）
# 用 POST 是因为"生成报告"是一个写操作（会创建文件）

async def generate_report(
    # Query(...) 表示这是 URL 查询参数
    # ... 表示必填（没有默认值）
    product: str = Query(..., description="产品名称"),
    # 例: ?product=银黄口服液

    month: str = Query(...),
    # 例: ?month=2026-06

    report_type: str = Query("monthly"),
    # 例: ?report_type=monthly
    # "monthly" 是默认值，不传时自动使用

    output_format: str = Query("docx"),
    # 例: ?output_format=pdf

    template_id: str | None = Query(None),
):
    """
    生成药品成本分析报告。

    请求示例:
      POST /api/report/generate?product=银黄口服液&month=2026-06

    返回示例:
      {
        "task_id": "a1b2c3d4e5f6",
        "status": "completed",
        "product": "银黄口服液",
        "download_url": "/api/report/a1b2c3d4e5f6/download",
        ...
      }

    学习要点：
      - async def 异步函数，FastAPI 会自动处理并发
      - Query 参数校验
      - try/except 异常处理
      - 函数内部延迟导入（避免循环导入）
    """

    # --- 步骤 1: 参数校验 ---
    product = validate_product(product)  # 校验产品名称（安全检查）
    month = validate_month(month)        # 校验月份格式（如 2026-06）
    report_type = _validate_report_type(report_type)
    output_format = _validate_output_format(output_format)
    if template_id:
        resource = resource_manager.get(template_id)
        if not resource or resource.get("resource_type") != "template":
            raise HTTPException(status_code=404, detail="报告模板不存在")
        if resource.get("status") != "active" or not Path(resource.get("path", "")).is_file():
            raise HTTPException(status_code=409, detail="报告模板当前不可用")
        tagged_type = str(resource.get("metadata", {}).get("report_type") or "")
        if tagged_type in {"monthly", "quarterly", "topic"}:
            report_type = tagged_type

    # Queue the expensive engine/export work on a bounded worker pool. The
    # legacy synchronous implementation below is retained for source-level
    # compatibility but is unreachable after this submission path.
    _cleanup_tasks()
    task_id = uuid.uuid4().hex[:12]
    now = _now_text()
    task = {
        "task_id": task_id, "status": "queued", "product": product, "month": month,
        "report_type": report_type, "output_format": output_format,
        "template_id": template_id,
        "created_at": now, "updated_at": now, "progress": 0,
        "estimate_seconds": _estimate_report_seconds(), "_created": time.time(),
    }
    _save_task(task)
    _REPORT_EXECUTOR.submit(_run_report_task, task)
    result = _public_task(task)
    result.update({
        "download_url": f"/api/report/{task_id}/download",
        "docx_download_url": f"/api/report/{task_id}/download?format=docx",
        "pdf_download_url": f"/api/report/{task_id}/download?format=pdf",
    })
    return result

    # --- 步骤 2: 清理过期任务 ---
    _cleanup_tasks()

    # --- 步骤 3: 创建任务记录 ---
    task_id = uuid.uuid4().hex[:12]
    # uuid.uuid4()  : 生成一个随机 UUID，如 "a1b2c3d4-e5f6-7890-..."
    # .hex          : 转为 32 位十六进制字符串（去掉横杠）
    # [:12]         : 取前 12 位作为短 ID，足够避免碰撞

    task = {
        "task_id": task_id,
        "status": "generating",      # 初始状态：正在生成
        "product": product,
        "month": month,
        "report_type": report_type,
        "output_format": output_format,
        "_created": time.time(),     # 下划线前缀 = 内部字段，不会返回给前端
    }
    _tasks[task_id] = task  # 存入内存字典

    # --- 步骤 4: 执行报告生成（核心业务逻辑） ---
    try:
        # 【延迟导入】在函数内部 import 而非文件顶部
        # 好处：避免模块间的循环导入问题
        #       并且只在真正需要时才加载这些模块
        from report.engine import ReportEngine
        from report.pdf_export import export_pdf

        engine = ReportEngine()  # 实例化报告生成引擎

        # --- 构建安全的文件名 ---
        safe_product = product.replace("/", "_").replace("\\", "_")
        # 把路径分隔符替换为下划线，防止目录遍历攻击
        # 例如: "银/黄" → "银_黄"

        safe_month = month.replace("/", "-")
        # 月份中的 "/" 换成 "-"，如 "2026/06" → "2026-06"

        stem = f"成本分析报告_{safe_product}_{safe_month}_{task_id}"
        # f-string 格式化：用花括号 {} 插入变量值
        # 结果示例: "成本分析报告_银黄口服液_2026-06_a1b2c3d4e5f6"

        docx_path = _resolve_output_path(OUTPUT_DIR / f"{stem}.docx")
        pdf_path = _resolve_output_path(OUTPUT_DIR / f"{stem}.pdf")
        if not docx_path or not pdf_path:
            raise HTTPException(status_code=500, detail="报告输出路径无效")
        # Path 对象可以用 / 拼接子路径

        # 生成 Word 文档，返回预览数据
        preview = engine.generate(product, month, str(docx_path), report_type=report_type)
        # str(docx_path) 因为 engine.generate 接收字符串路径

        # 根据预览数据生成 PDF 文件
        export_pdf(preview, str(pdf_path), str(docx_path))

        # --- 步骤 5: 更新任务状态为"已完成" ---
        task.update({
            "status": "completed",
            "docx_path": str(docx_path),   # Word 文件路径
            "pdf_path": str(pdf_path),     # PDF 文件路径
            "preview": {
                key: value
                for key, value in preview.items()
                if key != "placeholders"   # 排除占位符数据，减小响应体积
            },
            "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            # time.strftime 按格式化字符串输出当前时间
        })

        # --- 步骤 6: 持久化到历史记录文件 ---
        history = _load_history()
        history.append(_public_task(task))  # 只保存公开字段
        _save_history(history)
        # 这样即使服务重启，历史记录也不会丢失

        logger.info("报告生成成功: %s", task_id)
        # %s 是占位符，会被 task_id 替换

        # --- 步骤 7: 构建返回结果 ---
        result = _public_task(task)  # 过滤内部字段
        result.update({
            "download_url": f"/api/report/{task_id}/download",
            "docx_download_url": f"/api/report/{task_id}/download?format=docx",
            "pdf_download_url": f"/api/report/{task_id}/download?format=pdf",
            # 提供多种下载链接，前端可以直接使用
        })
        return result
        # FastAPI 会自动把字典转为 JSON 响应

    except Exception:
        # 捕获所有异常（兜底处理）
        # exc_info=True 会把完整的错误堆栈写入日志
        logger.error("报告生成失败: %s", task_id, exc_info=True)

        # 更新任务状态为"失败"
        task.update({
            "status": "failed",
            "error": "报告生成失败,请稍后重试或联系管理员"
        })
        # 返回失败信息（注意：这里返回 200 状态码，不是 500）
        return {"task_id": task_id, "status": "failed", "error": task["error"]}


# ---- 接口 2: 查询报告历史 ----

@router.get("/history")
# GET 请求，用于查询数据（不修改任何东西）

async def report_history(
    limit: int | None = Query(None, ge=1, le=100),
    page: int = Query(1, ge=1),
    page_size: int = Query(10, ge=5, le=100),
    # ge = greater or equal（最小值）
    # le = less or equal（最大值）
    # 即 limit 只能是 1~100 之间的整数，默认 20
):
    """
    返回最近生成成功的报告记录。

    请求示例:
      GET /api/report/history?limit=10

    学习要点：
      - 列表推导式 [x for x in list if 条件] 用于过滤
      - reversed() 反转列表（最新的排前面）
      - 切片 [-limit:] 取最后 N 条
    """
    # `limit` remains compatible with old callers and means first-page size.
    size = limit or page_size
    current_page = 1 if limit else page
    total = _TASK_STORE.count(completed_only=True)
    if total:
        total_pages = max(1, (total + size - 1) // size)
        current_page = min(current_page, total_pages)
        offset = (current_page - 1) * size
        records = _TASK_STORE.list(limit=size, offset=offset, completed_only=True)
    else:
        legacy = [record for record in reversed(_load_history()) if record.get("status") == "completed"]
        total = len(legacy)
        total_pages = max(1, (total + size - 1) // size)
        current_page = min(current_page, total_pages)
        offset = (current_page - 1) * size
        records = legacy[offset:offset + size]
    return {
        "items": records,
        "total": total,
        "page": current_page,
        "page_size": size,
        "total_pages": total_pages,
    }


# ---- 接口 3: 查询单个任务状态 ----

@router.get("/{task_id}/status")
# {task_id} 是路径参数，会被 FastAPI 自动提取
# 例: GET /api/report/a1b2c3d4e5f6/status

async def report_status(task_id: str):
    """
    查询报告任务的当前状态。

    查询顺序：先查内存 → 再查历史文件 → 都没有则返回 404。

    学习要点：
      - {task_id} 路径参数的使用
      - next(生成器表达式, 默认值) 查找第一个匹配项
      - HTTPException(404) 返回"未找到"错误
    """
    task_id = validate_task_id(task_id)  # 安全校验

    task = _find_task(task_id)
    if task:
        return _public_task(task)

    # 内存中没有，从历史文件中查找
    record = next(
        (item for item in _load_history() if item.get("task_id") == task_id),
        None  # 找不到时返回 None
    )
    if not record:
        raise HTTPException(status_code=404, detail="任务不存在")
    return record


@router.post("/{task_id}/cancel")
async def cancel_report(task_id: str):
    """Cancel a report task and remove its persisted cache/files."""
    task_id = validate_task_id(task_id)
    task = _find_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    with _REPORT_LOCK:
        previous_status = task.get("status")
        _REPORT_CANCELLED.add(task_id)
        task["status"] = "cancelled"
        _delete_task_files(task)
        _TASK_STORE.delete(task_id)
        _tasks.pop(task_id, None)
        history = [item for item in _load_history() if item.get("task_id") != task_id]
        _save_history(history)
        if previous_status in {"completed", "failed", "cancelled"}:
            # A completed/failed worker will not reach its cancellation check.
            _REPORT_CANCELLED.discard(task_id)
    return {"task_id": task_id, "status": "cancelled"}


@router.delete("/{task_id}")
async def delete_report(task_id: str):
    task_id = validate_task_id(task_id)
    task = _find_task(task_id)
    history = _load_history()
    removed_records = [
        record for record in history
        if isinstance(record, dict) and record.get("task_id") == task_id
    ]
    if task is None and not removed_records:
        raise HTTPException(status_code=404, detail="任务不存在")

    if task:
        _delete_task_files(task)
    for record in removed_records:
        _delete_task_files(record)
    if task:
        _TASK_STORE.delete(task_id)
        _tasks.pop(task_id, None)
    if removed_records:
        _save_history([
            record for record in history
            if not (isinstance(record, dict) and record.get("task_id") == task_id)
        ])
    return {"task_id": task_id, "status": "deleted"}


# ---- 接口 4: 下载报告文件 ----

@router.get("/{task_id}/download")
async def download_report(
    task_id: str,
    format: str = Query("docx")  # 默认下载 docx 格式
):
    """
    下载已完成任务对应的报告文件。

    请求示例:
      GET /api/report/a1b2c3d4e5f6/download?format=pdf

    学习要点：
      - FileResponse 返回文件下载（浏览器会弹出"保存"对话框）
      - media_type 设置 HTTP Content-Type 头
      - 409 Conflict 状态码表示"请求与当前资源状态冲突"
    """
    task_id = validate_task_id(task_id)
    format = _validate_output_format(format)

    # 从内存或历史记录中查找任务
    task = _find_task(task_id) or next(
        (item for item in _load_history() if item.get("task_id") == task_id), None)
    # _tasks.get(task_id) 找不到返回 None（不会报错）
    # or 后面的表达式在左边为 None/False 时才执行

    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")

    # 只有已完成的任务才能下载
    if task.get("status") != "completed":
        raise HTTPException(status_code=409, detail="报告尚未生成完成")
        # 409 = Conflict，表示"任务还在生成中，无法下载"

    # 根据请求的格式选择对应文件路径
    path = _resolve_output_path(task.get("pdf_path" if format == "pdf" else "docx_path"))

    # 检查文件是否真正存在于磁盘上
    if not path or not path.exists():
        raise HTTPException(status_code=404, detail="报告文件不存在")

    # 设置正确的 MIME 类型
    media_type = (
        "application/pdf"
        if format == "pdf"
        else "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        # 后者是 .docx 文件的标准 MIME 类型
    )

    return FileResponse(
        str(path),
        filename=path.name,
        media_type=media_type
    )
    # FileResponse 会自动：
    #   1. 读取文件内容
    #   2. 设置 Content-Disposition 头（触发浏览器下载）
    #   3. 流式传输（不会一次性加载到内存）
