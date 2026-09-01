"""RPA整改任务API路由"""
from fastapi import APIRouter, Query, HTTPException
from pydantic import BaseModel, Field
from typing import Any, Literal, Optional
from security import validate_product, validate_month, validate_task_id

router = APIRouter()

VALID_STATUSES = {"draft", "sent", "received", "confirmed", "in_progress", "completed", "overdue", "failed", "pending", "processing"}
STATUS_ALIASES = {"pending": "sent", "processing": "in_progress"}
VALID_PRIORITIES = {"high", "medium", "low"}


class SelectedTasksRequest(BaseModel):
    task_ids: list[str] = Field(min_length=1, max_length=20)


class TaskGenerationRequest(BaseModel):
    """由分析模块提交已完成的归因结论，用于生成任务草稿。"""
    product: str = Field(min_length=1, max_length=100)
    month: str = Field(min_length=7, max_length=7)
    analysis_scenario: Literal["dashboard_attribution", "benchmark_attribution"]
    attribution_conclusion: str = Field(min_length=1, max_length=50000)
    analysis_evidence: dict[str, Any] = Field(default_factory=dict)


class TaskDraftSaveRequest(BaseModel):
    tasks: list[dict[str, Any]] = Field(min_length=1, max_length=20)


class TaskDraftUpdateRequest(BaseModel):
    task_title: Optional[str] = Field(default=None, min_length=1, max_length=200)
    assignee: Optional[dict[str, str]] = None
    priority: Optional[Literal["high", "medium", "low"]] = None
    deadline: Optional[str] = Field(default=None, min_length=10, max_length=10)
    suggestion: Optional[str] = Field(default=None, max_length=2000)


@router.post("/generate")
async def generate_tasks(payload: TaskGenerationRequest):
    """根据分析页面已展示的归因结论生成临时任务，等待用户选择保存。"""
    product = validate_product(payload.product)
    month = validate_month(payload.month)
    from rpa.client import generate_task_drafts
    return await generate_task_drafts(
        product=product,
        month=month,
        analysis_scenario=payload.analysis_scenario,
        attribution_conclusion=payload.attribution_conclusion.strip(),
        analysis_evidence=payload.analysis_evidence,
    )


@router.post("/save-drafts")
async def save_task_drafts(payload: TaskDraftSaveRequest):
    """保存审阅弹窗中用户选中的任务草稿，不调用RPA。"""
    from rpa.client import save_selected_task_drafts
    return await save_selected_task_drafts(payload.tasks)


@router.post("/dispatch-selected")
async def dispatch_selected_tasks(payload: SelectedTasksRequest):
    """将用户勾选的待派发任务发送至RPA，不推送微信。"""
    from rpa.client import dispatch_selected_tasks
    return await dispatch_selected_tasks(payload.task_ids)


@router.post("/notify-selected")
async def notify_selected_tasks(payload: SelectedTasksRequest):
    """将已发送至RPA的任务推送微信。"""
    from rpa.client import notify_selected_tasks
    return await notify_selected_tasks(payload.task_ids)


@router.get("/tasks")
async def list_tasks(
    status: Optional[str] = None,
    priority: Optional[str] = None,
    product: Optional[str] = None,
    month: Optional[str] = None,
):
    """查询任务列表"""
    if status and status not in VALID_STATUSES:
        raise HTTPException(400, f"无效的状态值,可选: {', '.join(VALID_STATUSES)}")
    if priority and priority not in VALID_PRIORITIES:
        raise HTTPException(400, f"无效的优先级,可选: {', '.join(VALID_PRIORITIES)}")
    if product:
        product = validate_product(product)
    if month:
        month = validate_month(month)
    from rpa.client import list_rpa_tasks
    status = STATUS_ALIASES.get(status, status)
    return await list_rpa_tasks(status=status, priority=priority, product=product, month=month)


@router.get("/tasks/{task_id}")
async def get_task(task_id: str):
    """查询任务详情"""
    task_id = validate_task_id(task_id)
    from rpa.client import get_rpa_task
    return await get_rpa_task(task_id)


@router.delete("/tasks/{task_id}")
async def delete_task(task_id: str):
    """删除本地保存的任务及其闭环记录。"""
    task_id = validate_task_id(task_id)
    from rpa.client import delete_rpa_task
    try:
        return await delete_rpa_task(task_id)
    except KeyError:
        raise HTTPException(404, "任务不存在")
    except ValueError as exc:
        raise HTTPException(409, str(exc))


@router.patch("/tasks/{task_id}")
async def update_task_draft(task_id: str, payload: TaskDraftUpdateRequest):
    """更新待派发任务草稿的可编辑字段。"""
    task_id = validate_task_id(task_id)
    from rpa.client import update_rpa_task
    try:
        return await update_rpa_task(task_id, payload.model_dump(exclude_none=True))
    except KeyError:
        raise HTTPException(404, "任务不存在")
    except ValueError as exc:
        raise HTTPException(409, str(exc))


@router.get("/stats")
async def get_stats():
    """获取任务统计"""
    from rpa.client import get_rpa_stats
    return await get_rpa_stats()
