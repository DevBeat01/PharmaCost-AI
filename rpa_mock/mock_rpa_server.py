"""Competition-compatible mock RPA service for local and Docker deployment."""
import threading
import time
from datetime import datetime
from typing import Optional

import uvicorn
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

app = FastAPI(title="模拟RPA整改任务服务", version="1.0.0")
tasks_db: dict[str, dict] = {}
notifications_db: list[dict] = []


class Assignee(BaseModel):
    name: str
    department: str
    role: Optional[str] = None


class Source(BaseModel):
    analysis_type: str
    analysis_month: str
    product: str
    finding: str


class TaskCreateRequest(BaseModel):
    task_id: str
    task_title: str
    assignee: Assignee
    source: Source
    priority: str
    deadline: str
    suggestion: Optional[str] = None
    notify_method: Optional[str] = "wechat"
    created_at: str


class WechatNotifyRequest(BaseModel):
    recipient: str
    department: str
    message: str


def auto_advance_status(task_id: str) -> None:
    for delay, status in ((30, "received"), (10, "confirmed"), (5, "in_progress"), (5, "completed")):
        time.sleep(delay)
        task = tasks_db.get(task_id)
        if task is None:
            return
        task["status"] = status
        task["status_history"].append({"status": status, "time": datetime.now().isoformat()})


@app.get("/health")
def health_check():
    return {"status": "ok", "tasks_count": len(tasks_db), "timestamp": datetime.now().isoformat()}


@app.post("/api/rpa/tasks")
def create_task(req: TaskCreateRequest):
    if req.priority not in ("high", "medium", "low"):
        raise HTTPException(400, "priority必须为 high/medium/low")
    if req.task_id in tasks_db:
        raise HTTPException(400, f"任务ID {req.task_id} 已存在")

    now = datetime.now().isoformat()
    task = {
        "task_id": req.task_id,
        "task_title": req.task_title,
        "assignee": req.assignee.model_dump(),
        "source": req.source.model_dump(),
        "priority": req.priority,
        "deadline": req.deadline,
        "suggestion": req.suggestion,
        "notify_method": req.notify_method,
        "created_at": req.created_at,
        "status": "sent",
        "status_history": [{"status": "sent", "time": now}],
        "progress": "",
    }
    tasks_db[req.task_id] = task
    threading.Thread(target=auto_advance_status, args=(req.task_id,), daemon=True).start()
    return {
        "code": 200,
        "message": "任务创建成功，等待业务系统推送消息",
        "data": {
            "task_id": req.task_id,
            "status": "sent",
            "notify_status": None,
            "tracking_url": f"http://localhost:8090/api/rpa/tasks/{req.task_id}",
        },
    }


@app.get("/api/rpa/tasks/{task_id}")
def get_task(task_id: str):
    task = tasks_db.get(task_id)
    if task is None:
        raise HTTPException(404, f"任务 {task_id} 不存在")
    return {"code": 200, "message": "查询成功", "data": task}


@app.get("/api/rpa/tasks")
def list_tasks(
    status: Optional[str] = None,
    priority: Optional[str] = None,
    product: Optional[str] = None,
    month: Optional[str] = None,
    page: int = 1,
    page_size: int = 20,
):
    tasks = list(tasks_db.values())
    if status:
        tasks = [task for task in tasks if task["status"] == status]
    if priority:
        tasks = [task for task in tasks if task["priority"] == priority]
    if product:
        tasks = [task for task in tasks if task["source"]["product"] == product]
    if month:
        tasks = [task for task in tasks if task["source"]["analysis_month"] == month]
    tasks.sort(key=lambda task: task["created_at"], reverse=True)
    page_tasks = tasks[(page - 1) * page_size : page * page_size]
    simple_tasks = [
        {
            "task_id": task["task_id"], "task_title": task["task_title"],
            "priority": task["priority"], "status": task["status"],
            "deadline": task["deadline"],
            "assignee": f"{task['assignee']['name']}({task['assignee']['department']})",
            "product": task["source"]["product"], "month": task["source"]["analysis_month"],
            "created_at": task["created_at"],
        }
        for task in page_tasks
    ]
    return {"code": 200, "message": "查询成功", "data": {"total": len(tasks), "page": page, "page_size": page_size, "tasks": simple_tasks}}


@app.post("/api/notify/wechat")
def send_wechat(req: WechatNotifyRequest):
    now = datetime.now().isoformat()
    message_id = f"WX-{datetime.now().strftime('%Y%m%d')}-{len(notifications_db) + 1:03d}"
    notifications_db.append({"message_id": message_id, "recipient": req.recipient, "department": req.department, "message": req.message, "sent_at": now})
    return {"code": 200, "message": "微信消息发送成功", "data": {"recipient": req.recipient, "message_id": message_id, "sent_at": now, "status": "delivered"}}


@app.get("/api/stats")
def get_stats():
    by_status: dict[str, int] = {}
    by_priority: dict[str, int] = {}
    for task in tasks_db.values():
        by_status[task["status"]] = by_status.get(task["status"], 0) + 1
        by_priority[task["priority"]] = by_priority.get(task["priority"], 0) + 1
    return {"code": 200, "data": {"total_tasks": len(tasks_db), "total_notifications": len(notifications_db), "by_status": by_status, "by_priority": by_priority}}


@app.delete("/api/admin/reset")
def reset_all():
    tasks_db.clear()
    notifications_db.clear()
    return {"code": 200, "message": "所有数据已重置"}


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8090, log_level="info")
