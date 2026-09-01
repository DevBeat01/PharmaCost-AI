"""RPA整改任务生成、选择派发和状态追踪客户端。"""
import asyncio
import httpx
import json
import logging
import re
import sys
import uuid
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import RPA_BASE_URL, RPA_TASK_DB_PATH
from rpa.task_store import TaskStore

logger = logging.getLogger("rpa.client")

ASSIGNEE_MAP = {
    "材料": {"name": "张伟", "department": "采购部", "role": "采购经理"},
    "人工": {"name": "李芳", "department": "生产部", "role": "生产主管"},
    "制造费用": {"name": "王强", "department": "设备部", "role": "设备经理"},
    "效率": {"name": "李芳", "department": "生产部", "role": "生产主管"},
    "通用": {"name": "陈明", "department": "财务部", "role": "成本会计"},
}
RPA_STATUSES = {"draft", "sent", "received", "confirmed", "in_progress", "completed", "overdue", "failed"}
SCENARIO_NAMES = {
    "dashboard_attribution": "成本看板归因分析",
    "benchmark_attribution": "成本对标归因分析",
}

# 任务草稿与派发状态持久化到 SQLite，应用重启后仍可继续处理。
TASK_STORE = TaskStore(RPA_TASK_DB_PATH)


def _unwrap_response(payload: dict) -> dict:
    if isinstance(payload, dict) and isinstance(payload.get("data"), dict):
        return payload["data"]
    return payload


def _deadline_for_month(month: str, days: int = 30) -> str:
    return (datetime.strptime(month + "-28", "%Y-%m-%d") + timedelta(days=days)).strftime("%Y-%m-%d")


def _determine_assignee(text: str) -> dict:
    for key, assignee in ASSIGNEE_MAP.items():
        if key in text:
            return assignee.copy()
    return ASSIGNEE_MAP["通用"].copy()


def _normalize_priority(value: str) -> str:
    aliases = {"高": "high", "中": "medium", "低": "low"}
    value = aliases.get(str(value).strip(), str(value).strip().lower())
    return value if value in {"high", "medium", "low"} else "medium"


async def _request(method: str, path: str, **kwargs) -> dict:
    last_error = None
    for attempt in range(3):
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                response = await client.request(method, f"{RPA_BASE_URL}{path}", **kwargs)
                response.raise_for_status()
                payload = response.json()
                if isinstance(payload, dict) and payload.get("code") not in (None, 200):
                    raise RuntimeError(payload.get("message") or f"RPA业务错误: {payload.get('code')}")
                return payload
        except (httpx.TimeoutException, httpx.NetworkError, httpx.HTTPStatusError, RuntimeError) as exc:
            last_error = exc
            if attempt < 2:
                await asyncio.sleep(0.25 * (2 ** attempt))
    raise last_error or RuntimeError("RPA请求失败")


async def send_rpa_task(task_data: dict) -> dict:
    return await _request("POST", "/api/rpa/tasks", json=task_data)


async def send_wechat_notify(recipient: str, department: str, message: str) -> dict:
    return _unwrap_response(await _request("POST", "/api/notify/wechat", json={
        "recipient": recipient,
        "department": department,
        "message": message,
    }))


def _parse_llm_tasks(raw_text: str) -> list[dict]:
    try:
        match = re.search(r"\[.*\]", str(raw_text or ""), re.DOTALL)
        items = json.loads(match.group()) if match else []
        result = []
        seen = set()
        for item in items:
            if not isinstance(item, dict) or not item.get("task_title"):
                continue
            title = re.sub(r"\s+", "", str(item["task_title"]))
            if title in seen:
                continue
            seen.add(title)
            result.append(item)
        return result[:3]
    except (json.JSONDecodeError, TypeError, AttributeError):
        return []


async def _generate_llm_tasks(
    product: str, month: str, scenario: str, conclusion: str, evidence: dict,
) -> list[dict]:
    from llm.client import llm_client
    from llm.prompts import TASK_GENERATION_PROMPT

    evidence_text = json.dumps(evidence or {}, ensure_ascii=False, default=str)
    prompt = TASK_GENERATION_PROMPT.format(
        context=f"分析场景：{scenario}\n归因结论：{conclusion}\n分析证据：{evidence_text}",
        product=product,
        month=month,
    )
    raw = await asyncio.to_thread(llm_client.chat, prompt, max_tokens=1200)
    return _parse_llm_tasks(raw)


def _fallback_tasks(product: str, month: str, scenario: str, conclusion: str, evidence: dict) -> list[dict]:
    """模型不可用时，依据当前分析页提交的结论和证据生成可追溯草稿。"""
    source_text = f"{conclusion} {json.dumps(evidence or {}, ensure_ascii=False, default=str)}"
    month_label = month.replace("-", "年") + "月"
    priority = "high" if any(word in source_text for word in ("异常", "超", "高于", "上涨", "差异")) else "medium"
    # 模型不可用时优先使用归因文本中的编号/项目建议，避免每次都返回相同标题。
    extracted = []
    for line in conclusion.splitlines():
        match = re.match(r"^\s*(?:[-*•]|\d+[.)、])\s*(.+)$", line)
        if match and len(match.group(1).strip()) >= 8:
            value = re.sub(r"[*_`]+", "", match.group(1)).strip()
            if value not in extracted:
                extracted.append(value)
    if extracted:
        tasks = []
        for suggestion in extracted[:3]:
            dimension = "材料" if any(k in suggestion for k in ("采购", "原材料", "供应商", "合同", "价格")) else ("人工" if any(k in suggestion for k in ("人工", "工时", "生产", "效率")) else "制造费用")
            tasks.append({
                "task_title": suggestion[:100],
                "assignee": _determine_assignee(dimension),
                "source": {"analysis_scenario": scenario, "attribution_conclusion": conclusion},
                "priority": priority,
                "deadline": _deadline_for_month(month),
                "suggestion": suggestion,
            })
        return tasks
    candidates = [
        (
            "材料", ("材料", "采购", "原材料", "供应商", "合同", "价格"),
            f"请核查{product}{month_label}原材料采购价格、合同调价条款及供应商报价",
            "复核采购价格、合同条款和供应商报价，形成材料成本整改措施。",
        ),
        (
            "人工", ("人工", "工时", "生产", "工艺", "效率", "单耗", "收率"),
            f"请复核{product}{month_label}生产工时、单耗及工艺执行情况",
            "核查生产工时、单耗和工艺参数，制定效率改善措施。",
        ),
        (
            "制造费用", ("制造费用", "设备", "能耗", "产量", "分摊"),
            f"请核查{product}{month_label}设备费用、能耗及产量分摊情况",
            "核查设备利用率、能耗和费用分摊，制定降本措施。",
        ),
    ]
    tasks = []
    for dimension, keywords, title, suggestion in candidates:
        if any(keyword in source_text for keyword in keywords):
            tasks.append({
                "task_title": title,
                "assignee": _determine_assignee(dimension),
                "source": {"analysis_scenario": scenario, "attribution_conclusion": conclusion},
                "priority": priority,
                "deadline": _deadline_for_month(month),
                "suggestion": suggestion,
            })
    return tasks[:3] or [{
        "task_title": f"请核查{product}{month_label}成本异常归因结论",
        "assignee": _determine_assignee("通用"),
        "source": {"analysis_scenario": scenario, "attribution_conclusion": conclusion},
        "priority": priority,
        "deadline": _deadline_for_month(month),
        "suggestion": "请依据归因结论复核相关成本数据并制定整改措施。",
    }]


def _benchmark_suggestion_tasks(product: str, month: str, scenario: str, conclusion: str, evidence: dict) -> list[dict]:
    """对标场景只使用已校验的结构化建议，禁止从长归因正文自由拆分任务。"""
    suggestions = evidence.get("improvement_suggestions", []) if isinstance(evidence, dict) else []
    tasks = []
    seen = set()
    for item in suggestions:
        if not isinstance(item, dict):
            continue
        suggestion = str(item.get("suggestion") or "").strip()
        normalized = re.sub(r"\s+", "", suggestion)
        if len(suggestion) < 8 or normalized in seen:
            continue
        seen.add(normalized)
        department = str(item.get("department") or "")
        dimension = str(item.get("source_dimension") or department or "成本差异")
        assignee_key = "材料" if "采购" in department else ("人工" if "生产" in department else "制造费用")
        title = re.sub(r"\s+", " ", suggestion).strip().rstrip("。；")
        if not re.match(r"^(请|核查|复核|建立|制定|落实|启动|开展|优化|共享|确认|加强)", title):
            title = f"请落实：{title}"
        tasks.append({
            "task_title": title[:100],
            "assignee": _determine_assignee(assignee_key),
            "source": {"analysis_scenario": scenario, "attribution_conclusion": conclusion},
            "priority": _normalize_priority(item.get("priority", "medium")),
            "deadline": item.get("deadline") or _deadline_for_month(month),
            "suggestion": suggestion,
        })
        if len(tasks) == 3:
            break
    return tasks


def _make_task_draft(
    product: str, month: str, item: dict, scenario: str, conclusion: str, evidence: dict | None = None,
) -> dict:
    source = item.get("source") if isinstance(item.get("source"), dict) else {}
    source_text = " ".join(str(value) for value in source.values())
    assignee = item.get("assignee") if isinstance(item.get("assignee"), dict) else {}
    if not assignee.get("department") or not assignee.get("role"):
        assignee = _determine_assignee(source_text or item.get("task_title", ""))
    else:
        assignee = {
            "name": assignee.get("name") or _determine_assignee(assignee["department"])["name"],
            "department": assignee["department"],
            "role": assignee["role"],
        }
    return {
        "task_id": f"TASK-{month}-{uuid.uuid4().hex[:10].upper()}",
        "task_title": item.get("task_title") or f"请检查{product}成本异常原因",
        "assignee": assignee,
        "source": {
            "analysis_scenario": source.get("analysis_scenario") or scenario,
            "attribution_conclusion": source.get("attribution_conclusion") or conclusion,
        },
        "priority": _normalize_priority(item.get("priority", "medium")),
        "deadline": item.get("deadline") or _deadline_for_month(month),
        "suggestion": item.get("suggestion", ""),
        "analysis_evidence": dict(evidence or {}),
        "rag_used": bool(evidence.get("rag_sources") or evidence.get("rag_used")) if isinstance(evidence, dict) else False,
        "rag_sources": list(evidence.get("rag_sources") or []) if isinstance(evidence, dict) else [],
        "product": product,
        "month": month,
        "status": "draft",
        "notification_status": "not_sent",
        "created_at": datetime.now().isoformat(),
    }


async def generate_task_drafts(
    product: str,
    month: str,
    analysis_scenario: str,
    attribution_conclusion: str,
    analysis_evidence: dict | None = None,
) -> dict:
    """根据分析页已展示的归因结论生成临时任务，不重新执行分析、不触发RPA。"""
    scenario = SCENARIO_NAMES[analysis_scenario]
    conclusion = attribution_conclusion.strip()
    evidence = dict(analysis_evidence or {})
    # 两类分析场景都优先调用 AI；结构化建议仅作为模型不可用时的备用来源。
    generated = []
    generation_source = "ai"
    if not generated:
        try:
            generated = await _generate_llm_tasks(product, month, scenario, conclusion, evidence)
        except Exception:
            logger.warning("LLM整改任务JSON生成失败，使用规则化兜底任务", exc_info=True)
            generated = []
    if not generated:
        if analysis_scenario == "benchmark_attribution":
            generated = _benchmark_suggestion_tasks(product, month, scenario, conclusion, evidence)
            if generated:
                generation_source = "analysis_suggestions"
        if not generated:
            generation_source = "fallback"
            generated = _fallback_tasks(product, month, scenario, conclusion, evidence)

    # 这里只生成临时草稿供分析页审阅，不写入持久化任务表。
    drafts = [_make_task_draft(product, month, item, scenario, conclusion, evidence) for item in generated[:3]]
    return {
        "product": product, "month": month, "tasks_generated": len(drafts),
        "generation_source": generation_source, "tasks": drafts,
    }


async def save_selected_task_drafts(tasks: list[dict]) -> dict:
    """仅保存用户在审阅弹窗中明确勾选的任务草稿。"""
    saved = []
    for item in tasks[:20]:
        if not isinstance(item, dict):
            continue
        product = str(item.get("product") or "").strip()
        month = str(item.get("month") or "").strip()
        source = item.get("source") if isinstance(item.get("source"), dict) else {}
        scenario = str(source.get("analysis_scenario") or "成本看板归因分析").strip()
        conclusion = str(source.get("attribution_conclusion") or "").strip()
        if not product or not month or not conclusion or not item.get("task_title"):
            continue
        draft = _make_task_draft(
            product, month, item, scenario, conclusion,
            item.get("analysis_evidence") if isinstance(item.get("analysis_evidence"), dict) else {},
        )
        saved.append(draft)
    TASK_STORE.upsert_many(saved)
    return {"tasks_saved": len(saved), "tasks": saved}


def _rpa_payload(task: dict) -> dict:
    return {
        "task_id": task["task_id"],
        "task_title": task["task_title"],
        "assignee": task["assignee"],
        "source": {
            "analysis_type": task["source"]["analysis_scenario"],
            "analysis_month": task["month"],
            "product": task["product"],
            "finding": task["source"]["attribution_conclusion"],
            "evidence": task.get("analysis_evidence", {}),
        },
        "priority": task["priority"],
        "deadline": task["deadline"],
        "suggestion": task["suggestion"],
        "notify_method": "wechat",
        "created_at": task["created_at"],
    }


async def dispatch_selected_tasks(task_ids: list[str]) -> dict:
    """仅将前端勾选的草稿任务发送到RPA，不发送微信。"""
    results = []
    for task_id in dict.fromkeys(task_ids):
        task = TASK_STORE.get(task_id)
        if not task:
            results.append({"task_id": task_id, "status": "failed", "error": "任务不存在"})
            continue
        if task["status"] != "draft":
            results.append({"task_id": task_id, "status": "failed", "error": "仅待派发任务可发送"})
            continue
        try:
            rpa_result = await send_rpa_task(_rpa_payload(task))
            task["status"] = "sent"
            task["notification_status"] = "not_sent"
            task["rpa_result"] = _unwrap_response(rpa_result)
            TASK_STORE.upsert(task)
            results.append({
                "task_id": task_id,
                "status": "sent",
                "rpa_status": "已发送至 RPA",
            })
        except Exception as exc:
            logger.exception("RPA任务派发失败")
            task["status"] = "failed"
            task["notification_status"] = "failed"
            TASK_STORE.upsert(task)
            results.append({"task_id": task_id, "status": "failed", "error": str(exc)})

    sent = sum(item["status"] == "sent" for item in results)
    return {"tasks_dispatched": sent, "tasks_failed": len(results) - sent, "results": results}


async def notify_selected_tasks(task_ids: list[str]) -> dict:
    """将已进入RPA且尚未通知的任务发送微信。"""
    results = []
    for task_id in dict.fromkeys(task_ids):
        task = TASK_STORE.get(task_id)
        if not task:
            results.append({"task_id": task_id, "status": "failed", "error": "任务不存在"})
            continue
        if task.get("status") == "draft":
            results.append({"task_id": task_id, "status": "failed", "error": "请先发送至RPA"})
            continue
        if task.get("notification_status") == "sent":
            results.append({"task_id": task_id, "status": "failed", "error": "微信已发送"})
            continue
        try:
            notification = await send_wechat_notify(
                task["assignee"]["name"], task["assignee"]["department"],
                f"【成本整改任务】\n任务：{task['task_title']}\n优先级：{task['priority']}\n"
                f"归因结论：{task['source']['attribution_conclusion']}\n截止：{task['deadline']}",
            )
            task["notification_status"] = "sent"
            task["notification"] = notification
            TASK_STORE.upsert(task)
            results.append({
                "task_id": task_id,
                "status": "sent",
                "notify_status": {"wechat": f"已发送至 {task['assignee']['name']}({task['assignee']['department']})"},
            })
        except Exception as exc:
            logger.exception("微信通知失败")
            task["notification_status"] = "failed"
            TASK_STORE.upsert(task)
            results.append({"task_id": task_id, "status": "failed", "error": str(exc)})

    sent = sum(item["status"] == "sent" for item in results)
    return {"notifications_sent": sent, "notifications_failed": len(results) - sent, "results": results}


async def _refresh_task_statuses() -> None:
    sent_tasks = [task for task in TASK_STORE.list() if task["status"] in RPA_STATUSES - {"draft", "failed"}]
    if not sent_tasks:
        return

    async def refresh_one(task: dict) -> None:
        try:
            # 状态刷新不能阻塞任务列表；RPA 服务不可用时保留本地状态。
            remote = _unwrap_response(await asyncio.wait_for(
                _request("GET", f"/api/rpa/tasks/{task['task_id']}"), timeout=3,
            ))
            task["status"] = remote.get("status", task["status"])
            TASK_STORE.upsert(task)
        except Exception:
            logger.debug("RPA状态刷新失败: %s", task["task_id"], exc_info=True)

    await asyncio.gather(*(refresh_one(task) for task in sent_tasks))


async def list_rpa_tasks(**filters) -> dict:
    # 列表优先读取本地 SQLite，避免远端逐条状态查询导致页面超时。
    # 派发、通知和单条详情仍会同步保存最新状态。
    tasks = TASK_STORE.list(**filters)
    return {"total": len(tasks), "tasks": tasks}


async def get_rpa_task(task_id: str) -> dict:
    await _refresh_task_statuses()
    return TASK_STORE.get(task_id) or _unwrap_response(await _request("GET", f"/api/rpa/tasks/{task_id}"))


async def get_rpa_stats() -> dict:
    tasks = TASK_STORE.list()
    counts = Counter(task["status"] for task in tasks)
    total = len(tasks)
    delivered_statuses = ("received", "confirmed", "in_progress", "completed", "overdue")
    confirmed_statuses = ("confirmed", "in_progress", "completed", "overdue")
    return {
        "total": total,
        "generated": total,
        "draft": counts["draft"],
        "dispatched": total - counts["draft"],
        "delivered": sum(counts[status] for status in delivered_statuses),
        "confirmed": sum(counts[status] for status in confirmed_statuses),
        "processing": counts["in_progress"],
        "completed": counts["completed"],
        "failed": counts["failed"],
        "by_status": dict(counts),
    }


async def delete_rpa_task(task_id: str) -> dict:
    """删除本地持久化的整改任务及其闭环记录。"""
    task = TASK_STORE.get(task_id)
    if not task:
        raise KeyError("任务不存在")
    TASK_STORE.delete(task_id)
    return {"task_id": task_id, "deleted": True, "status": task.get("status")}


async def update_rpa_task(task_id: str, updates: dict) -> dict:
    """更新待派发草稿，派发后的任务不可篡改。"""
    task = TASK_STORE.get(task_id)
    if not task:
        raise KeyError("任务不存在")
    if task.get("status") != "draft":
        raise ValueError("仅待派发任务可修改")

    if "task_title" in updates:
        task["task_title"] = str(updates["task_title"]).strip()
    if "assignee" in updates:
        assignee = updates["assignee"] if isinstance(updates["assignee"], dict) else {}
        department = str(assignee.get("department") or "").strip()
        role = str(assignee.get("role") or "").strip()
        if not department or not role:
            raise ValueError("责任部门和岗位不能为空")
        task["assignee"] = {
            "name": str(assignee.get("name") or task["assignee"].get("name") or "责任人").strip(),
            "department": department,
            "role": role,
        }
    if "priority" in updates:
        task["priority"] = _normalize_priority(updates["priority"])
    if "deadline" in updates:
        deadline = str(updates["deadline"]).strip()
        try:
            datetime.strptime(deadline, "%Y-%m-%d")
        except ValueError:
            raise ValueError("截止时间格式应为 YYYY-MM-DD")
        task["deadline"] = deadline
    if "suggestion" in updates:
        task["suggestion"] = str(updates["suggestion"]).strip()
    task["updated_at"] = datetime.now().isoformat()
    TASK_STORE.upsert(task)
    return task
